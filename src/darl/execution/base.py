import logging
import time
from typing import TYPE_CHECKING

from darl.provider import run_provider
from darl.cache import Cache, CacheEntryResult, CacheEntryResultMeta

if TYPE_CHECKING:
    from darl.graph import Graph


LOGGER = logging.getLogger(__name__)


class Runner:
    def run(self, graph: 'Graph', cache: 'Cache'):
        raise NotImplementedError


def create_dep_results(graph, cache, dep_nodes):
    dep_results = {}
    for dep_node in dep_nodes:
        dep_node_data = graph.nodes[dep_node]

        # ensure data is coming from expected runs
        # TODO: test this, I think this can only happen from cross process/thread race conditions?
        #       could happen from trace replays, but checks in place earlier to catch this already
        dep_result_graph_build_id = cache.get(f'res_meta:{dep_node}').graph_build_id
        if dep_node_data['from_cache_only']:
            expected_graph_build_id = dep_node_data['computed_from_graph_build_id']
        else:
            # in this case if you know you're not going through a shared cache to pass intra-run data
            # around, you don't have to worry about it coming from wrong cached result
            expected_graph_build_id = graph.graph_build_id
        if dep_result_graph_build_id != expected_graph_build_id:
            raise ValueError('Cached result from unexpected graph build id, run corrupted')

        call_keys = dep_node_data['call_keys']
        dep_res = cache.get(f'res:{dep_node}').result
        # do this because different call_key call_path configurations could be
        # used to reach the same node in the graph during a scoped graph build
        for call_key in call_keys:
            dep_results[call_key] = dep_res
    return dep_results


def create_dep_service_mapping(graph, dep_nodes):
    """
    Passed to mock engine to lookup provider for kwargs resolving. pass this in to mock engine
    instead of the actual ngn.service_mapping, since this way more stateless/robust. just grab the exact
    provider from the already built graph
    """
    dep_service_mapping = {}
    for dep_node in dep_nodes:
        # do this for when different services have the same provider under the hood, since they would
        # show up as difference call keys under the same node.
        for call_key in graph.nodes[dep_node]['call_keys']:
            dep_service_mapping[call_key.service_name] = graph.nodes[dep_node]['provider']
    return dep_service_mapping


def run_node(node, graph, cache):
    node_data = graph.nodes[node]
    dep_nodes = graph.edges[node]

    if node_data['from_cache_only']:
        LOGGER.info(f"Pulled from cache {node_data['call_keys'][0]}")
        return

    LOGGER.info(f"Running {node_data['call_keys'][0]}")

    dep_results = create_dep_results(graph, cache, dep_nodes)
    dep_service_mapping = create_dep_service_mapping(graph, dep_nodes)

    start = time.time()
    result = run_provider(
        # TODO: should I just pass in the node_data at this point? see if that fits when doing dask execution
        node_data['provider'],
        node_data['scope_call_path'],
        dep_results,
        dep_service_mapping,
        node_data['iso_key'].hash,
        # used for iter conflict avoidance. should this be scoped hash? needs to match with graph build get_deps
        node_data['cache_key'],  # this and graph build id used for displaying extra context for error replay
        graph.graph_build_id,
        node_data['error_catched'],
        node_data['catches_errors'],
        **node_data['call_keys'][0].kwargs
    )
    duration_sec = time.time() - start
    # TODO: should we enforce return type matches type hint here when ngn.enforce_types=True?
    #       yes, but need to figure out how to check custom types first
    #       maybe to start if type is a string (e.g. ngn.type['some thing']) ignore, otherwise do a isinstance check?

    cache_entry_result = CacheEntryResult(
        result=result,
    )
    cache_entry_result_meta = CacheEntryResultMeta(
        graph_build_id=graph.graph_build_id,
        duration_sec=duration_sec,
    )

    # TODO: should we separate data passing mechanism from caching mechanism, right now one and the same
    #       cons: more machinery to manage
    #       pros: potentially faster execution, eliminates race conditions, where cache gets overwritten by another run
    #             and a node tries to pull a dep result from another run (still have the problem of overwriting results
    #             in cache though)
    # these two sets should be atomic
    cache.set(f'res:{node}', cache_entry_result)
    cache.set(f'res_meta:{node}', cache_entry_result_meta)

