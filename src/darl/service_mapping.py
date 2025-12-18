from collections import defaultdict
from typing import Tuple, Dict

from darl.provider import is_service_arg_provider
from darl.special_providers import ServiceArgProviderStandardizer, _IterSlicer


def filter_path_to_scopes(path, scopes):
    filtered_path = []
    scope_idxs = [0 for _ in scopes]
    for element in path:
        add_element = False
        for i, (scope, scope_idx) in enumerate(zip(scopes, scope_idxs)):
            try:
                scope_element = scope[scope_idx]
            except IndexError:
                continue
            else:
                if element == scope_element:
                    add_element = True
                    scope_idxs[i] += 1
        if add_element:
            filtered_path.append(element)
    return tuple(filtered_path)


def is_path_in_scope(path, scope):
    return filter_path_to_scopes(path, [scope]) == scope


class ServiceMapping:
    def __init__(self):
        self.service_mapping_by_scope: Dict[Tuple[str, ...], Dict[str, 'Provider']] = defaultdict(
            dict,
            {(): {
                '_IterSlicer': _IterSlicer  # used for ngn.iter...
            }}
        )

    def set(self, mapping: Dict[str, 'Provider'], scope: Tuple[str, ...] = None):
        if scope is None:
            scope = ()

        processed_mapping = {}
        for service_name in mapping.keys():
            if service_name in scope:
                # you can, but easier to just not allow it, and not that useful afaik
                raise ValueError('cannot scope update service that is part of scope')

            provider = mapping[service_name]
            if is_service_arg_provider(provider):
                provider = ServiceArgProviderStandardizer(provider)
            processed_mapping[service_name] = provider

        self.service_mapping_by_scope[scope].update(processed_mapping)

    def get(self, service_name, call_path):
        service_mapping = self.resolve_service_mapping(call_path)
        return service_mapping[service_name]

    # region this getitem and contains are for matching interface of top level engine with internal mock engines
    def __getitem__(self, item):
        return self.get(item, ())

    def __contains__(self, item):
        return item in self.service_mapping_by_scope[()]
    # endregion

    def keys(self, call_path):
        service_mapping = self.resolve_service_mapping(call_path)
        return service_mapping.keys()

    def resolve_service_mapping(self, call_path, with_scope=False):
        """
        with_scope: bool - if true, mapping will include the scope from which each provider was picked
        """
        # TODO: can probably be optimized to avoid constantly filtering path in is_path_in_scope check
        final_mapping = {}
        for scope, mapping in self.service_mapping_by_scope.items():
            if with_scope:
                mapping = {k: (v, scope) for k, v in mapping.items()}
            if is_path_in_scope(call_path, scope):
                final_mapping.update(mapping)
        return final_mapping

    def get_relevant_call_path(self, call_path):
        """
        Filters the call path to just values that are relevant to identifying any
        scopes in the scope mappings. This includes call paths both partially and fully
        realizing a scope path.
        """
        return filter_path_to_scopes(call_path, self.service_mapping_by_scope.keys())

    def copy(self):
        new_service_mapping = ServiceMapping()
        for scope, mapping in self.service_mapping_by_scope.items():
            new_service_mapping.set(mapping=mapping, scope=scope)
        return new_service_mapping

    def has_scoped_updates(self):
        return list(self.service_mapping_by_scope.keys()) != [()]





