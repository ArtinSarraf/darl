# TODO: dry for all this logic that overlaps with sequential
# TODO: test parallel graph build
# TODO: parallel graph build can probably be a lot better, look into redoing it
import inspect
import pickle

from distributed import Client, secede, rejoin, get_client

from darl.cache import DictCache
from darl.execution.sequential import SequentialRunner
from darl.graph_build.base import Builder
from darl.graph import Graph, NodeKey, GraphCycleError
from darl.helpers import deterministic_hash
from darl.provider import hash_provider, get_dependencies
from darl.special_providers import ConstantProvider
from darl.typing import get_return_type

from darl.graph_build.sequential import SequentialBuilder


def build_graph_helper(ngn, call_key, starting_call_path, starting_cycle_check_stack, error_catched, no_value_hash):
    # starting_scoped_hash_stack just used to make sure no cycles exist
    # keeps track of all iso_keys that exist in the branch already to compare again and make sure current
    # scoped hash doesn't already exist
    ngn_ser = ngn
    ngn = pickle.loads(ngn_ser)

    client = get_client()

    if call_key.call_path_override is not None:
        starting_call_path = call_key.call_path_override
    call_path = starting_call_path + (call_key.service_name,)

    scope_relevant_call_path = ngn.service_mapping.get_relevant_call_path(call_path)
    # resolved_scope stuff added for graph caching compatibility with scoped updates
    resolved_service_mapping = ngn.service_mapping.resolve_service_mapping(scope_relevant_call_path, with_scope=True)

    if call_key.provider_override is None:
        (provider, resolved_scope) = resolved_service_mapping[call_key.service_name]
    else:
        provider = call_key.provider_override
        resolved_scope = None

    # drop scope and just keep service_name: provider mapping
    resolved_service_mapping = {k: prov for k, (prov, sc) in resolved_service_mapping.items()}

    return_type = get_return_type(provider)
    deps_expected_types = {}

    iso_key = NodeKey(
        provider_hash=hash_provider(provider),
        provider_kwargs_hash=deterministic_hash(call_key.kwargs),
        # need path since the branch of the graph can diverge if a
        # scope ends up materializing during the graph build.
        scope_relevant_call_path=scope_relevant_call_path,
    )

    cycle_services = []
    cycle_detected = False

    for scoped_hash, service in starting_cycle_check_stack:
        if (not cycle_detected) and (scoped_hash == iso_key.scoped_hash):
            cycle_detected = True
        if cycle_detected:
            cycle_services.append(service)

    if cycle_detected:
        cycle_services.append(call_key.service_name)
        # service_name alone might not be enough to easily identify cycles completely since kwargs can have impact
        # but full call_key would be too verbose for error
        raise GraphCycleError('Cycle detected: {}'.format(cycle_services))
    cycle_check_stack = starting_cycle_check_stack + (
        (iso_key.scoped_hash, call_key.service_name),)  # no append, want new stack object

    graph_node = client.datasets.get(f'node-{iso_key.scoped_hash}')

    if graph_node is not None:
        if error_catched:
            # will this easily translate to a distributed graph build? if not can have some separate error_catch
            # override record tracked somewhere and resolved at the end of the graph build
            graph_node['error_catched'] = error_catched
        # since there's different call_keys the node can be called with from different providers
        graph_node['call_keys'].add(call_key)
        client.datasets[f'node-{iso_key.scoped_hash}'] = graph_node
        return iso_key

    node_edges = set()

    value_hashed = getattr(provider, 'value_hashed', False) and (not no_value_hash)
    if value_hashed:
        catches_errors = False  # TODO: can this catch errors? I guess it can and will on no_value_hash run
        value = client.datasets.get(f'value_hash_cache-{iso_key.scoped_hash}')
        if value is None:
            ngn2 = ngn.clone()
            ngn2.runner = SequentialRunner()
            ngn2.cache = DictCache()

            # no_value_hash=True is to force running through standard graph build path and not
            # hit this line again and go in infinite loop. this will be used in conjunction with
            # a fresh local cache/runner each time so will not result in any bad conflicts
            # TODO: is there a better solution than this?
            value = ngn2._run_call_internal(call_key, starting_call_path,
                                            no_value_hash=True)  # TODO: do I pass error_catched into here?
            client.datasets[f'value_hash_cache-{iso_key.scoped_hash}'] = value

        iso_key.value_hash_override = deterministic_hash(value)
        # redefine provider, AFTER hashing provider for iso key - want original provider hash
        provider = ConstantProvider(value, signature=inspect.signature(provider))
    else:
        dependency_keys = get_dependencies(
            provider,
            ngn,
            scope_relevant_call_path,
            resolved_service_mapping,
            iso_key.hash,  # should this be scoped hash? needs to match with runner run_provider
            **call_key.kwargs,
        )

        dep_iso_key_fs = []
        catches_errors = False
        for dep_key in dependency_keys:
            dep_call_key = dep_key.call_key
            print('Submitting', dep_call_key)
            dep_iso_key_f = client.submit(
                build_graph_helper,
                ngn=ngn_ser,
                call_key=dep_call_key,
                starting_call_path=call_path,
                starting_cycle_check_stack=cycle_check_stack,
                error_catched=dep_key.catched,
                no_value_hash=no_value_hash,
                key=f'build_{dep_call_key.service_name}-{deterministic_hash(dep_call_key)}',
            )
            dep_iso_key_fs.append(dep_iso_key_f)

        secede()
        dep_iso_keys = client.gather(dep_iso_key_fs)
        rejoin()

        for dep_key, dep_iso_key in zip(dependency_keys, dep_iso_keys):
            if dep_key.catched:
                catches_errors = True
            node_edges.add(dep_iso_key.scoped_hash)
            if dep_key.type is not None:
                deps_expected_types[dep_iso_key.hash] = dep_key.type

    client.datasets[f'edges-{iso_key.scoped_hash}'] = node_edges
    client.datasets[f'node-{iso_key.scoped_hash}'] = {
        'provider': provider,
        'call_keys': {call_key},
        'iso_key': iso_key,
        'scope_call_path': scope_relevant_call_path,
        'error_catched': error_catched,
        'catches_errors': catches_errors,
        'deps_expected_types_by_hash': deps_expected_types,
        'return_type': return_type,
        'value_hashed': value_hashed,
        'from_cache_only': False,
        'computed_from_graph_build_id': None,
        'resolved_scope': resolved_scope,
    }

    return iso_key


class DaskBuilder(Builder):
    def __init__(self, client):
        self.client = client

    def __setstate__(self, state):
        self.client = Client(state['address'])

    def __getstate__(self):
        return {'address': self.client.scheduler.address}

    def _build_iso_graph(
            self,
            iso_graph,
            ngn,
            root_call_key,
            starting_call_path=(),
            no_value_hash=False,
            error_catched=False
    ) -> 'Graph':
        graph = iso_graph

        _ngn = ngn.clone()
        _ngn.builder = None  # if you keep dask builder, on deser it will try to recreate a new client connection each time, and blow up
        _ngn.runner = None

        for k, v in ngn.value_hash_cache.items():
            self.client.datasets[f'value_hash_cache-{k}'] = v

        f = self.client.submit(
            build_graph_helper,
            ngn=pickle.dumps(_ngn),
            call_key=root_call_key,
            starting_call_path=starting_call_path,
            starting_cycle_check_stack=(),
            error_catched=error_catched,
            no_value_hash=no_value_hash,
            key='build_root'
        )

        self.client.gather([f])

        variable_names = self.client.list_datasets()
        # TODO: need to namespace by graph_build_id
        #       also this is probably too slow in general, do a better distributed build, maybe coordinate with redis
        for variable_name in variable_names:
            val = self.client.datasets.pop(variable_name)
            val_type, iso_key_scoped_hash = variable_name.split('-')
            if val_type == 'edges':
                graph_dict = graph.edges
            elif val_type == 'node':
                graph_dict = graph.nodes
            elif val_type == 'value_hash_cache':
                graph_dict = ngn.value_hash_cache
            else:
                raise ValueError(f'unexpected val type: {val_type}')
            graph_dict[iso_key_scoped_hash] = val

        # some bug where if you rerun in succession too quickly some submitted futures don't get run, this mitigates that
        # alternatively could put some buffering logic to wait at least x seconds since last run
        self.client.restart()

        return graph

    def _resolve_cache_keys(self, iso_graph: 'Graph'):
        return SequentialBuilder()._resolve_cache_keys(iso_graph)

    def _mark_error_catched_nodes(self, graph: 'Graph'):
        return SequentialBuilder()._mark_error_catched_nodes(graph)
