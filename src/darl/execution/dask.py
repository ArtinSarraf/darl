"""
TODO: not stdlib so should not be in core?
"""
import time
from graphlib import TopologicalSorter

from darl.execution.base import Runner, run_node


def _prefix_node(node, graph):
    """
    used to prefix service name to node, this is purely so that dask UI will display the service name
    """
    service_name = graph.nodes[node]['call_keys'][0].service_name
    return f'{service_name}-{node}'


def run_dask_node(node, graph, cache, dep_refs):  # dep_refs just used for dask dep wiring
    return run_node(node, graph, cache)


def create_dsk(client, graph, cache):
    dsk = {}
    order = TopologicalSorter(graph.edges).static_order()

    graph_ref = client.scatter(graph, broadcast=True)  # needs to be scattered
    cache_ref = client.scatter(cache, broadcast=True)

    for node in order:
        dep_nodes = graph.edges[node]
        dep_refs = [_prefix_node(n, graph) for n in dep_nodes]
        dsk[_prefix_node(node, graph)] = (run_dask_node, node, graph_ref, cache_ref, dep_refs)
    return dsk


class DaskRunner(Runner):

    def __init__(self, client):
        self.client = client

    def run(self, graph: 'Graph', cache: 'Cache'):
        # TODO: only works with remote caches, should we do a check here?

        # in back to back dask runs, get some dask error (lost dependencies) if no sleep here, not sure why such a tiny sleep matters
        # e.g. test_inline_nested
        time.sleep(0.01)
        dsk = create_dsk(self.client, graph, cache)
        root_node, = graph.root_nodes()
        # TODO: on first client.submit/get/etc task stream does not populate, make sure primed so task stream populates
        self.client.get(dsk, [_prefix_node(root_node, graph)])
        return cache.get(f'res:{root_node}').result

