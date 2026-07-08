import networkx as nx


class CityTrafficGraph:
    def __init__(self):
        self.graph = nx.DiGraph()
        self._init_dubai_avenue_structure()

    def _init_dubai_avenue_structure(self):
        # Инициализируем геометрию: Перекресток_А -> Ребро(Полоса) -> Перекресток_Б
        # В Т-образном перекрестке регистрируем связи с соседними (гипотетическими) узлами
        self.graph.add_edge("intersection_A", "intersection_B", lane_id="lane_A_B", weight=1.0)
        self.graph.add_edge("intersection_B", "intersection_C", lane_id="lane_B_C", weight=1.0)
        # Обратное направление для каскадного упреждения назад
        self.graph.add_edge("intersection_B", "intersection_A", lane_id="lane_B_A", weight=1.0)

    def update_edge_weight(self, lane_id: str, congestion_index: float):
        # Динамически меняем вес ребра на основе данных из Redis
        for u, v, data in self.graph.edges(data=True):
            if data.get("lane_id") == lane_id:
                self.graph[u][v]['weight'] = congestion_index
                break

    def get_cascade_commands(self, critical_node: str) -> dict:
        """
        Концепция упреждения назад: если на узел Б растет затор,
        находим входящие ребра и готовим команду притормозить поток для узла А.
        """
        commands = {}
        incoming_edges = self.graph.in_edges(critical_node, data=True)

        for source_node, _, data in incoming_edges:
            # Если вес текущего участка критический, даем команду "Каскад" назад
            if self.graph[source_node][critical_node]['weight'] > 0.7:
                commands[source_node] = {
                    "action": "HOLD_TRAFFIC",
                    "target_lane": data.get("lane_id"),
                    "reduce_green_phase": 10  # уменьшить зелёный на 10 сек
                }
        return commands


traffic_network = CityTrafficGraph()