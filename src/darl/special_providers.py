import inspect
import json

from darl.error_handling import IterIndexError
from darl.provider import get_default_service_name


class ShockProvider:
    def __init__(self, base_service_name, shock_func, service_name=None, return_type=None):
        self.base_service_name = base_service_name
        self.shock_func = shock_func
        self.service_name = service_name
        if return_type:
            self.__call__.__func__.__annotations__ = {'return': return_type}

    def __call__(self, ngn, **kwargs):
        base_result = ngn[self.base_service_name](**kwargs)
        ngn.collect()
        return self.shock_func(base_result, **kwargs)


class ConstantProvider:  # TODO: combine with PinnedProvider
    def __init__(self, value, service_name=None, signature=None):
        self.value = value
        self.service_name = service_name
        if signature is not None:
            self.__signature__ = signature

    def __call__(self, ngn, **kwargs):
        ngn.collect()
        return self.value


def _dict_to_sorted_items(d):
    '''
    use json dumps since this result will be used as a key in a dict on the PinnedProvider
    and that PinnedProvider will later be json serialized at some point, and dict keys can't be tuples in json
    '''
    return json.dumps([(k, d[k]) for k in sorted(d.keys())])


class PinnedProvider:
    def __init__(self, service_name, signature):
        self.results = {}
        self.service_name = service_name
        self.__signature__ = signature

    def __call__(self, ngn, **kwargs):
        key = _dict_to_sorted_items(kwargs)
        return self.results[key]

    def insert_result(self, kwargs, result):
        key = _dict_to_sorted_items(kwargs)
        self.results[key] = result


class ServiceArgProviderStandardizer:
    def __init__(self, provider):
        self.__provider__ = provider
        self.service_name = get_default_service_name(provider)
        self.value_hashed = getattr(self.__provider__, 'value_hashed', False)

    def __call__(self, ngn):
        results = {}
        for service_name, param in inspect.signature(self.__provider__).parameters.items():
            if param.annotation == inspect._empty:
                results[service_name] = ngn[service_name]()
            else:
                results[service_name] = ngn.type[param.annotation][service_name]()
        ngn.collect()
        return self.__provider__(**results)


def _IterSlicer(ngn, result_to_slice, idx, iter_id):
    ngn.collect()
    try:
        return result_to_slice[idx]
    except IndexError as e:
        raise IterIndexError(iter_id)
