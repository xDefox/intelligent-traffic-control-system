# backend/models/traffic.py
from pydantic import BaseModel, Field
from typing import List

class LaneDetectionDTO(BaseModel):
    lane_id: str         # ID полосы/ребра (например, "lane_west_to_east_1")
    car_count: int       # Количество машин в ROI
    avg_speed: float     # Средняя скорость движения в зоне

class IntersectionUpdateDTO(BaseModel):
    intersection_id: str # ID перекрестка (Fog узел)
    camera_id: str       # ID конкретной камеры
    lanes: List[LaneDetectionDTO]

# Добавляем сюда, чтобы main.py мог её импортировать
class CongestionData(BaseModel):
    camera_id: str = Field(..., description="Уникальный идентификатор или имя камеры из Unity")
    car_count: int = Field(..., ge=0, description="Количество зафиксированных автомобилей в зоне")
    congestion_index: float = Field(..., ge=0.0, le=1.0, description="Индекс затора от 0.0 до 1.0")