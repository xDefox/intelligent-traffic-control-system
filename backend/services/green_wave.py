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
    
    # Целевая скорость для зелёной волны (м/с)
    # 50 км/ч = 13.9 м/с — оптимальная скорость для города
    TARGET_SPEED_MS = 13.9
    
    # Минимальная длина коридора для активации (2 перекрёстка)
    MIN_CORRIDOR_LENGTH = 2
    
    def __init__(self):
        self._active_waves: Dict[str, dict] = {}  # corridor_id -> wave_data
        self._last_calculation = 0
        self._calculation_interval = 5.0  # Пересчёт каждые 5 секунд
        
    def calculate_green_wave(self, force: bool = False) -> List[dict]:
        """
        Рассчитать команды для зелёной волны.
        
        Returns:
            Список команд для применения к светофорам
        """
        current_time = time.time()
        
        # Не пересчитываем слишком часто (кэширование)
        if not force and (current_time - self._last_calculation) < self._calculation_interval:
            return []
        
        self._last_calculation = current_time
        commands = []
        
        # 1. Находим все коридоры (линейные последовательности перекрёстков)
        corridors = self._find_corridors()
        
        # 2. Для каждого коридора рассчитываем синхронизацию
        for corridor in corridors:
            corridor_commands = self._calculate_corridor_sync(corridor)
            commands.extend(corridor_commands)
        
        return commands
    
    def _find_corridors(self) -> List[List[str]]:
        """
        Найти все линейные коридоры в дорожной сети.
        
        Коридор — это последовательность перекрёстков, соединённых прямыми связями.
        Например: intersection_1 -> intersection_2 -> intersection_3
        """
        corridors = []
        visited = set()
        
        # Получаем все перекрёстки
        intersections = list(traffic_network.intersection_phases.keys())
        
        for start_inter in intersections:
            if start_inter in visited:
                continue
            
            # Пытаемся построить коридор от этого перекрёстка
            corridor = self._build_corridor_from(start_inter)
            
            if len(corridor) >= self.MIN_CORRIDOR_LENGTH:
                corridors.append(corridor)
                visited.update(corridor)
        
        return corridors
    
    def _build_corridor_from(self, start_inter: str) -> List[str]:
        """
        Построить коридор, начиная с перекрёстка.
        
        Алгоритм:
        1. Начинаем с start_inter
        2. Ищем downstream перекрёстки (куда можно ехать)
        3. Если есть только один downstream — продолжаем коридор
        4. Если есть несколько или нет — завершаем
        """
        corridor = [start_inter]
        current = start_inter
        
        while True:
            # Получаем все downstream перекрёстки
            downstream_map = traffic_network.get_downstream_intersections(current)
            
            # Собираем все downstream перекрёстки (убираем дубликаты)
            all_downstream = set()
            for downstream_list in downstream_map.values():
                all_downstream.update(downstream_list)
            
            # Если есть ровно один downstream и он ещё не в коридоре — добавляем
            if len(all_downstream) == 1:
                next_inter = list(all_downstream)[0]
                if next_inter not in corridor:
                    corridor.append(next_inter)
                    current = next_inter
                else:
                    break
            else:
                # Развилка или тупик — завершаем коридор
                break
        
        return corridor
    
    def _calculate_corridor_sync(self, corridor: List[str]) -> List[dict]:
        """
        Рассчитать синхронизацию для коридора.
        
        Логика:
        1. Определяем основное направление (восток-запад или север-юг)
        2. Рассчитываем время проезда между перекрёстками
        3. Сдвигаем фазы так, чтобы зелёный "бежал" по коридору
        """
        commands = []
        
        if len(corridor) < 2:
            return commands
        
        # 1. Определяем основное направление коридора
        # Берём позиции перекрёстков из конфига
        positions = self._get_intersection_positions(corridor)
        
        if not positions:
            return commands
        
        # 2. Определяем основную ось (X или Z)
        # Если перекрёстки расположены вдоль X — это EW направление
        # Если вдоль Z — это NS направление
        main_axis = self._determine_main_axis(positions)
        
        # 3. Рассчитываем время проезда между перекрёстками
        travel_times = self._calculate_travel_times(positions, main_axis)
        
        # 4. Определяем базовую фазу (ту, которая идёт по направлению коридора)
        base_phase = "EW" if main_axis == "x" else "NS"
        
        # 5. Создаём команды синхронизации
        # Первый перекрёсток — базовый, остальные сдвигаются
        base_offset = 0.0
        
        for idx, inter_id in enumerate(corridor):
            if idx == 0:
                # Первый перекрёсток — базовый
                commands.append({
                    "target_intersection": inter_id,
                    "action": "GREEN_WAVE_SYNC",
                    "phase": base_phase,
                    "offset": 0.0,
                    "corridor": corridor,
                    "priority": "high"
                })
            else:
                # Остальные — со сдвигом
                # Сдвиг = сумма времён проезда от первого до текущего
                offset = sum(travel_times[:idx])
                
                commands.append({
                    "target_intersection": inter_id,
                    "action": "GREEN_WAVE_SYNC",
                    "phase": base_phase,
                    "offset": offset,
                    "corridor": corridor,
                    "priority": "high"
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
        """
        Определить основную ось коридора (x или z).
        
        Анализирует разброс координат:
        - Если разброс по X больше, чем по Z — основная ось X (EW)
        - Иначе — основная ось Z (NS)
        """
        if len(positions) < 2:
            return "x"
        
        x_values = [pos[0] for pos in positions.values()]
        z_values = [pos[1] for pos in positions.values()]
        
        x_range = max(x_values) - min(x_values)
        z_range = max(z_values) - min(z_values)
        
        return "x" if x_range >= z_range else "z"
    
    def _calculate_travel_times(self, positions: Dict[str, Tuple[float, float]], 
                                main_axis: str) -> List[float]:
        """
        Рассчитать время проезда между последовательными перекрёстками.
        
        Returns:
            Список времён проезда [t(0->1), t(1->2), ...]
        """
        travel_times = []
        
        # Сортируем перекрёстки по основной оси
        sorted_inters = sorted(positions.keys(), 
                              key=lambda iid: positions[iid][0] if main_axis == "x" else positions[iid][1])
        
        for i in range(len(sorted_inters) - 1):
            inter1 = sorted_inters[i]
            inter2 = sorted_inters[i + 1]
            
            pos1 = positions[inter1]
            pos2 = positions[inter2]
            
            # Рассчитываем расстояние
            dx = pos2[0] - pos1[0]
            dz = pos2[1] - pos1[1]
            distance = math.sqrt(dx**2 + dz**2)
            
            # Время = расстояние / скорость
            travel_time = distance / self.TARGET_SPEED_MS
            travel_times.append(travel_time)
        
        return travel_times
    
    def get_active_waves(self) -> Dict[str, dict]:
        """Получить активные зелёные волны"""
        return self._active_waves.copy()


# Синглтон
green_wave_coordinator = GreenWaveCoordinator()