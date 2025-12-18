import copy
from typing import Dict, Any, Union
from darl.call_key import CallKeyCreator, CallKey
from darl.collect import CollectExit
from darl.error_handling import ErrorSentinel, IterIndexError
from darl.dep_proxy import DepProxy, get_dep_proxy_arg_names, DepProxyException
from darl.service_caller import __shared__getitem__


class DependencyViolation(Exception):
    pass


class TypeAnnotator:
    def __init__(self, ngn_obj):
        self.ngn_obj: DepCollectorEngine = ngn_obj

    def __getitem__(self, item):
        ngn_obj = self.ngn_obj.copy()
        ngn_obj._type = item
        return ngn_obj


class _IterCaller:
    def __init__(self, ngn: Union['DepCollectorEngine', 'MockEngine'], n: int):
        self.ngn = ngn
        self.n = n

    def __getattr__(self, item):
        def caller(*args, **kwargs):
            res_to_iter = self.ngn[item](*args, **kwargs)

            if self.ngn.is_collect_mode:
                for i in range(self.n):
                    yield self.ngn._IterSlicer(res_to_iter, i, self.ngn.node_id)

            elif self.ngn.is_execution_mode:
                if self.n < len(res_to_iter):
                    raise ValueError(f'iter(n={self.n} is smaller than length of iter result, bump up n')

                for i in range(self.n):
                    sliced_res = self.ngn._IterSlicer(res_to_iter, i, self.ngn.node_id)
                    match sliced_res:  # TODO: convert to isinstance to allow <py3.10 compatibility?
                        case self.ngn.error(IterIndexError()):
                            continue
                        case _:
                            yield sliced_res
            else:
                raise ValueError('this shouldnt be reached')

        return caller


class MockEngine:
    def __init__(self, call_path, service_mapping, node_id, results=None, catch_errors=False):
        self.call_path = call_path
        # TODO: maybe collect provider signatures during graph build so that we don't need the full service mapping?
        #       probably doesn't make much difference though
        self.service_mapping = service_mapping  # this is only used for resolving kwargs
        if results is None:
            results = {}
        self.results: Dict['CallKey', Any] = results
        self.catch_errors = catch_errors
        self.node_id = node_id

    __getattr__ = __getitem__ = __shared__getitem__

    def run_call(self, call_key):
        try:
            res = copy.deepcopy(self.results[call_key])
            if isinstance(res, ErrorSentinel) and (not isinstance(res.error, IterIndexError)) and (not self.catch_errors):
                raise ValueError(
                    'This should never happen, since this should have short circuited before execution.'
                    # TODO: refactor to explicitly catch and raise this possible explanation during graph build
                    ' One possible explanation is that duplicate service calls are being made, one with'
                    ' .catch and one without.'
                )
            return res

        except KeyError:
            raise DependencyViolation('Attempted to call a service that was not specified as a dependency')

    @property
    def callkey(self):
        return CallKeyCreator(self.service_mapping, self.call_path)

    @property
    def inline(self):
        return self

    def collect(self):
        return

    @property
    def catch(self):
        mngn = self.copy()
        mngn.catch_errors = True
        return mngn

    @property
    def type(self):
        _self = self
        class _Dummy:
            def __getitem__(self, item):
                return _self
        return _Dummy()

    def iter(self, n):
        # TODO: need to do check for underestimate here I think
        return _IterCaller(self, n)

    @property
    def error(self):
        return ErrorSentinel

    @property
    def is_execution_mode(self):
        """
        providers run through all logic, collect is ignored
        for MockEngine, always True
        """
        return True

    @property
    def is_collect_mode(self):
        """
        graph build mode, providers exit at collect
        for MockEngine, never True
        """
        return False

    def copy(self):
        return MockEngine(
            call_path=self.call_path,
            service_mapping=self.service_mapping,
            node_id=self.node_id,
            results=self.results,
            catch_errors=self.catch_errors,
        )


class DepCollectorEngine:
    def __init__(self, ngn, call_path, service_mapping, node_id, inlined=False,
                 dep_keys=None, catch_errors=False, type=None):
        self._ngn = ngn
        self.call_path = call_path
        self.service_mapping = service_mapping  # TODO: guarantee that service_mapping matches call_path
        self.node_id = node_id
        self.inlined = inlined
        if dep_keys is None:
            dep_keys = []
        self.dep_keys = dep_keys
        self.catch_errors = catch_errors
        self._type = type

    __getattr__ = __getitem__ = __shared__getitem__

    def run_call(self, call_key):
        dep_proxy_arg_names = get_dep_proxy_arg_names(call_key.kwargs)
        if not self._ngn._allow_edge_dependencies and dep_proxy_arg_names:
            raise ValueError('Passed in a dep proxy as an arg to a service, to enable this set allow_edge_dependencies=True')
        for arg_name in dep_proxy_arg_names:
            dep_proxy_arg: DepProxy = call_key.kwargs[arg_name]
            if dep_proxy_arg.modified:
                raise DepProxyException('Attempted to pass in a dep proxy which has an operation applied to it, this is not allowed')

        # TODO: should we use a separate type from DepProxy?
        dep_proxy = DepProxy(call_key, catched=self.catch_errors, type=self._type)
        self.dep_keys.append(dep_proxy)
        if self.inlined:
            # TODO: if inlined don't add call key to deps, or else it will rebuild whole
            #       subgraph, even though the value has already been determined
            #       but actually we probably need that so that cache keys are built properly
            #       since inline is not value hashed, we care about upstream cache keys of inline

            # after inline call runs and graph is built, during trimming of graph the inline branch will
            # pull from cache and trim that branch from the graph. is this ok? probably
            return self._ngn._run_call_internal(call_key, self.call_path)
        else:
            return dep_proxy

    @property
    def callkey(self):
        # just do this Caller stuff so that callkey services are added to deps so that it will then
        # be added to service_mapping during execution, which is then used for kwargs resolving. if
        # you don't do this, kwarg resolution will fail since it won't be in service_mapping.
        # shouldn't cause much overhead at all since if you're creating a callkey in a service it
        # should be called elsewhere upstream anyway.
        # TODO: there should be a better way to do this, but this is just the least intrusive way to do it
        #       atm (as in no changes needed to be made elsewhere)
        class Caller:
            def __init__(self, dcn: DepCollectorEngine):
                self.dcn = dcn

            def __getattr__(self, item):
                def caller(*args, **kwargs):
                    call_key = CallKeyCreator(self.dcn.service_mapping, self.dcn.call_path).__getattr__(item)(*args, **kwargs)
                    self.dcn.dep_keys.append(DepProxy(call_key, False, None))
                    return call_key
                return caller

            __getitem__ = __getattr__

        return Caller(self)

    @property
    def inline(self):
        dcngn = self.copy()
        dcngn.inlined = True
        return dcngn

    def collect(self):
        # TODO: in functions that are not the top level provider being executed (e.g. non provider
        #       functions and super() calls) need to catch the CollectExit and only raise it up
        #       from top level provider
        raise CollectExit()

    @property
    def catch(self):
        dcngn = self.copy()
        dcngn.catch_errors = True
        return dcngn

    @property
    def type(self):
        return TypeAnnotator(self)

    @property
    def error(self):
        raise RuntimeError('Should not call ngn.error before ngn.collect() error handling should only be done after collect')

    def iter(self, n):
        return _IterCaller(self, n)

    @property
    def is_execution_mode(self):
        """
        providers run through all logic, collect is ignored
        for DepCollectorEngine, never True
        """
        return False

    @property
    def is_collect_mode(self):
        """
        graph build mode, providers exit at collect
        for DepCollectorEngine, always True
        """
        return True

    def copy(self):
        return DepCollectorEngine(
            ngn=self._ngn,
            call_path=self.call_path,
            service_mapping=self.service_mapping,
            node_id=self.node_id,
            inlined=self.inlined,
            dep_keys=self.dep_keys,
            catch_errors=self.catch_errors,
            type=self._type,
        )
