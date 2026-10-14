"""
Green Wave (Зелёный поток) система координации светофоров.

Принцип работы:
1. Определяет коридоры (последовательности перекрёстков)
2. Рассчитывает задержки между перекрёстками на основе расстояний
3. Синхронизирует фазы светофоров для создания непрерывного потока
4. Целевая скорость: 50 км/ч (~13.9 м/с) — стандарт для городских дорог
"""

import math
import time
from typing import Dict, List, Tuple
from backend.services.graph_manager import traffic_network


class GreenWaveCoordinator:
    """
    Координатор зелёной волны.
    
    Анализирует топологию дорожной сети и создаёт синхронизированные
    последовательности светофоров для основных коридоров.
    """
    
    TARGET_SPEED_MS = 13.9  # 50 км/ч
    MIN_CORRIDOR_LENGTH = 2
    CYCLE_TIME = 60.0  # Полный цикл светофора (сек)
    GREEN_WINDOW = 20.0  # Окно зелёного в цикле (сек)
    
    def __init__(self):
        self._active_waves: Dict[str, dict] = {}
        self._last_calculation = 0
        self._calculation_interval = 5.0
        
    def calculate_green_wave(self, force: bool = False) -> List[dict]:
        """
        Рассчитать команды для зелёной волны.
        Возвращает список команд с target_intersection, phase, и offset (сдвиг в секундах).
        """
        current_time = time.time()
        
        if not force and (current_time - self._last_calculation) < self._calculation_interval:
            return self._get_current_commands()
        
        self._last_calculation = current_time
        commands = []
        
        corridors = self._find_corridors()
        for corridor in corridors:
            corridor_commands = self._calculate_corridor_sync(corridor)
            commands.extend(corridor_commands)
        
        # Сохраняем активные волны.
        # КЛЮЧ = коридор + перекрёсток: иначе все команды коридора пишутся в один
        # ключ str(corridor) и перезаписывают друг друга — выживал только последний
        # перекрёсток, а первый в коридоре никогда не получал синхронизацию.
        self._active_waves.clear()
        for cmd in commands:
            inter_id = cmd.get("target_intersection", "")
            corridor_key = f"{cmd.get('corridor', [])}::{inter_id}"
            self._active_waves[corridor_key] = cmd
        
        if not commands:
            return []

        return self._get_current_commands()
    
    def _get_current_commands(self) -> List[dict]:
        """Вернуть команды с расчётом, активна ли сейчас зелёная волна"""
        result = []
        current_time = time.time()
        
        for cmd in self._active_waves.values():
            offset = cmd.get("offset", 0.0)
            # Определяем, активна ли сейчас зелёная волна
            # offset — сдвиг начала цикла для этого перекрёстка
            # Если (time + offset) % CYCLE_TIME < GREEN_WINDOW — зелёная волна активна
            normalized_time = (current_time + offset) % self.CYCLE_TIME
            is_active = normalized_time < self.GREEN_WINDOW
            
            if is_active:
                result.append(cmd)
        
        return result
    
    def _find_corridors(self) -> List[List[str]]:
        """
        Найти линейные коридоры в дорожной сети.

        Идём по связям «насквозь» ВДОЛЬ ОДНОЙ ОСИ. Раньше требовали, чтобы у
        перекрёстка был ровно ОДИН downstream-сосед — из-за этого коридор
        обрывался на любом 4-стороннем перекрёстке посреди прямой (у него ведь
        есть ещё и перпендикулярные ответвления), и зелёная волна молча
        отключалась на магистрали из 3+ перекрёстков.
        """
        links = self._collect_axis_links()
        if not links:
            return []

        # adj[inter][axis] = множество соседей вдоль этой оси
        adj: Dict[str, Dict[str, set]] = {}
        for a, b, axis in links:
            adj.setdefault(a, {}).setdefault(axis, set()).add(b)
            adj.setdefault(b, {}).setdefault(axis, set()).add(a)

        used_edges = set()
        corridors: List[List[str]] = []

        for a, b, axis in links:
            edge = (min(a, b), max(a, b))
            if edge in used_edges:
                continue
            chain = self._extend_straight(a, b, axis, adj)
            for i in range(len(chain) - 1):
                used_edges.add((min(chain[i], chain[i + 1]), max(chain[i], chain[i + 1])))
            if len(chain) >= self.MIN_CORRIDOR_LENGTH:
                corridors.append(chain)

        return corridors

    def _collect_axis_links(self) -> List[Tuple[str, str, str]]:
        """Рёбра между разными перекрёстками с указанием оси (x для EW, z для NS)."""
        links = []
        seen = set()
        for (inter_a, app_a), (inter_b, app_b) in traffic_network.graph.edges:
            if inter_a == inter_b:
                continue
            axis = "x" if app_a in ("approach_0", "approach_1") else "z"
            key = (min(inter_a, inter_b), max(inter_a, inter_b), axis)
            if key in seen:
                continue
            seen.add(key)
            links.append(key)
        return links

    def _extend_straight(self, a: str, b: str, axis: str,
                         adj: Dict[str, Dict[str, set]]) -> List[str]:
        """Продолжить цепочку a-b в обе стороны строго вдоль оси axis."""
        chain = [a, b]

        # Вперёд от b
        prev, cur = a, b
        while True:
            nxt = [n for n in adj.get(cur, {}).get(axis, set())
                   if n != prev and n not in chain]
            if len(nxt) != 1:
                break
            chain.append(nxt[0])
            prev, cur = cur, nxt[0]

        # Назад от a
        prev, cur = b, a
        while True:
            nxt = [n for n in adj.get(cur, {}).get(axis, set())
                   if n != prev and n not in chain]
            if len(nxt) != 1:
                break
            chain.insert(0, nxt[0])
            prev, cur = cur, nxt[0]

        return chain
    
    def _calculate_corridor_sync(self, corridor: List[str]) -> List[dict]:
        """
        Рассчитать синхронизацию для коридора.
        Каждый следующий перекрёсток получает offset = время проезда от первого.
        """
        commands = []
        
        if len(corridor) < 2:
            return commands
        
        positions = self._get_intersection_positions(corridor)
        if not positions:
            return commands
        
        main_axis = self._determine_main_axis(positions)
        travel_times = self._calculate_travel_times(positions, main_axis)
        base_phase = "EW" if main_axis == "x" else "NS"
        
        for idx, inter_id in enumerate(corridor):
            commands.append({
                "target_intersection": inter_id,
                "action": "GREEN_WAVE_SYNC",
                "phase": base_phase,
                "offset": 0.0 if idx == 0 else sum(travel_times[:idx]),
                "corridor": corridor,
            })
        
        return commands
    
    def _get_intersection_positions(self, corridor: List[str]) -> Dict[str, Tuple[float, float]]:
        """Получить позиции перекрёстков из конфига"""
        from backend.core.road_config import ROADS
        
        positions = {}
        for inter_id in corridor:
            config = ROADS.get(inter_id, {})
            pos = config.get("position", {})
            if "x" in pos and "z" in pos:
                positions[inter_id] = (pos["x"], pos["z"])
        
        return positions
    
    def _determine_main_axis(self, positions: Dict[str, Tuple[float, float]]) -> str:
        """Определить основную ось коридора (x или z)."""
        if len(positions) < 2:
            return "x"
        
        x_values = [pos[0] for pos in positions.values()]
        z_values = [pos[1] for pos in positions.values()]
        
        x_range = max(x_values) - min(x_values)
        z_range = max(z_values) - min(z_values)
        
        return "x" if x_range >= z_range else "z"
    
    def _calculate_travel_times(self, positions: Dict[str, Tuple[float, float]], 
                                main_axis: str) -> List[float]:
        """Рассчитать время проезда между последовательными перекрёстками."""
        travel_times = []
        
        sorted_inters = sorted(positions.keys(), 
                              key=lambda iid: positions[iid][0] if main_axis == "x" else positions[iid][1])
        
        for i in range(len(sorted_inters) - 1):
            inter1 = sorted_inters[i]
            inter2 = sorted_inters[i + 1]
            
            pos1 = positions[inter1]
            pos2 = positions[inter2]
            
            dx = pos2[0] - pos1[0]
            dz = pos2[1] - pos1[1]
            distance = math.sqrt(dx**2 + dz**2)
            
            travel_time = distance / self.TARGET_SPEED_MS
            travel_times.append(travel_time)
        
        return travel_times
    
    def get_active_waves(self) -> Dict[str, dict]:
        """Получить активные зелёные волны"""
        return self._active_waves.copy()


# Синглтон
green_wave_coordinator = GreenWaveCoordinator()