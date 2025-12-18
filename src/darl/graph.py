from dataclasses import dataclass, field
from typing import Tuple, Any, Dict, Set, Optional

from darl.helpers import deterministic_hash


class GraphCycleError(Exception):
    pass


@dataclass
class Graph:  # directed acyclic graph really
    engine_id: str
    graph_build_id: str
    nodes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    edges: Dict[str, Set[str]] = field(default_factory=dict)
    trimmed: bool = False

    def copy(self):
        return Graph(
            engine_id=self.engine_id,
            graph_build_id=self.graph_build_id,
            nodes=self.nodes.copy(),
            edges=self.edges.copy(),
            trimmed=self.trimmed,
        )

    def ancestors(self, root_node):
        edges = self.edges
        stack = [root_node]

        ancestor_nodes = set()
        while stack:
            node = stack.pop()
            if node in ancestor_nodes:
                continue
            ancestor_nodes.add(node)
            direct_upstream = edges[node]
            stack.extend(direct_upstream)

        ancestor_nodes.remove(root_node)
        return ancestor_nodes

    def root_nodes(self):
        all_nodes = set(self.nodes.keys())
        dependent_nodes = set(dep for deps in self.edges.values() for dep in deps)
        return all_nodes - dependent_nodes

    def root_node(self):
        root_nodes = self.root_nodes()
        if len(root_nodes) != 1:
            raise ValueError('Only one root node expected, found more than one')
        else:
            return root_nodes.pop()

    def subset(self, nodes):
        nodes = set(nodes)
        extra_nodes = nodes - self.nodes.keys()
        if extra_nodes:
            raise ValueError(f'Attempted to subset graph with following nodes that don\'t exist: {extra_nodes}')
        new_nodes = {k: v for k, v in self.nodes.items() if k in nodes}
        new_edges = {}
        for node, deps in self.edges.items():
            if node in nodes:
                new_deps = deps & nodes  # keep in mind if new_deps != deps then running graph won't work
                new_edges[node] = new_deps
        return Graph(
            engine_id=self.engine_id,
            graph_build_id=self.graph_build_id,
            nodes=new_nodes,
            edges=new_edges,
            trimmed=self.trimmed,
        )

    def subset_upstream_of_node(self, node):
        nodes = self.ancestors(node) | {node}
        return self.subset(nodes)


@dataclass
class NodeKey:
    provider_hash: str
    provider_kwargs_hash: str
    scope_relevant_call_path: Tuple[str, ...]
    value_hash_override: str = None

    def __hash__(self):
        return hash(self.scoped_hash)

    @property
    def scoped_hash(self):
        # don't override with value_hash_override, since need scoped_hash during graph build during crawling
        return deterministic_hash(
            (self.provider_hash, self.provider_kwargs_hash, self.scope_relevant_call_path)
        )

    @property
    def hash(self):
        if self.value_hash_override is not None:
            return self.value_hash_override
        else:
            return deterministic_hash(
                (self.provider_hash, self.provider_kwargs_hash)
            )


def trim_graph(graph, cache, force_compute_nodes: Optional[set] = None):
    if force_compute_nodes is None:
        force_compute_nodes = set()
    in_caches = cache.bulk_contains(f'res:{k}' for k in graph.nodes.keys())
    nodes_to_compute = {k for k, ic in zip(graph.nodes.keys(), in_caches) if ((not ic) or (k in force_compute_nodes))}

    # TODO: this logic is a little confusing since it was retroactively reworked, maybe clean it up a bit
    root_nodes = graph.root_nodes()
    all_root_nodes_cached = all((root_node not in nodes_to_compute) for root_node in root_nodes)

    if all_root_nodes_cached:
        nodes_from_cache = root_nodes
        nodes_to_keep = root_nodes
        nodes_to_compute = set()
    else:
        direct_upstreams = [graph.edges[n] for n in nodes_to_compute]
        direct_upstreams = {x for xs in direct_upstreams for x in xs}
        nodes_from_cache = direct_upstreams - nodes_to_compute
        nodes_to_keep = nodes_to_compute | nodes_from_cache

    cached_result_metas = cache.bulk_get(f'res_meta:{k}' for k in nodes_from_cache)

    new_edges = {k: v for k, v in graph.edges.items() if k in nodes_to_compute}
    for n in nodes_to_keep:
        new_edges.setdefault(n, set())
    new_nodes = {k: v for k, v in graph.nodes.items() if k in nodes_to_keep}
    for n, res in zip(nodes_from_cache, cached_result_metas):
        new_nodes[n] = new_nodes[n].copy()
        new_nodes[n]['computed_from_graph_build_id'] = res.graph_build_id
        new_nodes[n]['from_cache_only'] = True

    trimmed_graph = Graph(
        engine_id=graph.engine_id,
        graph_build_id=graph.graph_build_id,
        nodes=new_nodes,
        edges=new_edges,
        trimmed=True,
    )

    # due to items potentially being removed from cache the nodes_to_compute could end up with islands (to compute
    # node disconnected from roots by from_cache nodes). we don't want to compute any of these islands as their results
    # will be superseded by downstream cached nodes. this will ensure that our graph is all connected to the original
    # roots (see test_dont_compute_islands)
    # TODO: maybe do this trimming sooner. since extra processing needs to be done for nodes that will be tossed
    connected_to_roots = set()
    for root_node in graph.root_nodes():
        connected_to_roots |= (trimmed_graph.ancestors(root_node) | {root_node})

    return trimmed_graph.subset(connected_to_roots)
