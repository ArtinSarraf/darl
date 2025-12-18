import inspect
from graphlib import TopologicalSorter


def get_return_type(provider):
    provider = getattr(provider, '__provider__', provider)
    return inspect.signature(provider).return_annotation


def enforce_types(graph):
    order = TopologicalSorter(graph.edges).static_order()
    for node in order:
        for dep_node, expected_type in graph.nodes[node]['deps_expected_types_by_cache_key'].items():
            # TODO: type checking needs to be implented beyond equality
            # TODO: allow custom type checking logic
            if graph.nodes[dep_node]['return_type'] != expected_type:
                # TODO: find all mismatches instead of fast fail
                # TODO: show info about mismatch
                raise TypeError('Type mismatch')

