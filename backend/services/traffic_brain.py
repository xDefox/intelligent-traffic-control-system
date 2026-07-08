from backend.models.traffic import IntersectionUpdateDTO
from typing import Dict


class AdaptiveTrafficBrain:
    def __init__(self):
        # Хранилище слепков перекрестков (память сервера)
        # Структура: { "intersection_id": { "lane_id": car_count } }
        self._intersections_state: Dict[str, Dict[str, int]] = {}

        # Текущая активная фаза для каждого перекрестка
        # Структура: { "intersection_id": "CURRENT_PHASE" }
        self._current_phases: Dict[str, str] = {}

    def _get_phase_for_lane(self, lane_id: str) -> str:
        """
        Определяет, к какой фазе относится данная полоса.
        Разделяет ответственность за маппинг инфраструктуры (SRP).
        """
        lane_lower = lane_id.lower()

        # Логика определения по подстрокам в ID полосы (из Unity)
        if "main" in lane_lower or "west" in lane_lower or "east" in lane_lower:
            return "MAIN_GREEN"
        if "side" in lane_lower or "north" in lane_lower or "south" in lane_lower:
            return "SIDE_GREEN"

        return "MAIN_GREEN"  # Дефолтная фаза безопасности

    def process_telemetry(self, update: IntersectionUpdateDTO) -> str:
        """
        Принимает пакет данных от конкретной камеры, обновляет глобальный
        слепок перекрестка и выносит решение по фазе на основе полной картины.
        """
        inter_id = update.intersection_id

        # Инициализируем перекресток в памяти, если он запрашивается впервые
        if inter_id not in self._intersections_state:
            self._intersections_state[inter_id] = {}
            self._current_phases[inter_id] = "MAIN_GREEN"

        # Обновляем состояние только тех полос, которые увидела эта камера
        for lane in update.lanes:
            self._intersections_state[inter_id][lane.lane_id] = lane.car_count

        # Рассчитываем суммарную нагрузку по фазам на основе ВСЕХ полос перекрестка
        main_road_load = 0
        side_road_load = 0

        for lane_id, car_count in self._intersections_state[inter_id].items():
            phase = self._get_phase_for_lane(lane_id)
            if phase == "MAIN_GREEN":
                main_road_load += car_count
            elif phase == "SIDE_GREEN":
                side_road_load += car_count

        # Алгоритм выбора фазы с гистерезисом (+1 машина) для исключения дребезга фаз
        current_phase = self._current_phases[inter_id]

        if side_road_load > (main_road_load + 1):
            current_phase = "SIDE_GREEN"
        elif main_road_load > (side_road_load + 1):
            current_phase = "MAIN_GREEN"

        # Сохраняем и возвращаем обновленное решение
        self._current_phases[inter_id] = current_phase
        return current_phase