# backend/services/traffic_brain.py
from typing import Dict, Optional
from backend.models.traffic import IntersectionUpdateDTO
from backend.services.graph_manager import traffic_network
import time


# Глобальный словарь для координации фаз НА ОДНОМ перекрёстке
_intersection_phase_state: Dict[str, dict] = {}


def _get_intersection_phase_state(intersection_id: str) -> dict:
    """Получить или создать состояние фазы для перекрёстка"""
    if intersection_id not in _intersection_phase_state:
        _intersection_phase_state[intersection_id] = {
            "active_phase": None,
            "phase_start_time": 0,
            "min_duration": 8.0,
        }
    return _intersection_phase_state[intersection_id]


def _has_cars_on_any_approach(intersection_id: str, phase_name: str) -> bool:
    """Есть ли машины хотя бы на одном подходе данной фазы"""
    for lane_id, data in traffic_network.lane_pool.items():
        if data["intersection_id"] != intersection_id:
            continue
        lane_approach = data["approach"]
        lane_phase = traffic_network.get_phase_for_approach(intersection_id, lane_approach)
        if lane_phase == phase_name and data["car_count"] > 0:
            return True
    return False


class AdaptiveTrafficBrain:
    """
    Мозг ОДНОГО светофора (per-lane).
    
    НЕ управляет фазой — фазу переключает оркестратор.
    Только решает: GREEN или RED для ЭТОГО подхода.
    """

    def __init__(self, intersection_id: str, is_per_lane: bool = False):
        self.intersection_id = intersection_id
        self.is_per_lane = is_per_lane
        
        if is_per_lane:
            self._current_command: str = "RED"
            self._last_car_count: int = 0
            self._green_start_time: float = 0
            self._min_green_duration: float = 8.0
            self._max_green_duration: float = 25.0
            self._last_sent_duration: float = 0.0
            self._approach: str = ""
            self._phase_name: str = ""
            self._last_green_decision_time: float = 0

    def process_lane_telemetry(self, update: IntersectionUpdateDTO) -> tuple:
        """
        Вернуть (команда, длительность) для ЭТОГО светофора.
        Фазу НЕ переключает — только проверяет, активна ли его фаза.
        """
        if not self.is_per_lane:
            return self.process_telemetry(update), 0.0
        
        for lane in update.lanes:
            traffic_network.update_lane_state(
                lane_id=lane.lane_id,
                car_count=lane.car_count,
                avg_speed=lane.avg_speed,
                max_capacity=lane.max_capacity,
            )
            self._last_car_count = lane.car_count

        intersection_id = update.intersection_id
        lane_id = update.camera_id
        self._approach = lane_id.replace(f"{intersection_id}_", "")
        self._phase_name = traffic_network.get_phase_for_approach(intersection_id, self._approach)
        
        if not self._phase_name:
            return "RED", 0.0
        
        # Проверяем, активна ли наша фаза
        phase_state = _get_intersection_phase_state(intersection_id)
        active_phase = phase_state.get("active_phase")
        
        if active_phase != self._phase_name:
            # Наша фаза не активна → красный
            if self._current_command != "RED":
                self._current_command = "RED"
                self._green_start_time = 0
                print(f"  🔴 [{lane_id}] RED (фаза {self._phase_name} не активна, активна {active_phase})")
            return "RED", 0.0
        
        # Наша фаза активна → зелёный
        elapsed = time.time() - self._green_start_time if self._green_start_time > 0 else 999
        
        # Минимальное время
        if self._current_command == "GREEN" and elapsed < self._min_green_duration:
            return "GREEN", max(self._min_green_duration - elapsed, 1.0)
        
        if self._last_car_count > 0:
            congestion_ratio = self._last_car_count / max(self._last_max_capacity, 1)
            
            if congestion_ratio < 0.3:
                green_duration = 8.0 + (congestion_ratio / 0.3) * 5.0
            elif congestion_ratio < 0.7:
                green_duration = 13.0 + ((congestion_ratio - 0.3) / 0.4) * 7.0
            else:
                green_duration = 20.0 + ((congestion_ratio - 0.7) / 0.3) * 5.0
            
            green_duration = min(self._max_green_duration, max(8.0, green_duration))
            
            if self._current_command != "GREEN":
                self._current_command = "GREEN"
                self._green_start_time = time.time()
                self._last_sent_duration = green_duration
                self._last_green_decision_time = time.time()
                print(f"  🟢 [{lane_id}] GREEN на {green_duration:.1f}с (машины: {self._last_car_count})")
                return "GREEN", green_duration
            else:
                # Продление
                if time.time() - self._last_green_decision_time > 3.0:
                    self._last_sent_duration = green_duration
                    self._last_green_decision_time = time.time()
                    return "GREEN", green_duration
                return "GREEN", 0.0
        else:
            # Машин нет, но фаза активна — всё равно зелёный
            if self._current_command != "GREEN":
                self._current_command = "GREEN"
                self._green_start_time = time.time()
                self._last_sent_duration = 8.0
                self._last_green_decision_time = time.time()
                return "GREEN", 8.0
            return "GREEN", 0.0

    def process_telemetry(self, update: IntersectionUpdateDTO) -> str:
        """Legacy"""
        for lane in update.lanes:
            traffic_network.update_lane_state(lane_id=lane.lane_id, car_count=lane.car_count, avg_speed=lane.avg_speed, max_capacity=lane.max_capacity)
        return "NS"

    def apply_cascade_command(self, command: dict):
        action = command.get("action", "")
        if action == "REDUCE_GREEN":
            reduce_by = command.get("reduce_green_by", 5)
            self._min_green_duration = max(3.0, 8.0 - reduce_by)
            self._max_green_duration = max(10.0, 25.0 - reduce_by)
            print(f"  ⏱️  Урезан до {self._min_green_duration:.0f}с")
        elif action == "GREEN_WAVE":
            extend_by = command.get("extend_green_by", 3)
            self._min_green_duration = 15.0 + extend_by
            self._max_green_duration = 30.0 + extend_by
            print(f"  🟢  Зелёная волна +{extend_by}с")