import inspect

from darl.helpers import resolve_kwargs, hash_callable


def is_lambda(func):
    return inspect.isfunction(func) and ('<lambda>' in getattr(func, '__name__', ''))


class LambdaProvider:  # TODO: move to special_providers.py
    '''
    lambda redefined with new memory location in repr every pass through a function
    so when passed as a kwarg to a callkey, equality fails and mockeng look up fails
    '''
    def __init__(self, lambda_provider):
        self.__provider__ = lambda_provider
        self.__signature__ = inspect.signature(lambda_provider)

    def __call__(self, ngn, *args, **kwargs):
        return self.__provider__(ngn, *args, **kwargs)

    def __repr__(self):
        # need this for now, since deterministic hash of call_keys (and equality) is based on encoding of
        # callkey which includes callkey kwargs, which includes repr of this if it shows up in kwargs, e.g.
        # if a callkey with provider override is passed into another service
        # TODO: need better recursive deterministic hash of callkeys
        lambda_code = self.__provider__.__code__
        return f'<lambda_provider>.{lambda_code.co_filename}.{lambda_code.co_name}'


def __shared__getitem__(self, item):  # used by top level Engine, MockEngine and DepCollectorEngine
    if not callable(item) and item not in self.service_mapping:
        raise AttributeError(item)

    def collector(*args, **kwargs):
        # TODO: fix so local imports not needed
        from darl.call_key import CallKey, get_call_key_from_provider
        from darl.provider import is_service_arg_provider

        call_path = getattr(self, 'call_path', ())  # top level Engine doesn't have call_path so call getattr here
        if callable(item):
            provider = item

            if hasattr(self, '_ngn'):
                allow_anon_services = self._ngn._allow_anon_services
            else:
                # default True only reached for MockEngine, if its disallowed it will fail before it gets here
                # if it gets to MockEngine then just go always True so no extra logic needed to propagate the attr to MockEngine
                allow_anon_services = getattr(self, '_allow_anon_services', True)

            if not allow_anon_services:
                raise ValueError('Anonymous services are disabled. Instantiate Engine with `allow_anon_services=True`')
            if is_service_arg_provider(provider):
                raise TypeError('Anonymous service provider must have `ngn` as first arg')
            if is_lambda(provider):
                provider = LambdaProvider(provider)
            kwargs = resolve_kwargs(provider, *args, **kwargs)
            call_key = CallKey(
                service_name=hash_callable(provider),
                kwargs=kwargs,
                call_path_override=call_path,
                provider_override=provider,
            )
        else:
            provider = self.service_mapping[item]
            call_key = get_call_key_from_provider(item, provider, *args, **kwargs)
            call_key.call_path_override = call_path
        return self.run_call(call_key)
    return collector
