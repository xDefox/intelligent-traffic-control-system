# backend/services/graph_manager.py
import asyncio
import networkx as nx
from typing import Dict, List, Optional, Tuple, Set
from backend.core import road_config


# ---------------------------------------------------------------------------
# Утилиты разбора и генерации топологии
# ---------------------------------------------------------------------------

def _parse_links() -> List[Tuple[str, str]]:
    """Преобразовать строчки вида 'a -> b' в пары (a, b)"""
    result = []
    for link_str in road_config.get_links():
        parts = link_str.split("->")
        if len(parts) == 2:
            src = parts[0].strip()
            dst = parts[1].strip()
            if src and dst:
                result.append((src, dst))
    return result


def _resolve_intersection_and_approach(lane_id: str) -> Tuple[str, str]:
    """Из lane_id вида 'intersection_1_approach_0' или 'lane_intersection_1_approach_0'
    достать (intersection_id, approach)"""
    if lane_id.startswith("lane_"):
        lane_id = lane_id[5:]  # Убираем "lane_"

    idx = lane_id.find("_approach_")
    if idx == -1:
        return ("unknown", lane_id)
    inter = lane_id[:idx]
    approach = lane_id[idx + 1:]  # "approach_0"
    return (inter, approach)


def _axis_for_approach(approach_idx: int) -> str:
    """Ось подхода: 0,1 — EW (восток-запад / X), 2,3 — NS (север-юг / Z)."""
    return "EW" if approach_idx in (0, 1) else "NS"


def generate_phases(approach_indices: Set[int]) -> Dict[str, dict]:
    """
    СГЕНЕРИРОВАТЬ ФАЗЫ из набора подходов, присутствующих у перекрёстка.

    Подходы группируются по осям (как в реальном светофоре: вся ось
    переключается одновременно). Это корректно работает для ЛЮБОГО
    числа камер от 1 до 4:
      - 1 камера (approach_0)        -> 1 фаза  (EW)
      - 2 камеры (approach_0,1)      -> 1 фаза  (EW, оба направления)
      - 3 камеры (approach_0,1,2)    -> 2 фазы  (EW + NS)
      - 4 камеры (approach_0,1,2,3)  -> 2 фазы  (EW + NS)
    """
    ew = sorted(f"approach_{i}" for i in approach_indices if i in (0, 1))
    ns = sorted(f"approach_{i}" for i in approach_indices if i in (2, 3))

    phases: Dict[str, dict] = {}
    if ew:
        phases["EW"] = {"approaches": ew, "min_duration": 5.0, "max_duration": 30.0}
    if ns:
        phases["NS"] = {"approaches": ns, "min_duration": 5.0, "max_duration": 30.0}
    # Если ни одной известной оси нет (нестандартный индекс) — одна фаза на всё.
    if not phases and approach_indices:
        phases["P1"] = {
            "approaches": sorted(f"approach_{i}" for i in approach_indices),
            "min_duration": 5.0,
            "max_duration": 30.0,
        }
    return phases


# ---------------------------------------------------------------------------
# Граф дорожной сети
# ---------------------------------------------------------------------------

class CityTrafficGraph:
    """
    Граф дорожной сети.

    Узлы: (intersection_id, approach) например ("intersection_1", "approach_0")
    Рёбра: физические связи между подходами (из road_config)
    Пул полос: lane_id -> {car_count, avg_speed, congestion_index, max_capacity}

    ВАЖНО: количество дорог/подходов и фазы НЕ заданы жёстко. Они
    регистрируются ДИНАМИЧЕСКИ при поступлении телеметрии (register_approach),
    поэтому перекрёсток может иметь от 1 до 4 камер/дорог.
    """

    def __init__(self):
        self.graph = nx.DiGraph()
        self.lane_pool: Dict[str, dict] = {}
        self.lane_pool_lock = asyncio.Lock()  # блокировка для async-доступа
        self.intersection_max_capacity: Dict[str, int] = {}
        self.intersection_phases: Dict[str, dict] = {}
        self.intersection_approaches: Dict[str, Set[int]] = {}
        # Camera-First Design: реестр камер с метаданными
        self.camera_registry: Dict[str, dict] = {}
        # Кэш upstream/downstream — топология графа динамична
        self._upstream_cache: Dict[str, Dict[str, List[str]]] = {}
        self._downstream_cache: Dict[str, Dict[str, List[str]]] = {}
        # Начинаем с пустого графа, строим из telemetry
        self._build_from_telemetry()

    # ===================== ПОСТРОЕНИЕ ТОПОЛОГИИ =====================

    def _build_static_topology(self):
        """Построить статический граф связей из конфига (физическая топология)."""
        self.graph.clear()
        for src, dst in _parse_links():
            src_inter, src_app = _resolve_intersection_and_approach(src)
            dst_inter, dst_app = _resolve_intersection_and_approach(dst)
            self.graph.add_node((src_inter, src_app))
            self.graph.add_node((dst_inter, dst_app))
            self.graph.add_edge(
                (src_inter, src_app), (dst_inter, dst_app),
                lane_id=src, connected=True,
            )
        self._precompute_topology_cache()

    def _build_from_telemetry(self):
        """Построить граф из зарегистрированных камер (пустой старт)."""
        self.graph.clear()
        # Перестраиваем связи между камерами
        self._build_edges_from_cameras()
        self._precompute_topology_cache()

    def register_camera(self, camera_data: dict):
        """
        Camera-First Design: зарегистрировать камеру с метаданными.
        
        Args:
            camera_data: {
                "camera_id": "intersection_1_approach_0",
                "intersection_id": "intersection_1",
                "direction": "E",  # N, S, E, W
                "world_position": {"x": 105, "y": 1, "z": 0},
                "world_rotation": {"x": 0, "y": 90, "z": 0}
            }
        """
        camera_id = camera_data.get("camera_id")
        if not camera_id:
            return
        
        # Сохраняем метаданные камеры
        self.camera_registry[camera_id] = {
            "intersection_id": camera_data.get("intersection_id"),
            "direction": camera_data.get("direction"),
            "position": camera_data.get("world_position"),
            "rotation": camera_data.get("world_rotation"),
        }
        
        # Перестраиваем граф (топология может измениться)
        self._build_edges_from_cameras()
        self._precompute_topology_cache()

    def _build_edges_from_cameras(self):
        """
        Camera-First Design: автоматически построить связи между камерами.
        
        Алгоритм:
        - Камера A смотрит на E (восток), камера B смотрит на W (запад)
        - Если расстояние между ними < 200м → они соединены дорогой
        """
        # Группируем камеры по перекрёсткам
        cameras_by_inter: Dict[str, List[dict]] = {}
        for cam_id, cam_data in self.camera_registry.items():
            inter_id = cam_data.get("intersection_id")
            if inter_id:
                cameras_by_inter.setdefault(inter_id, []).append({
                    "camera_id": cam_id,
                    "direction": cam_data.get("direction"),
                    "position": cam_data.get("position"),
                })
        
        # Добавляем узлы
        for inter_id, cameras in cameras_by_inter.items():
            for cam in cameras:
                direction = cam.get("direction", "")
                if direction:
                    # Преобразуем N/S/E/W в approach_0/1/2/3
                    approach = self._direction_to_approach(direction)
                    self.graph.add_node((inter_id, approach))
        
        # Ищем связи между камерами
        camera_list = list(self.camera_registry.items())
        for i, (cam_a_id, cam_a_data) in enumerate(camera_list):
            for cam_b_id, cam_b_data in camera_list[i+1:]:
                if self._cameras_are_connected(cam_a_data, cam_b_data):
                    # Создаём ребро в обе стороны
                    inter_a = cam_a_data.get("intersection_id")
                    inter_b = cam_b_data.get("intersection_id")
                    dir_a = cam_a_data.get("direction")
                    dir_b = cam_b_data.get("direction")
                    
                    if inter_a and inter_b and dir_a and dir_b:
                        approach_a = self._direction_to_approach(dir_a)
                        approach_b = self._direction_to_approach(dir_b)
                        
                        # A → B (если A смотрит на B)
                        self.graph.add_edge(
                            (inter_a, approach_a), (inter_b, approach_b),
                            lane_id=cam_a_id, connected=True,
                        )
                        # B → A (если B смотрит на A)
                        self.graph.add_edge(
                            (inter_b, approach_b), (inter_a, approach_a),
                            lane_id=cam_b_id, connected=True,
                        )

    def _direction_to_approach(self, direction: str) -> str:
        """Преобразовать N/S/E/W в approach_0/1/2/3."""
        mapping = {"E": "approach_0", "W": "approach_1", "N": "approach_2", "S": "approach_3"}
        return mapping.get(direction, "approach_0")

    def _cameras_are_connected(self, cam_a: dict, cam_b: dict) -> bool:
        """
        Проверить, соединены ли две камеры дорогой.
        
        Условия:
        1. Они смотрят друг на друга (противоположные направления)
        2. Расстояние между камерами < 200м
        """
        dir_a = cam_a.get("direction")
        dir_b = cam_b.get("direction")
        
        if not dir_a or not dir_b:
            return False
        
        # Проверяем противоположность направлений
        opposite_pairs = {("E", "W"), ("W", "E"), ("N", "S"), ("S", "N")}
        if (dir_a, dir_b) not in opposite_pairs:
            return False
        
        # Проверяем расстояние
        pos_a = cam_a.get("position", {})
        pos_b = cam_b.get("position", {})
        
        if not pos_a or not pos_b:
            return False
        
        dx = pos_a.get("x", 0) - pos_b.get("x", 0)
        dz = pos_a.get("z", 0) - pos_b.get("z", 0)
        distance = (dx*dx + dz*dz) ** 0.5
        
        return distance < 200.0  # 200 метров порог

    def _register_approach(self, intersection_id: str, approach: str):
        """
        ДИНАМИЧЕСКАЯ регистрация подхода из телеметрии.
        Пересобирает фазы перекрёстка и создаёт запись в пуле полос.
        """
        if not approach.startswith("approach_"):
            return
        try:
            idx = int(approach[len("approach_"):])
        except ValueError:
            return

        apps = self.intersection_approaches.setdefault(intersection_id, set())
        if idx in apps:
            return  # уже зарегистрирован

        apps.add(idx)
        # Перегенерируем фазы под актуальный набор подходов (1-4 дороги).
        self.intersection_phases[intersection_id] = generate_phases(apps)

        # Создаём запись полосы, если ещё нет.
        full_lane_id = f"lane_{intersection_id}_{approach}"
        if full_lane_id not in self.lane_pool:
            self.lane_pool[full_lane_id] = {
                "intersection_id": intersection_id,
                "approach": approach,
                "car_count": 0,
                "avg_speed": 0.0,
                "congestion_index": 0.0,
                # Стартуем с 0: вместимость = МАКСИМУМ из виденных машин (см. update_lane_state).
                "max_capacity": 0,
            }
        # Узел графа (для будущих каскадных вычислений).
        self.graph.add_node((intersection_id, approach))

    # ===================== ОБНОВЛЕНИЕ СОСТОЯНИЯ =====================

    def update_lane_state(self, lane_id: str, car_count: int,
                          avg_speed: float = 0.0, max_capacity: int = 5) -> float:
        """
        Обновить состояние полосы от камеры. Вернуть congestion_index.
        При первом появлении lane_id — динамически регистрирует подход.
        """
        inter_id, approach = _resolve_intersection_and_approach(lane_id)
        if inter_id == "unknown":
            return 0.0

        # Динамическая регистрация топологии из данных (без хардкода).
        self._register_approach(inter_id, approach)

        if lane_id not in self.lane_pool:
            return 0.0

        pool = self.lane_pool[lane_id]
        pool["car_count"] = car_count
        pool["avg_speed"] = avg_speed

        # ОБЩИЙ бегущий максимум машин ДЛЯ ВСЕГО перекрёстка (один знаменатель
        # на все камеры/полосы). Никогда не уменьшается (функция max).
        inter_max = self.intersection_max_capacity.get(inter_id, 0)
        if car_count > inter_max:
            inter_max = car_count
            self.intersection_max_capacity[inter_id] = inter_max

        # Вместимость полосы = ОБЩИЙ максимум перекрёстка (для UI и трекбара).
        pool["max_capacity"] = inter_max

        # congestion_index = car_count / общий_максимум_перекрёстка (приведено к 0..1)
        if inter_max > 0:
            pool["congestion_index"] = min(1.0, car_count / inter_max)
        else:
            pool["congestion_index"] = 0.0

        return pool["congestion_index"]

    def get_lanes_for_intersection(self, intersection_id: str) -> List[dict]:
        """Получить все полосы перекрёстка (только зарегистрированные из данных)."""
        return [
            {"lane_id": lid, **data}
            for lid, data in self.lane_pool.items()
            if data["intersection_id"] == intersection_id
        ]

    def _get_approaches_for_phase(self, intersection_id: str, phase_name: str) -> list:
        """Достать список подходов для фазы (из сгенерированного словаря)."""
        phases = self.intersection_phases.get(intersection_id, {})
        phase_data = phases.get(phase_name, [])
        if isinstance(phase_data, dict):
            return phase_data.get("approaches", [])
        return phase_data if isinstance(phase_data, list) else []

    def get_congestion_for_phase(self, intersection_id: str, phase_name: str) -> float:
        """
        Средняя загруженность для фазы перекрёстка.
        Например, фаза "NS" = ["approach_2", "approach_3"]
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
        """По подходу определить, к какой фазе он относится."""
        phases = self.intersection_phases.get(intersection_id, {})
        for phase_name in phases:
            if approach in self._get_approaches_for_phase(intersection_id, phase_name):
                return phase_name
        return None

    def get_num_roads(self, intersection_id: str) -> int:
        """Количество дорог (подходов) у перекрёстка — определяется из данных."""
        return len(self.intersection_approaches.get(intersection_id, set()))

    # ===================== КАСКАДНОЕ УПРАВЛЕНИЕ =====================

    def _precompute_topology_cache(self):
        """
        Предвычислить upstream/downstream для всех перекрёстков на основе
        статического графа связей. Топология графа не меняется в рантайме,
        поэтому кэш считается один раз в _build_static_topology().
        """
        self._upstream_cache.clear()
        self._downstream_cache.clear()

        nodes_by_inter: Dict[str, List[str]] = {}
        for (inter_id, approach) in self.graph.nodes:
            nodes_by_inter.setdefault(inter_id, []).append(approach)

        for inter_id, approaches in nodes_by_inter.items():
            upstream_cache: Dict[str, List[str]] = {}
            downstream_cache: Dict[str, List[str]] = {}

            for approach in approaches:
                node = (inter_id, approach)
                for u, v, _ in self.graph.in_edges(node, data=True):
                    if u[0] != inter_id:
                        upstream_cache.setdefault(approach, []).append(u[0])
                for u, v, _ in self.graph.out_edges(node, data=True):
                    if v[0] != inter_id:
                        downstream_cache.setdefault(approach, []).append(v[0])

            self._upstream_cache[inter_id] = upstream_cache
            self._downstream_cache[inter_id] = downstream_cache

    def get_upstream_intersections(self, intersection_id: str) -> Dict[str, List[str]]:
        """Для каждого подхода перекрёстка найти upstream перекрёстки."""
        return self._upstream_cache.get(intersection_id, {}).copy()

    def get_downstream_intersections(self, intersection_id: str) -> Dict[str, List[str]]:
        """Для каждого подхода найти downstream перекрёстки."""
        return self._downstream_cache.get(intersection_id, {}).copy()

    def calculate_cascade(self) -> List[dict]:
        """
        Проверить все перекрёстки.
        Если подход забит >70% → upstream урезает зелёный.
        Если подход свободен <30% → можно продлить зелёный (зелёная волна).
        Работает для любого числа дорог (1-4), т.к. топология из данных.
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

    # ===================== ТОПОЛОГИЯ ДЛЯ UI =====================

    def get_topology_for_ui(self) -> dict:
        """
        Текущая топология сети для админ-панели (строится из ДАННЫХ,
        а не из хардкода). Возвращает перекрёстки (с позициями и числом дорог)
        и связи между ними.
        """
        intersections: Dict[str, dict] = {}
        for inter_id, apps in self.intersection_approaches.items():
            intersections[inter_id] = {
                "position": road_config.get_position(inter_id),
                "approaches": sorted(apps),
                "num_roads": len(apps),
            }
        # Если телеметрии ещё не было — покажем перекрёстки из конфига (без дорог).
        if not intersections:
            for inter_id in road_config.get_intersection_ids():
                intersections[inter_id] = {
                    "position": road_config.get_position(inter_id),
                    "approaches": [],
                    "num_roads": 0,
                }

        links = []
        for src, dst in _parse_links():
            src_inter, _ = _resolve_intersection_and_approach(src)
            dst_inter, _ = _resolve_intersection_and_approach(dst)
            if src_inter in intersections and dst_inter in intersections:
                links.append((src_inter, dst_inter))

        return {"intersections": intersections, "links": links}

    def get_full_state(self) -> dict:
        """Полное состояние для UI"""
        state = {}
        for inter_id in self.intersection_phases:
            lanes = self.get_lanes_for_intersection(inter_id)
            avg_cong = sum(l["congestion_index"] for l in lanes) / len(lanes) if lanes else 0
            state[inter_id] = {
                "lanes": lanes,
                "avg_congestion": round(avg_cong, 2),
                "num_roads": self.get_num_roads(inter_id),
            }
        return state

    def get_congestion_map(self) -> dict:
        """
        Camera-First Design: карта загруженности всех полос.
        Возвращает: { "intersection_1_approach_0": 0.6, ... }
        Используется Unity-машинами для ограничения переполненных дорог.
        """
        congestion_map = {}
        for lane_id, data in self.lane_pool.items():
            congestion_map[lane_id] = data.get("congestion_index", 0.0)
        return congestion_map


# Синглтон
traffic_network = CityTrafficGraph()