"""
This is a limited implementation of graph caching - currently only handles pulling full graph,
matching on root invocation.

TODO: implement pulling subgraphs when building a superset graph of a previously cached graph
TODO: handle services with same provider hash (not handled since we just choose first call key for a given node for dep_services)
TODO: determine how much time this adds
TODO: handle anon services
"""
from collections import defaultdict
from typing import TYPE_CHECKING

from darl.graph_build.base import new_graph_build_id
from darl.helpers import deterministic_hash
from darl.provider import hash_provider

if TYPE_CHECKING:
    from darl.graph import Graph


def cache_dep_indexes(graph: 'Graph', cache, ngn):
    """
    dep set used to pull graph from cache
    """
    root_node = graph.root_node()
    call_key_hash = hash(graph.nodes[root_node]['call_keys'][0])
    service_to_provider_hash = {}
    value_hashed_call_key_to_hash = {}

    # use this to make sure that an ngn pulling this graph from cache has a service updated in the same scopes
    # as this one. this is to protect against an previous issue where (1) a non scope updated run caches a graph
    # (2) a scoped update is made (3) the service is recorded in the dep_index as coming from scope=() (4) when
    # pulling the graph from cache, the dep_index checks the new scope updated ngn for the service at scope=() which
    # still exists, even though in the new run it would end up hitting scope=(...) (5) get a false-positive hit and pull
    # the wrong graph from cache.
    # by recording this mapping we can make sure that the service is updated in the same scopes so we know that
    # we'll always pull the hash for the provider in the right scope. NOTE: this can be overly sensitive and result
    # in false-negatives, but that's ok, can live with that.
    # an alternative more exact solution (but harder to implement) would be to record the callkey for each service
    # (which would include the call path) and resolve the callpath against the new ngn service mapping to get the
    # exact provider to hash, this way even if the scoped updates don't match up exactly, if the provider hashes
    # still match you can get a cache hit
    service_to_scopes_mapping = defaultdict(list)
    for scope, mapping in ngn.service_mapping.service_mapping_by_scope.items():
        for service_name in mapping.keys():
            service_to_scopes_mapping[service_name].append(scope)

    for node, node_data in graph.nodes.items():
        call_key = graph.nodes[node]['call_keys'][0]
        service_name = call_key.service_name  # TODO: see if this will get messed up by different service names sharing a provider
        scope = graph.nodes[node]['resolved_scope']

        if node_data['value_hashed']:
            value_hashed_call_key_to_hash[(call_key, scope)] = node_data['iso_key'].value_hash_override
        else:
            service_to_provider_hash[(service_name, scope)] = node_data['iso_key'].provider_hash

    try:
        dep_index = cache.get(f'dep_index:{call_key_hash}')  # TODO: add default get
    except KeyError:
        dep_index = []
    dep_index.append((service_to_provider_hash, value_hashed_call_key_to_hash, service_to_scopes_mapping, graph.graph_build_id))
    cache.set(f'dep_index:{call_key_hash}', dep_index)

    # write call_key index. naive initial solution for identifying sub call keys in graph to pull subgraphs from
    # a super set graph
    try:
        sub_call_index = cache.get('sub_call_index')
    except KeyError:
        sub_call_index = {}

    for node, node_data in graph.nodes.items():
        for sub_call_key in node_data['call_keys']:
            sub_call_key_hash = hash(sub_call_key)
            if sub_call_key_hash not in sub_call_index:
                sub_call_index[sub_call_key_hash] = set()
            sub_call_index[sub_call_key_hash].add(call_key_hash)

    cache.set('sub_call_index', sub_call_index)


def _match_dep_index(ngn, dep_index, service_to_scopes_mapping):
    service_to_provider_hash, value_hashed_call_key_to_hash, service_to_scopes_mapping_to_match, graph_build_id = dep_index

    for (service_name, scope), hash_to_match in service_to_provider_hash.items():
        if service_to_scopes_mapping[service_name] != service_to_scopes_mapping_to_match[service_name]:
            return False
        try:
            provider = ngn.service_mapping.service_mapping_by_scope[scope][service_name]
        except KeyError:
            return False
        if hash_provider(provider) != hash_to_match:
            return False

    for (call_key, scope), hash_to_match in value_hashed_call_key_to_hash.items():
        if service_to_scopes_mapping[call_key.service_name] != service_to_scopes_mapping_to_match[call_key.service_name]:
            return False
        try:
            provider = ngn.service_mapping.service_mapping_by_scope[scope][call_key.service_name]
        except KeyError:
            return False
        if not getattr(provider, 'value_hashed', False):  # must also be value hashed in the tested ngn
            return False
        val = ngn._run_call_internal(call_key, graph_caching=False)  # TODO: make sure this handles scopes properly
        if deterministic_hash(val) != hash_to_match:
            return None

    return True


# TODO: lazy cached evaluation of provider hash on service mapping object, can be reused in graph build too
# TODO: for scoped hash compat, store scope key from service_mapping on graph for quick lookup?
def pull_graph_from_cache_using_dep_indexes(call_key, ngn, cache):
    ngn = ngn.clone()  # why is this here? I don't remember. all tests pass if commented out as of right now
    # TODO: should we replace graph builder and executor with local versions

    # add superset graph dep_indexes too, in case call key is a subcall in an existing cached graph
    # part of the naive solution for subgraph cache pulls
    # how expensive is this naive solution subgraph finding operation?
    try:
        sub_call_index = cache.get('sub_call_index')
    except KeyError:
        sub_call_index = {}
    call_key_hash = hash(call_key)
    superset_call_key_hashes = list(sub_call_index.get(call_key_hash, set()))
    call_key_hashes_to_check = [call_key_hash] + superset_call_key_hashes

    dep_indexes = []
    for ck_hash in call_key_hashes_to_check:
        try:
            dep_indexes_for_call_key = cache.get(f'dep_index:{ck_hash}')
            dep_indexes.extend(dep_indexes_for_call_key)
        except KeyError:
            pass
    # subset logic to here

    service_to_scopes_mapping = defaultdict(list)
    for scope, mapping in ngn.service_mapping.service_mapping_by_scope.items():
        for service_name in mapping.keys():
            service_to_scopes_mapping[service_name].append(scope)

    for dep_index in dep_indexes:
        *_, graph_build_id = dep_index
        match = _match_dep_index(ngn, dep_index, service_to_scopes_mapping)
        if match:
            graph: 'Graph' = cache.get(f'full_graph:{graph_build_id}').graph
            graph.graph_build_id = new_graph_build_id()
            graph.engine_id = ngn.engine_id
            break
    else:
        return None

    # subset graph in case call key is a sub graph in superset graph pulled from cache
    node_found = False
    for node, node_data in graph.nodes.items():
        for ck in node_data['call_keys']:
            if ck == call_key:
                node_found = True
                break
        if node_found:
            break
    graph = graph.subset(graph.ancestors(node) | {node})
    return graph


