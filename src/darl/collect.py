import inspect
from functools import wraps


class CollectExit(Exception):
    pass


# TODO: should we autpdecorate methods of provider classes with this so we don't have to
#       explicitly decorate classes to subclass them and have super calls respect ngn.collect?
def embeddable(callable_obj):
    """
    Allows you to call a provider as a regular function within another provider. Without this
    the ngn.collect() call in the embedded provider would prematurely exit the main top level provider.
    """
    if not inspect.isfunction(callable_obj):  # is class
        if not hasattr(callable_obj, '__call__'):
            raise TypeError('embeddable must be decorated on a function or class with __call__ method')
        for k, v in vars(callable_obj).items():
            if inspect.isfunction(v):
                decorated = embeddable(v)
                setattr(callable_obj, k, decorated)
        return callable_obj

    @wraps(callable_obj)
    def inner(*args, **kwargs):
        try:
            return callable_obj(*args, **kwargs)
        except CollectExit:
            return
    return inner
