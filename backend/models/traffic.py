# backend/models/traffic.py
from pydantic import BaseModel, Field
from typing import List, Optional


class LaneDetectionDTO(BaseModel):
    lane_id: str         # "intersection_1_approach_0"
    car_count: int       # Количество машин в ROI
    avg_speed: float     # Средняя скорость движения в зоне
    max_capacity: int = Field(default=10, ge=1, description="Сколько машин помещается в кадре этой камеры")


class IntersectionUpdateDTO(BaseModel):
    intersection_id: str # ID перекрестка (Fog узел)
    camera_id: str       # ID конкретной камеры
    lanes: List[LaneDetectionDTO]


class PhaseTimingDTO(BaseModel):
    """Команда от Cloud к конкретному перекрёстку: какую фазу включить"""
    intersection_id: str
    target_phase: str                    # Название фазы из конфига (напр. "NS", "EW", "GREEN", "RED")
    min_duration_seconds: float = 5.0
    max_duration_seconds: float = 30.0
    cascade_action: str = "NONE"         # "NONE" | "REDUCE_GREEN" | "GREEN_WAVE"
    cascade_source: str = ""


class BackendResponseDTO(BaseModel):
    """Ответ backend'а на телеметрию от камеры"""
    target_phase: str                    # "GREEN" или "RED"
    green_duration: float = 0.0          # Длительность зелёного в секундах (0 = авто)
    confidence: float = 1.0              # Уверенность решения (0..1)


class CloudStateDTO(BaseModel):
    """Полное состояние системы для UI"""
    intersections: dict = {}
    cascade_commands: list = []
    green_wave_active: bool = False
