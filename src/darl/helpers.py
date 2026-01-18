# TODO: break out hashing specific functions into its own hashing.py
# TODO: make hasher configurable. can mostly be accessed through ngn, but CallKey will need to get it explicitly
import inspect
import json
import hashlib
from types import SimpleNamespace, LambdaType


def custom_encoder(obj):
    from darl.call_key import CallKey
    from darl.mock_engine import DepProxy
    from darl.error_handling import ErrorSentinel

    if isinstance(obj, CallKey):
        # don't use asdict, that will cause a deepcopy of the attrs in the CallKey, which for
        # provider_override functions means it will provide the function at a new location.
        # as of now since it will end up using the str repr of the function which includes the memory
        # location that will cause an inconsistent encoding. can get around this by using something other
        # than str repr for these objects, or put some wrapper object that holds the function and intercepts
        # deepcopy.
        # TODO: need better recursive deterministic hash of callkeys
        d = obj.__dict__
        return f'CallKey:{d}'
    elif isinstance(obj, DepProxy):
        return f'DepProxy:{custom_encoder(obj.call_key)}'
    elif isinstance(obj, ErrorSentinel):
        return f'ErrorSentinel:{str(obj.error)}'
    elif isinstance(obj, inspect.Signature):
        return f'Signature:{str(obj)}'
    raise TypeError(f"{type(obj).__name__} is not JSON serializable")


def deterministic_hash(obj):
    m = hashlib.sha256()
    m.update(json.dumps(obj, default=custom_encoder).encode())
    return m.hexdigest()


def hash_callable(obj):
    """
    TODO: handle closures where source code is the same, but different values attached
    """
    if inspect.isfunction(obj):
        obj = SimpleNamespace(func=obj, name='dummy_obj_func_holder')
        mro = []
    else:
        mro = obj.__class__.mro()[:-1]  # don't include base 'object'

    # Hash the instance attributes (including attributes from all parent classes)
    to_hash = []

    # Traverse the class hierarchy (including parent classes)
    for cls in [obj] + mro:  # obj not a cls but eh, fits the interface here
        for attr in vars(cls):
            if attr in ('__dict__', '__weakref__', '__module__',
                        '__firstlineno__',
                        '__slotnames__'):  # pickle.dumps'ing and object can add __slotnames__ to it
                continue
            val = getattr(cls, attr)
            if inspect.isbuiltin(val):
                to_hash.append((attr, ('builtins', repr(val))))
            elif inspect.isfunction(val):
                # to_hash.append((attr, str(val.__code__.co_code)))  # Hash method bytecode # this is not right - need to also include other co_* attrs

                # reasoning for defaults is if default value is attached from external scope, then sourcecode can be same
                # even if attached value and execution would be different, e.g.
                #
                # funcs = {}
                # for val in ['a', 'b', 'c']:
                #     def func(x=val):
                #         print(x)
                # funcs[val] = func
                to_hash.append((attr, (inspect.getsource(val), val.__defaults__)))
            elif callable(val):
                to_hash.append((attr, hash_callable(val)))
            else:
                to_hash.append((attr, val))

    return deterministic_hash(to_hash)


def resolve_kwargs(provider, *args, **kwargs):
    args = ('ngn_throwaway',) + args
    sig = inspect.signature(provider)
    special_args = [
        k for k, v in sig.parameters.items()
        if v.kind in [inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD]
    ]
    bound_args = sig.bind(*args, **kwargs)
    bound_args.apply_defaults()
    kwargs = bound_args.arguments
    for name in special_args:
        if kwargs[name]:
            raise ValueError('*args and **kwargs should not be used')
        kwargs.pop(name)
    kwargs.pop('ngn')
    return kwargs


class NumberedList(list):
    def __repr__(self):
        if not self:
            return "[]"
        lines = [f"  ({i}) {repr(item)}" for i, item in enumerate(self)]
        return "[\n" + ",\n".join(lines) + "\n]"
