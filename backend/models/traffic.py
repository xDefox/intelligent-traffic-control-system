from pydantic import BaseModel
from typing import List

class LaneDetectionDTO(BaseModel):
    lane_id: str         # ID полосы/ребра (например, "lane_west_to_east_1")
    car_count: int       # Количество машин в ROI
    avg_speed: float     # Средняя скорость движения в зоне (если трекается)

class IntersectionUpdateDTO(BaseModel):
    intersection_id: str # ID перекрестка (Fog узел)
    camera_id: str       # ID конкретной камеры
    lanes: List[LaneDetectionDTO]