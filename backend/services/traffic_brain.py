# backend/services/traffic_brain.py
from backend.models.traffic import IntersectionUpdateDTO
from typing import Dict

class AdaptiveTrafficBrain:
    def __init__(self):
        self._lanes_state: Dict[str, int] = {}
        self._current_phase: str = "Z_GREEN"

    def _resolve_lane_axis(self, lane_id: str) -> str:
        if "_X_" in lane_id:
            return "X"
        if "_Z_" in lane_id:
            return "Z"
        return "Z"

    def process_telemetry(self, update: IntersectionUpdateDTO) -> str:
        # Обновляем состояние текущих пришедших полос
        for lane in update.lanes:
            self._lanes_state[lane.lane_id] = lane.car_count

        x_load = 0
        z_load = 0

        # Фильтруем данные конкретно для текущего перекрестка
        for lane_id, car_count in self._lanes_state.items():
            if update.intersection_id in lane_id:
                axis = self._resolve_lane_axis(lane_id)
                if axis == "X":
                    x_load += car_count
                elif axis == "Z":
                    z_load += car_count

        print(f"📊 [МОНИТОРИНГ] {update.intersection_id} -> Ось Z: {z_load} | Ось X: {x_load}")

        # Если на одной из осей вообще нет машин (или нет камер), а на другой есть — включаем зелёный туда, где ждут
        if x_load > 0 and z_load == 0 and self._current_phase == "Z_GREEN":
            self._current_phase = "X_GREEN"
        elif z_load > 0 and x_load == 0 and self._current_phase == "X_GREEN":
            self._current_phase = "Z_GREEN"
        # Стандартная балансировка при наличии машин с обеих сторон
        elif x_load > (z_load + 2) and self._current_phase == "Z_GREEN":
            self._current_phase = "X_GREEN"
        elif z_load > (x_load + 1) and self._current_phase == "X_GREEN":
            self._current_phase = "Z_GREEN"

        return self._current_phase