import inspect
import logging
import sys
import time
from typing import Dict, List, TYPE_CHECKING
from uuid import uuid4

from darl.error_handling import ErrorSentinel, ProviderException
from darl.graph_build.sequential import SequentialBuilder
from darl.graph_caching import cache_dep_indexes, pull_graph_from_cache_using_dep_indexes
from darl.service_caller import __shared__getitem__
from darl.provider import get_default_service_name
from darl.special_providers import ShockProvider, ConstantProvider, PinnedProvider
from darl.call_key import CallKeyCreator, CallKey
from darl.cache import Cache, DictCache, CacheEntryGraph
from darl.graph import trim_graph
from darl.execution.sequential import SequentialRunner
from darl.service_mapping import ServiceMapping
from darl.trace import Trace
from darl.typing import enforce_types

if TYPE_CHECKING:
    from darl.graph_build.base import Builder
    from darl.execution.base import Runner


LOGGER = logging.getLogger(__name__)


class TypePassThrough:
    def __init__(self, ngn):
        self.ngn = ngn

    def __getitem__(self, item):
        # don't do any type checking logic for now
        return self.ngn

class Engine:
    def __init__(
            self,
            cache: Cache = None,
            builder: 'Builder' = None,
            runner: 'Runner' = None,
            enforce_types: bool = False,
            allow_edge_dependencies: bool = False,
            allow_anon_services: bool = False,
    ):
        self.cache = cache if cache is not None else DictCache()
        self.builder = builder if builder is not None else SequentialBuilder()
        self.runner = runner if runner is not None else SequentialRunner()
        self._enforce_types = enforce_types
        self._allow_edge_dependencies = allow_edge_dependencies
        self._allow_anon_services = allow_anon_services

        self.service_mapping = ServiceMapping()
        self.executed_graph_build_ids = []
        # this value_hash_cache is used to ensure value hashed provider results are consistent within
        # a single instantiated engine
        # TODO: make sure this works consistently in distributed/parallel graph builds
        # TODO: potentially can get rid of value hash cache once graph caching works with value hashed providers
        self.value_hash_cache = {}

        self.engine_id = str(uuid4())  # should this be reassigned on ngn configuration changes?
        self._catch_errors = False

    # define set/getstate so that getattr doesn't cause issues when pickling
    def __getstate__(self):
        return self.__dict__

    def __setstate__(self, state):
        self.__dict__.update(state)

    __getitem__ = __getattr__ = __shared__getitem__

    def _run_call_internal(self, call_key, starting_call_path=(), no_value_hash=False, graph_caching=True):
        """
        this is only considered private from outside this library, internally to this library this can
        and should be used. only done this way to not expose starting_call_path and no_value_hash
        to public api

        TODO: should we make it so that inline calls don't go through top level engine here?
        """
        trimmed_graph, full_graph = self._compile(
            call_key,
            error_catched=self._catch_errors,
            starting_call_path=starting_call_path,
            no_value_hash=no_value_hash,
            graph_caching=graph_caching,
        )

        # TODO: don't record/cache for inlined calls which will go through this call
        self.executed_graph_build_ids.append(trimmed_graph.graph_build_id)
        # TODO: should set graph with and without provider objects. without is to allow for deser to be robust
        executed_graph_cache_entry = CacheEntryGraph(graph=trimmed_graph, engine_id=self.engine_id)
        self.cache.set(f'executed_graph:{trimmed_graph.graph_build_id}', executed_graph_cache_entry)

        # TODO: break out graph build from graph execution to provide more official mechanism for running prebuilt graph
        try:
            return self.runner.run(trimmed_graph, self.cache)
        except ProviderException as e:
            # this is just to limit the amount of traceback from the ProviderException wrapper, to make it easier to
            # parse the main underlying exception above
            tb = sys.exc_info()[2]
            while tb.tb_next:
                tb = tb.tb_next
            raise e.with_traceback(tb)

    def run_call(self, call_key):
        return self._run_call_internal(call_key)

    def __setattr__(self, item, value):
        if item in (
            'cache',
            'builder',
            'runner',
            '_enforce_types',
            '_allow_edge_dependencies',
            '_allow_anon_services',
            'service_mapping',
            'executed_graph_build_ids',
            'value_hash_cache',
            'engine_id',
            '_catch_errors',
        ):
            return super().__setattr__(item, value)
        self.service_mapping.set({item: ConstantProvider(value)})

    def _compile(
            self,
            call_key,
            starting_call_path=(),
            no_value_hash=False,
            error_catched=False,
            trimmed=True,
            graph_caching=True,
            from_cache_only=False,
    ):
        if self.service_mapping.has_scoped_updates():
            # some bug with scoped updates and superset graph caching
            # disable for now when scoped updates present
            # TODO: figure out bug and remove this restriction - remove this and run tests to find it
            graph_caching = False

        pulled_from_cache = False
        if graph_caching:
            start = time.time()
            full_graph = pull_graph_from_cache_using_dep_indexes(call_key, self, self.cache)
            LOGGER.info(f'Pull graph from cache attempt took {round(time.time() - start, 2)} seconds')
        else:
            if from_cache_only:
                raise ValueError('If `from_cache_only`, graph_caching must be True')
            full_graph = None

        if full_graph is None:
            LOGGER.info('No graph pulled from cache, building from scratch')
            if from_cache_only:
                raise ValueError('Expected graph to be pulled from cache')
            start = time.time()
            full_graph = self.builder.build(self, call_key, starting_call_path, no_value_hash, error_catched)
            LOGGER.info(f'Full graph build took {round(time.time() - start, 2)} seconds')
        else:
            LOGGER.info('Reusing graph from cache')
            pulled_from_cache = True

        if graph_caching:
            full_graph_cache_entry = CacheEntryGraph(graph=full_graph, engine_id=self.engine_id)
            self.cache.set(f'full_graph:{full_graph.graph_build_id}', full_graph_cache_entry)
            if not pulled_from_cache:
                # do still want to cache graph above for diagnostic purposes
                # don't need to rewrite dep_indexes if pulled from cache though
                #   although maybe should we in cases where subgraph is pulled from superset graph?
                start = time.time()
                cache_dep_indexes(full_graph, self.cache, self)
                LOGGER.info(f'Dep set caching took {round(time.time() - start, 2)} seconds')

        if trimmed:
            LOGGER.info('Trimming graph')
            start = time.time()
            trimmed_graph = trim_graph(full_graph, self.cache)
            LOGGER.info(f'Graph trimming took {round(time.time() - start, 2)} seconds')
        else:
            trimmed_graph = full_graph.copy()

        if self._enforce_types:  # TODO: should type enforcement happen eagerly during graph build instead?
            enforce_types(full_graph)

        return trimmed_graph, full_graph

    def compile(self, call_key, catch_errors=False, trimmed=True, from_cache_only=False):
        trimmed_graph, full_graph = self._compile(
            call_key,
            trimmed=trimmed,
            error_catched=catch_errors,
            from_cache_only=from_cache_only,
        )
        return trimmed_graph

    def copy(self):
        new_ngn = Engine(cache=self.cache, builder=self.builder, runner=self.runner,
                         enforce_types=self._enforce_types, allow_edge_dependencies=self._allow_edge_dependencies,
                         allow_anon_services=self._allow_anon_services)
        new_ngn.service_mapping = self.service_mapping.copy()
        return new_ngn

    def clone(self):
        new_ngn = self.copy()
        new_ngn.executed_graph_build_ids = self.executed_graph_build_ids.copy()
        new_ngn.value_hash_cache = self.value_hash_cache
        new_ngn.engine_id = self.engine_id
        # don't do this - self._catch_errors should only be a temp state from ngn.catch, not meant to be persisted
        # new_ngn._catch_errors = self._catch_errors
        return new_ngn

    def update(self, updates: Dict[str, 'Provider'], scope=None, inplace=False):
        if inplace:
            new_ngn = self
        else:
            new_ngn = self.clone()
        if isinstance(updates, list):
            new_ngn.service_mapping.set({get_default_service_name(provider): provider for provider in updates}, scope=scope)
        elif isinstance(updates, dict):
            new_ngn.service_mapping.set(updates, scope=scope)
        return new_ngn

    def shock(self, service_name, shock_func, scope=None, return_type=None, inplace=False):
        shocked_services = [
            x for x in self.service_mapping.keys(scope or ())
            if (x == service_name) or (x.startswith(f'{service_name}__'))
        ]
        max_name = sorted(shocked_services)[-1]
        if max_name == service_name:
            max_count = -1
        else:
            max_count = int(max_name.split('__')[1])
        new_name = f'{service_name}__{max_count + 1}'
        shock_provider = ShockProvider(new_name, shock_func, return_type=return_type)
        new_ngn = (
            self
            .update({new_name: self.service_mapping.get(service_name, call_path=(scope or ()))}, scope=scope, inplace=inplace)
            .update({service_name: shock_provider}, scope=scope, inplace=inplace)
        )
        return new_ngn

    @property
    def callkey(self):
        return CallKeyCreator.from_call_path(self, ())

    def pin(self, call_key: 'CallKey', value, scope=None):
        try:
            provider = self.service_mapping.get(call_key.service_name, call_path=(scope or ()))
        except KeyError:
            provider = None
        if isinstance(provider, PinnedProvider):
            pass
        else:
            params = [
                inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                for name in ['ngn'] + list(call_key.kwargs.keys())
            ]
            signature = inspect.Signature(params)
            provider = PinnedProvider(call_key.service_name, signature)
        provider.insert_result(call_key.kwargs, value)
        return self.update({call_key.service_name: provider}, scope=scope)

    @property
    def inline(self):
        return self

    def collect(self):
        pass

    @property
    def type(self):
        return TypePassThrough(self)

    @property
    def catch(self):
        # TODO: need a better way to do this than making a copy
        ngn2 = self.clone()
        ngn2._catch_errors = True
        return ngn2

    def iter(self, n):
        # TODO: need to run a check that n >= len(result)
        return self

    @property
    def error(self):
        return ErrorSentinel

    @property
    def is_execution_mode(self):
        """
        providers run through all logic, collect is ignored
        for top level engine, always True
        """
        return True

    @property
    def is_collect_mode(self):
        """
        graph build mode, providers exit at collect
        for top level engine, never True
        """
        return False

    def trace(self, graph_build_id=None):
        if graph_build_id is None:
            if self.executed_graph_build_ids:
                graph_build_id = self.executed_graph_build_ids[-1]
            else:
                raise ValueError('no executed graph to trace')

        if graph_build_id not in self.executed_graph_build_ids:
            raise ValueError('graph_build_id was not executed on this engine, use Trace directly instead.')
        graph_cache_entry = self.cache.get(f'executed_graph:{graph_build_id}')
        return Trace(graph_cache_entry.graph, cache=self.cache)

    @classmethod
    def create(
            cls,
            providers: Dict | List,
            cache: Cache = None,
            builder: 'Builder' = None,
            runner: 'Runner' = None,
            enforce_types: bool = False,
            allow_edge_dependencies: bool = False,
            allow_anon_services: bool = False,
    ):
        ngn = cls(cache=cache, builder=builder, runner=runner,
                  enforce_types=enforce_types, allow_edge_dependencies=allow_edge_dependencies,
                  allow_anon_services=allow_anon_services)
        ngn.update(providers, inplace=True)
        return ngn
