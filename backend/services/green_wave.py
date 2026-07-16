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
from typing import Dict, List, Optional, Tuple
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
        
        # Сохраняем активные волны
        self._active_waves.clear()
        for cmd in commands:
            corridor_key = str(cmd.get("corridor", []))
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
        """Найти все линейные коридоры в дорожной сети."""
        corridors = []
        visited = set()
        
        intersections = list(traffic_network.intersection_phases.keys())
        
        for start_inter in intersections:
            if start_inter in visited:
                continue
            
            corridor = self._build_corridor_from(start_inter)
            
            if len(corridor) >= self.MIN_CORRIDOR_LENGTH:
                corridors.append(corridor)
                visited.update(corridor)
        
        return corridors
    
    def _build_corridor_from(self, start_inter: str) -> List[str]:
        """Построить коридор, начиная с перекрёстка."""
        corridor = [start_inter]
        current = start_inter
        
        while True:
            downstream_map = traffic_network.get_downstream_intersections(current)
            
            all_downstream = set()
            for downstream_list in downstream_map.values():
                all_downstream.update(downstream_list)
            
            if len(all_downstream) == 1:
                next_inter = list(all_downstream)[0]
                if next_inter not in corridor:
                    corridor.append(next_inter)
                    current = next_inter
                else:
                    break
            else:
                break
        
        return corridor
    
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
            if idx == 0:
                commands.append({
                    "target_intersection": inter_id,
                    "phase": base_phase,
                    "offset": 0.0,
                    "corridor": corridor,
                })
            else:
                offset = sum(travel_times[:idx])
                commands.append({
                    "target_intersection": inter_id,
                    "phase": base_phase,
                    "offset": offset,
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