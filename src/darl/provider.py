import inspect

from darl.dep_proxy import get_dep_proxy_arg_names
from darl.error_handling import ErrorSentinel, UpstreamException, IterIndexError, raise_provider_exception
from darl.helpers import hash_callable
from darl.mock_engine import MockEngine, DepCollectorEngine, CollectExit, DepProxy, DependencyViolation


def value_hashed(provider):
    provider.value_hashed = True
    return provider


def is_service_arg_provider(provider):
    """
    Determine if function is a provider where the args are service names
    any provider whose first arg is not ngn is considered to be a service arg provider
    """
    params = list(inspect.signature(provider).parameters.keys())
    return (
        (not params)
        or (params[0] != 'ngn')
    )


def _calls_collect(provider):
    '''
    TODO: is this robust to all edge cases?
    '''
    if inspect.isfunction(provider):
        code = inspect.getsource(provider)
    else:
        code = inspect.getsource(provider.__call__)
    return 'ngn.collect()' in [x.strip() for x in code.split()]


# node_id only needed for nested iter, to keep track of where iter originated from to avoid conflicts
def get_dependencies(provider, ngn, call_path, service_mapping, node_id, **full_kwargs):
    if _calls_collect(provider):
        dcngn = DepCollectorEngine(ngn, call_path, service_mapping, node_id)
        try:
            provider(dcngn, **full_kwargs)
        except CollectExit:
            dep_keys = dcngn.dep_keys
        else:
            raise ValueError('no collect found')
    else:
        dep_keys = []

    ## dep proxy handling from here
    for k, v in full_kwargs.items():
        if isinstance(v, DepProxy):
            dep_keys.append(v)
    ## dep proxy handling to here

    return dep_keys


class _ShortCircuit(Exception):
    pass


# great name
def _handle_error_handling(dep_results, error_catched, catches_errors, node_id):
    for call_key, result in dep_results.items():  # this will capture both service call and dep proxy arg deps
        # TODO: replace with match/case and don't worry about <py3.10 compatibility?
        if isinstance(result, ErrorSentinel) and isinstance(result.error, IterIndexError):
            # special case from ngn.iter(n=n).Service()
            if result.error.args[0] == node_id:  # if we are in the originating provider of the iter call, do not propagate index error further
                pass  # will pass through to the provider, but will be ignored by iter logic
            else:
                raise _ShortCircuit(result)
        elif isinstance(result, ErrorSentinel):
            if catches_errors:
                pass  # allow the error sentinel to pass through to the provider logic
            else:
                if error_catched:
                    raise _ShortCircuit(result)
                else:
                    # possible reason - failing service catched in one place but not another
                    raise UpstreamException('Improperly handled upstream exception found, see above traceback') from result.error
        else:
            pass


def _handle_dep_proxy(dep_results, full_kwargs):
    '''
    in execution mode, values will be resolved when returned from service call,
    so where in graph build mode:
    x = ngn.X()
    y = ngn.Y(x)
    would result in x being a dep_proxy object, thus kwargs of Y would have a dep_proxy object
    now in execution mode the same code when run through, x will be a resolved value
    and so ngn.Y(x) will now have an actual value for the kwargs instead of a dep_proxy object
    and so in the lookup against the dep_results (which are keyed by CallKey) need to have the kwargs
    of the call_key have the actual resolved value instead of the dep_proxy object
    '''
    dep_proxy_to_resolved_val_mapping = {}
    for dep_proxy_arg_name in get_dep_proxy_arg_names(full_kwargs):
        dep_proxy = full_kwargs[dep_proxy_arg_name]
        arg_result = dep_results[dep_proxy.call_key]
        full_kwargs[dep_proxy_arg_name] = arg_result
        dep_proxy_to_resolved_val_mapping[dep_proxy] = arg_result

    # the above will resolve dep_proxys passed into the kwargs of the provider. the below will resolve
    # dep_proxys that originated within the provider passed into other calls within the same provider
    # e.g. `ngn.B(ngn.A())`
    call_keys_from_kwarg_dep_proxies = [dp.call_key for dp in dep_proxy_to_resolved_val_mapping.keys()]
    for call_key, result in dep_results.items():
        if call_key in call_keys_from_kwarg_dep_proxies:
            # some dep_proxys passed into the kwargs, might themselves have dep_proxy kwargs,
            # we don't want to consider those as intra-provider passed dep proxies
            continue
        for k, v in call_key.kwargs.items():
            if (  # not passed in as kwarg, but edge dep within provider
                isinstance(v, DepProxy)
                and (v not in dep_proxy_to_resolved_val_mapping)
            ):
                dep_proxy_to_resolved_val_mapping[v] = dep_results[v.call_key]

    if dep_proxy_to_resolved_val_mapping:
        # need to rebuild the objects because otherwise dictionary hashing/lookup gets messed up
        # both for call_key kwargs dict and dep_results dict
        resolved_dep_results = {}
        for call_key, result in dep_results.items():
            new_call_key = call_key.replace(
                kwargs={
                    k: dep_proxy_to_resolved_val_mapping.get(v, v)
                    for k, v in call_key.kwargs.items()
                },
            )
            resolved_dep_results[new_call_key] = result
    else:
        resolved_dep_results = dep_results

    return resolved_dep_results


def run_provider(
        provider,
        call_path,
        dep_results,
        dep_service_mapping,
        node_id,  # used to keep track of iter origin in case of nested iters to avoid conflict
        cache_key,  # cache_key and graph_build_id used to format error message for debugging context
        graph_build_id,
        error_catched=False,
        catches_errors=False,
        **full_kwargs
):
    try:
        _handle_error_handling(dep_results, error_catched, catches_errors, node_id)
    except _ShortCircuit as e:
        return e.args[0]
    resolved_dep_results = _handle_dep_proxy(dep_results, full_kwargs)

    mock_ngn = MockEngine(call_path, dep_service_mapping, node_id, resolved_dep_results)
    try:
        return provider(mock_ngn, **full_kwargs)
    except IterIndexError as e:
        return ErrorSentinel(e, None)  # handled without catch mechanism
    except Exception as e:
        if isinstance(e, DependencyViolation):  # should dependency violation be able to be catched? thinking no
            raise_provider_exception(e, graph_build_id, cache_key)  # TODO: is there a better place to wrap this exception? higher up the stack maybe?
        elif error_catched:
            return ErrorSentinel(e, None)  # TODO: pass origin info through
        else:
            raise_provider_exception(e, graph_build_id, cache_key)  # TODO: consolidate with other raise above


def hash_provider(provider):
    return hash_callable(provider)


def get_default_service_name(provider):
    if getattr(provider, 'service_name', None) is not None:
        return provider.service_name
    elif inspect.isfunction(provider):
        return provider.__name__
    else:
        return provider.__class__.__name__
