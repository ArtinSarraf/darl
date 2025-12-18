from darl.helpers import deterministic_hash


class DepProxyException(Exception):
    pass


class DepProxy:
    def __init__(
            self,
            call_key,
            catched,
            type,
    ):
        self.call_key = call_key
        self.catched = catched
        self.type = type
        self.modified = False

    def __getstate__(self):
        return self.__dict__

    def __setstate__(self, state):
        self.__dict__ = state

    def __repr__(self):
        return self.call_key.__repr__().replace('CallKey', 'DepProxy')

    def __hash__(self):
        return hash(deterministic_hash(('DepProxy', self.call_key,)))

    def __eq__(self, other):
        return hash(self) == hash(other)

    def __bool__(self):
        raise DepProxyException('cannot resolve proxy to bool')

    def __iter__(self):
        raise DepProxyException('cannot iter proxy')

    def __return_self_modified(self):
        if self.modified:
            return self
        else:
            # TODO: if self.modified ever checked at a later point than here, then might need
            #       to return a copy so that later modifications don't have unexpected effects
            # self = DepProxy(self.call_key)
            self.modified = True
            return self

    def __getattr__(self, item):
        return self.__return_self_modified()

    def __getitem__(self, item):
        return self.__return_self_modified()

    def __call__(self, *args, **kwargs):
        return self.__return_self_modified()

    def __add__(self, other):
        return self.__return_self_modified()

    def __sub__(self, other):
        return self.__return_self_modified()

    def __mul__(self, other):
        return self.__return_self_modified()

    def __truediv__(self, other):
        return self.__return_self_modified()

    def __divmod__(self, other):
        return self.__return_self_modified()

    def __radd__(self, other):
        return self.__return_self_modified()

    def __rsub__(self, other):
        return self.__return_self_modified()

    def __rmul__(self, other):
        return self.__return_self_modified()

    def __rtruediv__(self, other):
        return self.__return_self_modified()

    def __rdivmod__(self, other):
        return self.__return_self_modified()


def get_dep_proxy_arg_names(full_kwargs):
    dep_proxy_arg_names = []
    for key, val in full_kwargs.items():
        if isinstance(val, DepProxy):
            dep_proxy_arg_names.append(key)
    return dep_proxy_arg_names
