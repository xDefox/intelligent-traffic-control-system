"""
PhaseManager — единый источник истины о фазах светофоров.

Заменяет:
- _intersection_phase_state в traffic_brain.py (глобальный словарь)
- self._intersection_phase_states в orchestrator.py (внутренний словарь)

Теперь состояние фаз хранится в одном месте и доступно через dependency injection.
"""

import time
from typing import Dict, Optional


class PhaseState:
    """Состояние одной фазы перекрёстка"""

    def __init__(self, intersection_id: str):
        self.intersection_id = intersection_id
        self.active_phase: Optional[str] = None
        self.phase_start_time: float = 0
        self.min_duration: float = 8.0
        self.max_duration: float = 30.0

    @property
    def elapsed(self) -> float:
        """Сколько секунд активна текущая фаза"""
        if self.active_phase is None:
            return 999
        return time.time() - self.phase_start_time

    def switch_to(self, phase_name: str):
        """Переключиться на фазу phase_name"""
        self.active_phase = phase_name
        self.phase_start_time = time.time()

    def to_dict(self) -> dict:
        return {
            "active_phase": self.active_phase,
            "phase_start_time": self.phase_start_time,
            "min_duration": self.min_duration,
            "max_duration": self.max_duration,
        }


class PhaseManager:
    """
    Управляет состоянием фаз для всех перекрёстков.
    Единый источник истины — нигде больше не хранятся active_phase.
    """

    def __init__(self):
        self._states: Dict[str, PhaseState] = {}

    def get_or_create(self, intersection_id: str) -> PhaseState:
        """Получить или создать состояние для перекрёстка"""
        if intersection_id not in self._states:
            self._states[intersection_id] = PhaseState(intersection_id)
        return self._states[intersection_id]

    def get_state(self, intersection_id: str) -> Optional[PhaseState]:
        """Получить существующее состояние (без создания)"""
        return self._states.get(intersection_id)

    def get_active_phase(self, intersection_id: str) -> Optional[str]:
        """Какая фаза сейчас активна на перекрёстке"""
        state = self._states.get(intersection_id)
        if state is None:
            return None
        return state.active_phase

    def switch_phase(self, intersection_id: str, phase_name: str):
        """Переключить фазу перекрёстка"""
        state = self.get_or_create(intersection_id)
        state.switch_to(phase_name)

    def set_min_duration(self, intersection_id: str, duration: float):
        state = self.get_or_create(intersection_id)
        state.min_duration = max(3.0, duration)

    def set_max_duration(self, intersection_id: str, duration: float):
        state = self.get_or_create(intersection_id)
        state.max_duration = max(5.0, duration)