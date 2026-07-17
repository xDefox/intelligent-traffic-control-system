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
    emergency_vehicle_detected: bool = Field(
        default=False,
        description="True если камера детектировала спецтранспорт (полиция, скорая, пожарные)"
    )
    emergency_approach: Optional[str] = Field(
        default=None,
        description="Подход на котором обнаружен спецтранспорт (например 'approach_0')"
    )


class BatchTelemetryDTO(BaseModel):
    """Batch-телеметрия: все камеры одного перекрёстка в одном запросе"""
    intersection_id: str
    cameras: List[CameraTelemetryDTO]


class SingleResponseDTO(BaseModel):
    """Ответ для одной камеры внутри batch-ответа"""
    camera_id: str
    target_phase: str
    green_duration: float = 0.0
    emergency_override: bool = Field(
        default=False,
        description="True если включён режим зелёного коридора для спецтранспорта"
    )


class BatchResponseDTO(BaseModel):
    """Ответ на batch-телеметрию"""
    type: str = "batch_response"
    responses: List[SingleResponseDTO]
    emergency_corridor_active: bool = Field(
        default=False,
        description="Активен ли зелёный коридор на этом перекрёстке"
    )
    emergency_corridor_phase: Optional[str] = Field(
        default=None,
        description="Фаза зелёного коридора (например 'EW' или 'NS')"
    )