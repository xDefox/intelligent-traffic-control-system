"""
Statistics Service - сбор и расчет статистики трафика.

Собирает метрики:
- Время ожидания (average waiting time)
- Нагруженность (congestion level)
- Эффективность переключения фаз
- История данных для аналитики
"""

import time
from typing import Dict, List, Optional, Set
from collections import deque
from dataclasses import dataclass, field
from backend.core.logger import debug, info, warning


@dataclass
class LaneStats:
    """Статистика одной полосы"""
    lane_id: str
    total_cars: int = 0
    total_wait_time: float = 0.0  # суммарное время ожидания всех машин (секунды)
    cars_waited: int = 0  # количество машин, которые ждали
    phase_switches: int = 0
    last_green_start: Optional[float] = None
    wait_start_time: Optional[float] = None
    history: deque = field(default_factory=lambda: deque(maxlen=100))  # История последних 100 значений
    
    def to_dict(self) -> dict:
        avg_wait = self.total_wait_time / self.cars_waited if self.cars_waited > 0 else 0.0
        return {
            "lane_id": self.lane_id,
            "total_cars": self.total_cars,
            "avg_wait_time": round(avg_wait, 2),
            "phase_switches": self.phase_switches,
            "current_waiting": self.wait_start_time is not None,
        }


@dataclass  
class IntersectionStats:
    """Статистика одного перекрёстка"""
    intersection_id: str
    total_cars: int = 0
    total_wait_time: float = 0.0
    cars_waited: int = 0
    phase_switches: int = 0
    green_time_total: float = 0.0
    red_time_total: float = 0.0
    last_phase_change: Optional[float] = None
    lanes: Dict[str, LaneStats] = field(default_factory=dict)
    active_approaches: Set[str] = field(default_factory=set)  # подходы с зелёным светом
    
    def to_dict(self) -> dict:
        avg_wait = self.total_wait_time / self.cars_waited if self.cars_waited > 0 else 0.0
        total_time = self.green_time_total + self.red_time_total
        efficiency = (self.green_time_total / total_time * 100) if total_time > 0 else 0.0
        
        return {
            "intersection_id": self.intersection_id,
            "total_cars": self.total_cars,
            "avg_wait_time": round(avg_wait, 2),
            "phase_switches": self.phase_switches,
            "green_time_total": round(self.green_time_total, 1),
            "red_time_total": round(self.red_time_total, 1),
            "efficiency_pct": round(efficiency, 1),
            "lanes": {lid: ls.to_dict() for lid, ls in self.lanes.items()},
        }


class TrafficStatistics:
    """
    Сборщик статистики трафика.
    
    Трекает:
    - Время ожидания машин на красном свете
    - Нагруженность полос и перекрёстков
    - Эффективность переключения фаз
    - Исторические данные
    """
    
    def __init__(self):
        self.intersection_stats: Dict[str, IntersectionStats] = {}
        self._phase_states: Dict[str, dict] = {}  # {inter_id: {"phase": str, "start_time": float, "approaches": set}}
        
    def get_or_create_intersection(self, intersection_id: str) -> IntersectionStats:
        """Получить или создать статистику перекрёстка"""
        if intersection_id not in self.intersection_stats:
            self.intersection_stats[intersection_id] = IntersectionStats(intersection_id=intersection_id)
        return self.intersection_stats[intersection_id]
    
    def record_phase_change(self, intersection_id: str, phase: str, active_approaches: List[str] = None):
        """
        Записать событие переключения фазы.
        Рассчитывает время ожидания для всех полос, которые были на красном.
        """
        stats = self.get_or_create_intersection(intersection_id)
        now = time.time()
        
        # Если была предыдущая фаза - обновляем статистику
        if intersection_id in self._phase_states:
            prev_phase = self._phase_states[intersection_id]
            prev_start = prev_phase.get("start_time", now)
            prev_approaches = prev_phase.get("approaches", set())
            
            # Время фазы
            phase_duration = now - prev_start
            
            # Считаем красное время для полос, которые НЕ были в активной фазе
            for lane_id, lane_stats in stats.lanes.items():
                # lane_id format: "lane_intersection_1_approach_0"
                # extract approach from lane_id
                if "approach_" in lane_id:
                    lane_approach = lane_id.split("approach_")[-1]
                    lane_approach = f"approach_{lane_approach}"
                else:
                    continue
                
                # Если эта полоса была на красном (не в active_approaches) - добавляем к красному времени
                if lane_approach not in prev_approaches:
                    stats.red_time_total += phase_duration
                    
                    # Если машины ждали - добавляем время ожидания
                    if lane_stats.wait_start_time is not None:
                        wait_time = now - lane_stats.wait_start_time
                        lane_stats.total_wait_time += wait_time
                        lane_stats.cars_waited += 1
                        lane_stats.wait_start_time = None
            
            # Зелёное время для активных подходов
            stats.green_time_total += phase_duration
            
            stats.phase_switches += 1
        
        # Сохраняем новое состояние фазы
        self._phase_states[intersection_id] = {
            "phase": phase,
            "start_time": now,
            "approaches": set(active_approaches) if active_approaches else set(),
        }
        
        debug("Statistics", f"Phase change: {intersection_id} -> {phase}, active_approaches={active_approaches}")
    
    def record_lane_data(self, intersection_id: str, lane_id: str, 
                        car_count: int, avg_speed: float, congestion: float,
                        is_green: bool = False):
        """
        Записать данные с полосы.
        Обновляет историю и считает время ожидания.
        
        Args:
            is_green: True если на этой полосе зелёный свет
        """
        stats = self.get_or_create_intersection(intersection_id)
        
        if lane_id not in stats.lanes:
            stats.lanes[lane_id] = LaneStats(lane_id=lane_id)
        
        lane_stats = stats.lanes[lane_id]
        
        # Сохраняем в историю
        lane_stats.history.append({
            "timestamp": time.time(),
            "car_count": car_count,
            "avg_speed": avg_speed,
            "congestion": congestion,
            "is_green": is_green,
        })
        
        # Обновляем счётчики
        if car_count > 0:
            stats.total_cars = max(stats.total_cars, car_count)
        
        # Управление таймером ожидания
        if is_green:
            # Если зелёный - сбрасываем таймер ожидания
            if lane_stats.wait_start_time is not None:
                wait_time = time.time() - lane_stats.wait_start_time
                lane_stats.total_wait_time += wait_time
                lane_stats.cars_waited += 1
                lane_stats.wait_start_time = None
        else:
            # Если красный и есть машины - начинаем/продолжаем отсчёт
            if car_count > 0 and lane_stats.wait_start_time is None:
                lane_stats.wait_start_time = time.time()
        
        debug("Statistics", f"Lane data: {lane_id} cars={car_count} speed={avg_speed:.1f} congestion={congestion:.2f} green={is_green}")
    
    def get_congestion_level(self, intersection_id: str) -> float:
        """
        Получить текущий уровень загруженности перекрёстка (0.0 - 1.0).
        """
        stats = self.intersection_stats.get(intersection_id)
        if not stats:
            return 0.0
        
        if not stats.lanes:
            return 0.0
        
        # Средняя загруженность по всем полосам
        total_congestion = 0.0
        count = 0
        for lane_stats in stats.lanes.values():
            if lane_stats.history:
                last = lane_stats.history[-1]
                total_congestion += last.get("congestion", 0)
                count += 1
        
        return total_congestion / count if count > 0 else 0.0
    
    def get_average_wait_time(self, intersection_id: str) -> float:
        """
        Получить среднее время ожидания на перекрёстке (секунды).
        """
        stats = self.intersection_stats.get(intersection_id)
        if not stats:
            return 0.0
        
        if stats.cars_waited == 0:
            return 0.0
        
        return stats.total_wait_time / stats.cars_waited
    
    def get_efficiency(self, intersection_id: str) -> float:
        """
        Получить эффективность перекрёстка (процент полезного зелёного времени).
        """
        stats = self.intersection_stats.get(intersection_id)
        if not stats:
            return 0.0
        
        total_time = stats.green_time_total + stats.red_time_total
        if total_time == 0:
            return 0.0
        
        return (stats.green_time_total / total_time) * 100
    
    def get_full_statistics(self) -> dict:
        """
        Получить полную статистику всех перекрёстков.
        """
        return {
            "intersections": {
                iid: stats.to_dict() 
                for iid, stats in self.intersection_stats.items()
            },
            "network_summary": {
                "total_intersections": len(self.intersection_stats),
                "total_cars": sum(s.total_cars for s in self.intersection_stats.values()),
                "avg_wait_time": sum(s.total_wait_time for s in self.intersection_stats.values()) / 
                                max(sum(s.cars_waited for s in self.intersection_stats.values()), 1),
                "total_phase_switches": sum(s.phase_switches for s in self.intersection_stats.values()),
            }
        }
    
    def get_lane_history(self, lane_id: str, limit: int = 50) -> List[dict]:
        """
        Получить историю данных для конкретной полосы.
        """
        for stats in self.intersection_stats.values():
            if lane_id in stats.lanes:
                return list(stats.lanes[lane_id].history)[-limit:]
        return []


# Синглтон
traffic_stats = TrafficStatistics()