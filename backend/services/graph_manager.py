# backend/services/graph_manager.py
import networkx as nx


class CityTrafficGraph:
    def __init__(self):
        self.graph = nx.DiGraph()
        self._init_network_structure()

    def _init_network_structure(self):
        """
        Строим связи между перекрёстками.
        Дорога соединяет ВЫХОД из intersection_1 со ВХОДОМ в intersection_2.
        """
        # Направление от 1-го ко 2-му перекрёстку (например, северный выезд)
        self.graph.add_edge("intersection_1", "intersection_2", lane_id="lane_north", weight=0.0)
        # Обратное направление (южный въезд)
        self.graph.add_edge("intersection_2", "intersection_1", lane_id="lane_south", weight=0.0)

    def update_lane_congestion(self, intersection_id: str, lane_id: str, car_count: int):
        """
        Конвертируем количество машин в индекс затора (weight).
        Допустим, 10 машин — это глухой затор (1.0).
        """
        congestion_index = min(car_count / 10.0, 1.0)

        # Ищем нужное ребро графа
        for u, v, data in self.graph.edges(data=True):
            if u == intersection_id and data.get("lane_id") == lane_id:
                self.graph[u][v]['weight'] = congestion_index
                return congestion_index
        return 0.0

    def get_cascade_commands(self, critical_node: str) -> dict:
        """
        Магия координации: если на текущем узле затор,
        отдаем команду узлам-соседям придержать машины на входе.
        """
        commands = {}
        # Смотрим, кто вливает трафик в критический узел
        incoming_edges = self.graph.in_edges(critical_node, data=True)

        for source_node, _, data in incoming_edges:
            weight = self.graph[source_node][critical_node]['weight']
            # Если соседний участок забит более чем на 70%
            if weight > 0.7:
                commands[source_node] = {
                    "action": "HOLD_TRAFFIC",
                    "trigger_by": critical_node,
                    "target_lane": data.get("lane_id"),
                    "reduce_green_phase": 8  # Урезаем зеленый соседу на 8 секунд
                }
        return commands


traffic_network = CityTrafficGraph()