from uuid import uuid4

from darl.graph import Graph


def new_graph_build_id():
    return str(uuid4())


class Builder:
    def build(self, ngn, root_call_key, starting_call_path=(), no_value_hash=False, error_catched=False):
        graph_build_id = new_graph_build_id()
        iso_graph = Graph(
            engine_id=ngn.engine_id,
            graph_build_id=graph_build_id,
        )
        iso_graph = self._build_iso_graph(
            iso_graph=iso_graph,
            ngn=ngn,
            root_call_key=root_call_key,
            starting_call_path=starting_call_path,
            no_value_hash=no_value_hash,
            error_catched=error_catched,
        )
        graph = self._resolve_cache_keys(iso_graph)
        self._mark_error_catched_nodes(graph)
        return graph

    def _build_iso_graph(self, iso_graph, ngn, root_call_key, starting_call_path=(), no_value_hash=False, error_catched=False) -> 'Graph':
        raise NotImplementedError

    def _resolve_cache_keys(self, iso_graph: 'Graph'):
        raise NotImplementedError

    def _mark_error_catched_nodes(self, graph: 'Graph'):
        raise NotImplementedError
