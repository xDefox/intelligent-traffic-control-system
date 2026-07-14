# backend/services/graph_manager.py
import networkx as nx
from typing import Dict, List, Optional, Tuple
from backend.core.road_config import ROADS


def _parse_links() -> List[Tuple[str, str]]:
    """Преобразовать строчки вида 'a -> b' в пары (a, b)"""
    result = []
    for link_str in ROADS.get("links", []):
        parts = link_str.split("->")
        if len(parts) == 2:
            src = parts[0].strip()
            dst = parts[1].strip()
            if src and dst:
                result.append((src, dst))
    return result


def _get_all_lane_ids() -> List[str]:
    """Собрать все возможные lane_id из конфига перекрёстков и связей"""
    lane_ids = set()

    # Из фаз перекрёстков (теперь approaches — список строк, либо dict с ключом "approaches")
    for inter_id, config in ROADS.items():
        if inter_id == "links":
            continue
        for phase_name, phase_data in config.get("phases", {}).items():
            if isinstance(phase_data, dict):
                approaches = phase_data.get("approaches", [])
            else:
                approaches = phase_data  # старый формат (список строк)
            for app in approaches:
                lane_ids.add(f"{inter_id}_{app}")

    # Из связей
    for src, dst in _parse_links():
        lane_ids.add(src)
        lane_ids.add(dst)

    return sorted(lane_ids)


def _resolve_intersection_and_approach(lane_id: str) -> Tuple[str, str]:
    """Из lane_id вида 'intersection_1_approach_0' или 'lane_intersection_1_approach_0' 
    достать (intersection_id, approach)"""
    # Убираем префикс "lane_" если есть
    if lane_id.startswith("lane_"):
        lane_id = lane_id[5:]  # Убираем "lane_"
    
    # Ищем последнее _approach_
    idx = lane_id.find("_approach_")
    if idx == -1:
        return ("unknown", lane_id)
    inter = lane_id[:idx]
    approach = lane_id[idx + 1:]  # "approach_0"
    return (inter, approach)


class CityTrafficGraph:
    """
    Граф дорожной сети.
    
    Узлы: (intersection_id, approach) например ("intersection_1", "approach_0")
    Рёбра: связи между подходами разных перекрёстков
    Пул полос: lane_id -> {car_count, avg_speed, congestion_index, max_capacity}
    """

    def __init__(self):
        self.graph = nx.DiGraph()
        self.lane_pool: Dict[str, dict] = {}
        self.intersection_phases: Dict[str, dict] = {}
        # Кэш upstream/downstream — топология графа статична
        self._upstream_cache: Dict[str, Dict[str, List[str]]] = {}
        self._downstream_cache: Dict[str, Dict[str, List[str]]] = {}
        self._build_from_config()

    def _build_from_config(self):
        """Построить граф из ROADS"""
        self.graph.clear()
        self.lane_pool.clear()
        self.intersection_phases.clear()
        self._upstream_cache.clear()
        self._downstream_cache.clear()

        # 1. Запоминаем фазы для каждого перекрёстка
        for inter_id, config in ROADS.items():
            if inter_id == "links":
                continue
            self.intersection_phases[inter_id] = config.get("phases", {})

        # 2. Собираем все lane_id
        all_lanes = _get_all_lane_ids()

        for lane_id in all_lanes:
            inter_id, approach = _resolve_intersection_and_approach(lane_id)

            # Добавляем узел
            node = (inter_id, approach)
            self.graph.add_node(node, intersection_id=inter_id, approach=approach)

            # Добавляем в пул полос
            self.lane_pool[lane_id] = {
                "intersection_id": inter_id,
                "approach": approach,
                "car_count": 0,
                "avg_speed": 0.0,
                "congestion_index": 0.0,
                "max_capacity": 5,
            }

        # 3. Добавляем связи
        for src, dst in _parse_links():
            src_inter, src_app = _resolve_intersection_and_approach(src)
            dst_inter, dst_app = _resolve_intersection_and_approach(dst)
            self.graph.add_edge(
                (src_inter, src_app), (dst_inter, dst_app),
                lane_id=src,
                connected=True
            )

        # 4. Предвычисляем upstream/downstream кэш
        self._precompute_topology_cache()

    # ===================== ОБНОВЛЕНИЕ СОСТОЯНИЯ =====================

    def update_lane_state(self, lane_id: str, car_count: int,
                          avg_speed: float = 0.0, max_capacity: int = 5) -> float:
        """Обновить состояние полосы от камеры. Вернуть congestion_index."""
        if lane_id not in self.lane_pool:
            return 0.0

        pool = self.lane_pool[lane_id]
        pool["car_count"] = car_count
        pool["avg_speed"] = avg_speed
        pool["max_capacity"] = max_capacity

        # congestion_index = car_count / max_capacity (приведено к 0..1)
        if max_capacity > 0:
            pool["congestion_index"] = min(1.0, car_count / max_capacity)
        else:
            pool["congestion_index"] = 0.0

        return pool["congestion_index"]

    def get_lanes_for_intersection(self, intersection_id: str) -> List[dict]:
        """Получить все полосы перекрёстка"""
        return [
            {"lane_id": lid, **data}
            for lid, data in self.lane_pool.items()
            if data["intersection_id"] == intersection_id
        ]

    def _get_approaches_for_phase(self, intersection_id: str, phase_name: str) -> list:
        """Достать список подходов для фазы (из нового формата словаря)"""
        phases = self.intersection_phases.get(intersection_id, {})
        phase_data = phases.get(phase_name, [])
        if isinstance(phase_data, dict):
            return phase_data.get("approaches", [])
        return phase_data if isinstance(phase_data, list) else []

    def get_congestion_for_phase(self, intersection_id: str, phase_name: str) -> float:
        """
        Средняя загруженность для фазы перекрёстка.
        Например, фаза "NS" = ["approach_0", "approach_2"]
        """
        approaches = self._get_approaches_for_phase(intersection_id, phase_name)
        if not approaches:
            return 0.0

        total = 0.0
        count = 0
        for lane_id, data in self.lane_pool.items():
            if data["intersection_id"] == intersection_id and data["approach"] in approaches:
                total += data["congestion_index"]
                count += 1

        return total / count if count > 0 else 0.0

    def get_phase_for_approach(self, intersection_id: str, approach: str) -> Optional[str]:
        """По подходу определить, к какой фазе он относится"""
        phases = self.intersection_phases.get(intersection_id, {})
        for phase_name in phases:
            approaches = self._get_approaches_for_phase(intersection_id, phase_name)
            if approach in approaches:
                return phase_name
        return None

    # ===================== КАСКАДНОЕ УПРАВЛЕНИЕ =====================

    def _precompute_topology_cache(self):
        """Предвычислить upstream/downstream для всех перекрёстков.
        Топология графа статична (не меняется после загрузки конфига),
        поэтому кэш считается один раз в _build_from_config()."""
        for inter_id in self.intersection_phases:
            # Upstream
            upstream_cache = {}
            for lane_id, data in self.lane_pool.items():
                if data["intersection_id"] != inter_id:
                    continue
                approach = data["approach"]
                node = (inter_id, approach)
                for u, v, edge_data in self.graph.in_edges(node, data=True):
                    if u[0] != inter_id:
                        upstream_cache.setdefault(approach, []).append(u[0])
            self._upstream_cache[inter_id] = upstream_cache

            # Downstream
            downstream_cache = {}
            for lane_id, data in self.lane_pool.items():
                if data["intersection_id"] != inter_id:
                    continue
                approach = data["approach"]
                node = (inter_id, approach)
                for u, v, edge_data in self.graph.out_edges(node, data=True):
                    if v[0] != inter_id:
                        downstream_cache.setdefault(approach, []).append(v[0])
            self._downstream_cache[inter_id] = downstream_cache

    def get_upstream_intersections(self, intersection_id: str) -> Dict[str, List[str]]:
        """
        Для каждого подхода перекрёстка найти upstream перекрёстки.
        {approach: [intersection_id, ...]}
        Использует предвычисленный кэш — O(1) вместо обхода графа.
        """
        return self._upstream_cache.get(intersection_id, {}).copy()

    def get_downstream_intersections(self, intersection_id: str) -> Dict[str, List[str]]:
        """Для каждого подхода найти downstream перекрёстки.
        Использует предвычисленный кэш — O(1) вместо обхода графа."""
        return self._downstream_cache.get(intersection_id, {}).copy()

    def calculate_cascade(self) -> List[dict]:
        """
        Проверить все перекрёстки.
        Если подход забит >70% → upstream урезает зелёный.
        Если подход свободен <30% → можно продлить зелёный (зелёная волна).
        """
        commands = []
        processed = set()

        for inter_id in self.intersection_phases:
            for lane_id, data in self.lane_pool.items():
                if data["intersection_id"] != inter_id:
                    continue

                approach = data["approach"]
                congestion = data["congestion_index"]

                if congestion > 0.7:
                    upstream_map = self.get_upstream_intersections(inter_id)
                    if approach in upstream_map:
                        for up_inter in upstream_map[approach]:
                            key = (up_inter, approach, "REDUCE")
                            if key not in processed:
                                commands.append({
                                    "target_intersection": up_inter,
                                    "action": "REDUCE_GREEN",
                                    "approach": approach,
                                    "reason": f"{inter_id}/{approach} congested {congestion:.0%}",
                                    "reduce_green_by": int(5 + congestion * 10),
                                })
                                processed.add(key)

                elif congestion < 0.3:
                    downstream_map = self.get_downstream_intersections(inter_id)
                    if approach in downstream_map:
                        for down_inter in downstream_map[approach]:
                            key = (inter_id, approach, "WAVE")
                            if key not in processed:
                                commands.append({
                                    "target_intersection": inter_id,
                                    "action": "GREEN_WAVE",
                                    "approach": approach,
                                    "reason": f"{down_inter} free, wave possible",
                                    "extend_green_by": 3,
                                })
                                processed.add(key)

        return commands

    def get_full_state(self) -> dict:
        """Полное состояние для UI"""
        state = {}
        for inter_id in self.intersection_phases:
            lanes = self.get_lanes_for_intersection(inter_id)
            avg_cong = sum(l["congestion_index"] for l in lanes) / len(lanes) if lanes else 0
            state[inter_id] = {
                "lanes": lanes,
                "avg_congestion": round(avg_cong, 2),
            }
        return state


# Синглтон
traffic_network = CityTrafficGraph()