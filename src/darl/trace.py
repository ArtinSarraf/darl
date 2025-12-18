from enum import Enum, auto

from darl.execution.sequential import SequentialRunner
from darl.graph import trim_graph
from darl.helpers import NumberedList

from darl.cache import ThroughCache, DictCache


# TODO: how to traverse when run errored out - simulate a "running but not completed" path from root to errored node?
#       is that even really useful?

# TODO: should we have ERROR/ERROR_CAUGHT statuses? this would require getting info from cache as opposed to just
#       graph node_data. we do already get info from cache (e.g. results) but currently these are only on demand,
#       not on every trace instantiation
class ExecutionStatus(Enum):
    COMPUTED = auto()
    FROM_CACHE = auto()


class Trace:
    def __init__(self, graph: 'Graph', cache, node_id=None):
        self.graph = graph
        if node_id is None:
            roots = self.graph.root_nodes()
            if len(roots) != 1:
                raise ValueError('if no node specified, 1 and only 1 root node must exist')
            node_id = list(roots)[0]
        self.node_id = node_id
        self.cache = cache

    def __repr__(self):
        # TODO: maybe pick the proper call_key corresponding to the path that this trace was traversed from
        #       would need to keep track of the call path in the trace as you traverse to do that
        call_key = self.graph.nodes[self.node_id]['call_keys'][0]
        return f'<Trace: {call_key}, {self.execution_status.name}>'

    @property
    def node_data(self):
        return self.graph.nodes[self.node_id]

    @property
    def result(self):
        result_graph_build_id = self.cache.get(f'res_meta:{self.node_id}').graph_build_id
        if (
            result_graph_build_id != self.graph.graph_build_id
            and not (
                self.node_data['from_cache_only']
                and self.node_data['computed_from_graph_build_id'] == result_graph_build_id  # TODO: test this
            )
        ):
            # TODO: maybe add a flag to allow result lookup and only enable it when trace built from a trusted source
            #       that is known to not have the boundary problem described
            raise ValueError('The result for this node is from an unexpected graph build. The cache was either'
                             'repopulated, or you havegone further up this branch than the point at which a cached '
                             ' result was returned. Likely due to using the trace.full_graph() feature to traverse '
                             ' the graph beyond the boundaries of nodes that were pulled from cache.')
        else:
            return self.cache.get(f'res:{self.node_id}').result

    @property
    def upstreams(self):
        ups = []
        for upstream_node in self.graph.edges[self.node_id]:
            ups.append(Trace(graph=self.graph, cache=self.cache, node_id=upstream_node))
        return NumberedList(ups)

    ups = upstreams

    @property
    def downstreams(self):
        downs = []
        for node, upstream_nodes in self.graph.edges.items():
            if self.node_id in upstream_nodes:
                downs.append(Trace(graph=self.graph, cache=self.cache, node_id=node))
        return NumberedList(downs)

    downs = downstreams

    @property
    def computed_node(self):
        """
        maybe misnomer?
        returns the trace for the corresponding computed node. if this is already that trace it will return itself
        """
        if self.graph.nodes[self.node_id]['from_cache_only']:
            graph_build_id = self.graph.nodes[self.node_id]['computed_from_graph_build_id']
            graph = self.cache.get(f'executed_graph:{graph_build_id}').graph
            return Trace(graph, self.cache, self.node_id)
        else:
            return self

    @property
    def duration_sec(self):
        return self.cache.get(f'res_meta:{self.node_id}').duration_sec

    def full_graph(self):
         full_graph = self.cache.get(f'full_graph:{self.graph.graph_build_id}').graph
         return Trace(full_graph, self.cache, self.node_id)

    @property
    def execution_status(self):
        if self.node_data['from_cache_only']:
            return ExecutionStatus.FROM_CACHE
        else:
            return ExecutionStatus.COMPUTED

    @property
    def computed(self):
        return self.execution_status == ExecutionStatus.COMPUTED

    @property
    def from_cache(self):
        return self.execution_status == ExecutionStatus.FROM_CACHE

    def replay(self):
        # note: this graph is already likely a trimmed execution graph itself
        # TODO: how to behave when self.graph is the result of a .full_graph call?
        graph = self.graph.subset_upstream_of_node(node=self.node_id)
        trimmed_graph = trim_graph(graph, self.cache, force_compute_nodes={self.node_id})

        # the below logic is to ensure results pulled from cache are either from the original run, or are the
        # same result pulled from cache in the original run?
        # TODO: test the failure cases
        for node, node_data in trimmed_graph.nodes.items():
            original_node_data = graph.nodes[node]
            if original_node_data['from_cache_only']:
                # TODO: these should not be asserts, should be actual errors, fix
                assert node_data['from_cache_only']  # don't think there's any mechanism for this to not be from_cache_only too
                # TODO: should we allow a way to keep an index of old cached results so we can reference them from
                #       trace even if they've been deleted from "live cache" could either do
                #       (1) store results by (cache_key, graph_build_id) then have a map of cache_key -> latest graph_build_id
                #       (2) store the live result the same way we are now and store suggestion (1) (except no map needed)
                assert node_data['computed_from_graph_build_id'] == original_node_data['computed_from_graph_build_id']
            elif node_data['from_cache_only']:
                assert node_data['computed_from_graph_build_id'] == graph.graph_build_id

        # do this combo cache thing so that replays don't unexpectedly populate cache
        # TODO: (1) do we want it to populate cache?
        #       (2) should we split up the caching and data passing mechanism so that we don't have to rely on cache
        #           manipulation to prevent this?
        #       (3) currently can run deeper than just the replayed node if upstreams not in cache, do we want to allow that?
        cache = ThroughCache(DictCache(), self.cache, read_through=True, write_through=False, copy_on_read=False)
        return SequentialRunner().run(trimmed_graph, cache)

    @classmethod
    def from_graph_build_id(cls, graph_build_id, cache, node_id=None):
        graph = cache.get(f'executed_graph:{graph_build_id}').graph
        if node_id is None:
            root_nodes = graph.root_nodes()
            if len(root_nodes) != 1:
                raise ValueError('expected 1 and only 1 root node')
            node_id = root_nodes[0]
        return cls(graph, cache, node_id)



