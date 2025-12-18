from graphlib import TopologicalSorter
from typing import TYPE_CHECKING

from darl.execution.base import Runner, run_node

if TYPE_CHECKING:
    from darl.cache import Cache
    from darl.graph import Graph


class SequentialRunner(Runner):

    def run(self, graph: 'Graph', cache: 'Cache'):
        order = TopologicalSorter(graph.edges).static_order()
        for node in order:
            run_node(node, graph, cache)
        result = cache.get(f'res:{node}').result
        return result


