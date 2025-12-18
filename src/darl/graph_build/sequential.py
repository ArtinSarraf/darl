import inspect
from collections import defaultdict
from graphlib import TopologicalSorter
from typing import TYPE_CHECKING

from darl.cache import DictCache
from darl.execution.sequential import SequentialRunner
from darl.graph import Graph, GraphCycleError, NodeKey
from darl.graph_build.base import Builder
from darl.helpers import deterministic_hash
from darl.provider import get_dependencies, hash_provider
from darl.special_providers import ConstantProvider
from darl.typing import get_return_type

if TYPE_CHECKING:
    from darl.call_key import CallKey


class SequentialBuilder(Builder):
    def _build_iso_graph(
            self,
            iso_graph,
            ngn,
            root_call_key,
            starting_call_path=(),
            no_value_hash=False,
            error_catched=False
    ):
        graph = iso_graph

        # TODO: call path/scoping kwarg sensitive
        # TODO: reimplement as an iterative solution rather than recursive? really shouldn't have graph depth
        #       come close to max recursion depth limit though
        def build_graph_helper(call_key: 'CallKey', starting_call_path, starting_cycle_check_stack, error_catched=False):
            # starting_scoped_hash_stack just used to make sure no cycles exist
            # keeps track of all iso_keys that exist in the branch already to compare again and make sure current
            # scoped hash doesn't already exist
            if call_key.call_path_override is not None:
                starting_call_path = call_key.call_path_override
            call_path = starting_call_path + (call_key.service_name,)

            # TODO: do something smarter so you don't have to refilter everytime
            scope_relevant_call_path = ngn.service_mapping.get_relevant_call_path(call_path)
            # resolved_scope stuff added for graph caching compatibility with scoped updates
            resolved_service_mapping = ngn.service_mapping.resolve_service_mapping(scope_relevant_call_path, with_scope=True)

            if call_key.provider_override is None:
                (provider, resolved_scope) = resolved_service_mapping[call_key.service_name]
            else:
                provider = call_key.provider_override
                resolved_scope = None

            # drop scope and just keep service_name: provider mapping
            resolved_service_mapping = {k: prov for k, (prov, sc) in resolved_service_mapping.items()}

            return_type = get_return_type(provider)
            deps_expected_types = {}

            iso_key = NodeKey(
                provider_hash=hash_provider(provider),
                provider_kwargs_hash=deterministic_hash(call_key.kwargs),
                # need path since the branch of the graph can diverge if a
                # scope ends up materializing during the graph build.
                scope_relevant_call_path=scope_relevant_call_path,
            )

            cycle_services = []
            cycle_detected = False
            # TODO: use a set for cycle check? (would still need a list too, to keep track of call path to show in error)
            # TODO: since this check does add some amount of time and since cycles would just naturally get caught by
            #       recursion error anyway, could disable this and let recursion error catch it. could catch recursion
            #       error and then print out the service cycle from call_path.
            #       however if we switch to a iterative graph build or parallel graph build recursion error will no longer
            #       work. in that case either need this check or build in a "depth" limit that we keep track of (rather than
            #       tracked by stack limit), this would be faster than the check, but of course slower to catch a cycle if
            #       it exists. But until we determine that this check is too expensive, leave as is
            for scoped_hash, service in starting_cycle_check_stack:
                if (not cycle_detected) and (scoped_hash == iso_key.scoped_hash):
                    cycle_detected = True
                if cycle_detected:
                    cycle_services.append(service)

            if cycle_detected:
                cycle_services.append(call_key.service_name)
                # service_name alone might not be enough to easily identify cycles completely since kwargs can have impact
                # but full call_key would be too verbose for error
                raise GraphCycleError('Cycle detected: {}'.format(cycle_services))
            cycle_check_stack = starting_cycle_check_stack + (
                (iso_key.scoped_hash, call_key.service_name),)  # no append, want new stack object

            # TODO: don't actually need to check based on iso_key.
            #       Hash of call_key + scope_relevant_call_path good enough.
            #       iso_key needed for cross process sync, for single graph build
            #       call_key+ good enough, and doesn't require provider hash.
            if iso_key.scoped_hash in graph.nodes:
                if error_catched:
                    # will this easily translate to a distributed graph build? if not can have some separate error_catch
                    # override record tracked somewhere and resolved at the end of the graph build
                    graph.nodes[iso_key.scoped_hash]['error_catched'] = error_catched
                # since there's different call_keys the node can be called with from different providers
                graph.nodes[iso_key.scoped_hash]['call_keys'].add(call_key)
                return iso_key

            graph.edges[iso_key.scoped_hash] = set()

            value_hashed = getattr(provider, 'value_hashed', False) and (not no_value_hash)
            if value_hashed:
                catches_errors = False  # TODO: can this catch errors? I guess it can and will on no_value_hash run
                value = ngn.value_hash_cache.get(iso_key.scoped_hash, None)
                if value is None:
                    ngn2 = ngn.clone()
                    ngn2.runner = SequentialRunner()
                    ngn2.cache = DictCache()

                    # no_value_hash=True is to force running through standard graph build path and not
                    # hit this line again and go in infinite loop. this will be used in conjunction with
                    # a fresh local cache/runner each time so will not result in any bad conflicts
                    # TODO: is there a better solution than this?
                    value = ngn2._run_call_internal(call_key, starting_call_path, no_value_hash=True)  # TODO: do I pass error_catched into here?
                    ngn.value_hash_cache[iso_key.scoped_hash] = value

                iso_key.value_hash_override = deterministic_hash(value)
                # redefine provider, AFTER hashing provider for iso key - want original provider hash
                provider = ConstantProvider(value, signature=inspect.signature(provider))
            else:
                dependency_keys = get_dependencies(
                    provider,
                    ngn,
                    scope_relevant_call_path,
                    resolved_service_mapping,
                    iso_key.hash,  # should this be scoped hash? needs to match with runner run_provider
                    **call_key.kwargs,
                )

                catches_errors = False
                for dep_key in dependency_keys:
                    dep_call_key = dep_key.call_key
                    dep_iso_key = build_graph_helper(dep_call_key, call_path, cycle_check_stack, dep_key.catched)
                    if dep_key.catched:
                        catches_errors = True
                    graph.edges[iso_key.scoped_hash].add(dep_iso_key.scoped_hash)
                    if dep_key.type is not None:
                        deps_expected_types[dep_iso_key.hash] = dep_key.type

            # note: some nodes might be merged together when it ends up being
            #       determined that they were not different under different scopes
            graph.nodes[iso_key.scoped_hash] = {
                'provider': provider,
                'call_keys': {call_key},
                'iso_key': iso_key,
                'scope_call_path': scope_relevant_call_path,
                'error_catched': error_catched,
                'catches_errors': catches_errors,
                'deps_expected_types_by_hash': deps_expected_types,
                'return_type': return_type,
                'value_hashed': value_hashed,
                'from_cache_only': False,
                'computed_from_graph_build_id': None,
                'resolved_scope': resolved_scope,

                # TODO: should we add some marker about inlined in here?
                #       could be useful when traversing trace and seeing a node is from_cache
                #       and you wouldn't expect it to be from_cache, could help to know it was inlined
                #       which could explain why from cache
            }

            return iso_key

        # can just start at empty no matter where in graph you really are since if there's a cycle it will catch it anyway
        starting_cycle_check_stack = ()
        build_graph_helper(root_call_key, starting_call_path, starting_cycle_check_stack, error_catched)
        return graph

    def _resolve_cache_keys(self, iso_graph):
        graph = iso_graph
        order = TopologicalSorter(graph.edges).static_order()
        for node in order:
            dep_nodes = graph.edges[node]

            dep_cache_keys = []
            hash_to_cache_key_map = {}
            for dep_node in dep_nodes:
                dep_cache_key = graph.nodes[dep_node]['cache_key']
                dep_hash = graph.nodes[dep_node]['iso_key'].hash
                dep_cache_keys.append(dep_cache_key)
                hash_to_cache_key_map[dep_hash] = dep_cache_key

            # cache key is a combination of direct dependencies' cache_key + node hash.
            # don't need scoped hash, because now the graph is fully realized, we just want to
            # identify nodes based on their content, if two nodes have the same content in two
            # different scopes, then they are the same and should be merged together.
            # note: one nuance to this is that call_keys of merged nodes might be slightly different
            #       either because one call_key had the call_path of a partially realized scope
            #       or a fully realized scope that didn't end up affecting the underlying provider.
            #       so we should keep track of all the unique call_keys merged together, so that
            #       we can look up results by any of the possible call_keys
            #       (same goes for iso_key.scoped_hash, but don't really need that for anything)
            cache_key = deterministic_hash([
                graph.nodes[node]['iso_key'].hash,
                sorted(dep_cache_keys)
            ])
            graph.nodes[node]['cache_key'] = cache_key

            # resolve expected types to be referenced by cache key instead of hash
            deps_expected_types_by_cache_key = {}
            for dep_hash, _type in graph.nodes[node]['deps_expected_types_by_hash'].items():
                dep_ck = hash_to_cache_key_map[dep_hash]
                deps_expected_types_by_cache_key[dep_ck] = _type

            graph.nodes[node]['deps_expected_types_by_cache_key'] = deps_expected_types_by_cache_key
            del graph.nodes[node]['deps_expected_types_by_hash']

        # graph merging happens below, need to collect all call_keys for each merged node since
        # there's different call_keys the node can be called with from different providers
        new_edges = {}
        new_nodes = {}
        cache_key_to_call_keys = defaultdict(set)
        for old_node in graph.nodes.keys():
            node_data = graph.nodes[old_node]
            new_node = node_data['cache_key']
            cache_key_to_call_keys[new_node].update(node_data['call_keys'])
            new_deps = {graph.nodes[old_dep]['cache_key'] for old_dep in graph.edges[old_node]}

            new_nodes[new_node] = node_data
            new_edges[new_node] = new_deps

        for node, node_data in new_nodes.items():
            node_data['call_keys'] = list(cache_key_to_call_keys[node])
        return Graph(
            engine_id=graph.engine_id,
            graph_build_id=graph.graph_build_id,
            nodes=new_nodes,
            edges=new_edges,
        )

    def _mark_error_catched_nodes(self, graph):
        """
        all upstream nodes of an ngn.catch'ed node should also be marked as error_catched
        so that error sentinels flow down to the catch
        """
        catched_nodes = []
        for node, node_data in graph.nodes.items():
            if node_data['error_catched']:
                catched_nodes.append(node)

        catched_node_ancestors = set()
        for node in catched_nodes:
            catched_node_ancestors.update(graph.ancestors(node))

        for node in catched_node_ancestors:
            graph.nodes[node]['error_catched'] = True

