from dataclasses import dataclass, asdict
from typing import Any, Dict, Tuple, Callable
from darl.helpers import deterministic_hash, resolve_kwargs
from darl.service_caller import __shared__getitem__


@dataclass
class CallKey:
    service_name: str
    # IMPORTANT: kwargs must represent all args of a service call, not partial
    kwargs: Dict[str, Any]
    # not inclusive of call keys service name
    # used for overriding call path during graph build to originate at root of call key creation
    # TODO: should call_path_override be a attribute of the call_key or the dep_key? would prefer not on the call_key,
    #       however, if multiple of the same call_key from different scopes passed into a service through kwargs, need
    #       to be able to differentiate them
    #       e.g. this test test_call_key_multiple_from_different_scopes
    call_path_override: Tuple[str] | None = None  # should this just default to ()?
    provider_override: Callable | None = None  # this enables anonymous services

    def __hash__(self):
        # TODO: what to do about provider override? used for anon service calls
        return hash(deterministic_hash((self.service_name, self.kwargs, self.call_path_override)))

    def __eq__(self, other):
        return hash(self) == hash(other)

    def __repr__(self):
        if self.call_path_override is not None:
            call_path_override_str = f', {self.call_path_override}'
        else:
            call_path_override_str = ''

        if self.provider_override is not None:
            provider_override_str = f', {self.provider_override}'
        else:
            provider_override_str = ''
        return f'<CallKey({self.service_name}: {self.kwargs}{call_path_override_str}{provider_override_str})>'

    def replace(self, **kwargs):
        attrs = asdict(self)
        extra_args = set(kwargs.keys()) - set(attrs.keys())
        if extra_args:
            raise ValueError(f'extra replace args found: {extra_args}')
        attrs.update(kwargs)
        return CallKey(**attrs)


def get_call_key_from_provider(service_name, provider, *args, **kwargs):
    kwargs = resolve_kwargs(provider, *args, **kwargs)
    call_key = CallKey(service_name, kwargs)
    return call_key


class CallKeyCreator:
    def __init__(self, service_mapping, call_path=None):
        self.service_mapping = service_mapping
        self.call_path = call_path

    __getitem__ = __getattr__ = __shared__getitem__

    def run_call(self, call_key):
        '''
        only have this to allow reuse of the __shared__getitem__ used with the engines
        '''
        return call_key

    def __call__(self, starting_call_path=()):
        raise NotImplementedError
        # need to pass in ngn to init wherever used
        return CallKeyCreator.from_call_path(self.ngn, starting_call_path)

    @classmethod
    def from_call_path(cls, ngn, call_path):
        service_mapping = ngn.service_mapping.resolve_service_mapping(call_path)
        return cls(service_mapping, call_path)

