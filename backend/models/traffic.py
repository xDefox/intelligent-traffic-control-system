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


class CameraTelemetryDTO(BaseModel):
    """Телеметрия от одной камеры внутри batch-запроса"""
    camera_id: str
    lanes: List[LaneDetectionDTO]


class BatchTelemetryDTO(BaseModel):
    """Batch-телеметрия: все камеры одного перекрёстка в одном запросе"""
    intersection_id: str
    cameras: List[CameraTelemetryDTO]


class SingleResponseDTO(BaseModel):
    """Ответ для одной камеры внутри batch-ответа"""
    camera_id: str
    target_phase: str
    green_duration: float = 0.0


class BatchResponseDTO(BaseModel):
    """Ответ на batch-телеметрию"""
    type: str = "batch_response"
    responses: List[SingleResponseDTO]
