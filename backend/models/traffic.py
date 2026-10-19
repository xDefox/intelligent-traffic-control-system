# backend/models/traffic.py

from pydantic import BaseModel, Field


class LaneDetectionDTO(BaseModel):
    lane_id: str         # "intersection_1_approach_0"
    car_count: int = Field(ge=0, description="Количество машин в ROI (не может быть отрицательным)")
    avg_speed: float = Field(default=0.0, ge=0, description="Средняя скорость движения в зоне")
    # ge=0, а не ge=1: если Unity пошлёт 0 (пустое поле в инспекторе) — не роняем
    # весь batch в 422; бэкенд трактует 0 как «вместимость неизвестна» (см.
    # graph_manager.update_lane_state → fallback на бегущий максимум).
    max_capacity: int = Field(default=10, ge=0, description="Сколько машин помещается в кадре этой камеры")


class CameraTelemetryDTO(BaseModel):
    """Телеметрия от одной камеры внутри batch-запроса"""
    camera_id: str
    lanes: list[LaneDetectionDTO]
    # Camera-First Design: метаданные камеры для автоматического построения графа
    direction: str | None = Field(
        default=None,
        description="Направление камеры: N, S, E, W (определяется из transform.forward)"
    )
    world_position: dict | None = Field(
        default=None,
        description="Мировые координаты камеры: {x, y, z}"
    )
    world_rotation: dict | None = Field(
        default=None,
        description="Мировой rotation камеры: {x, y, z} (euler angles)"
    )
    emergency_vehicle_detected: bool = Field(
        default=False,
        description="True если камера детектировала спецтранспорт (полиция, скорая, пожарные)"
    )
    emergency_approach: str | None = Field(
        default=None,
        description="Подход на котором обнаружен спецтранспорт (например 'approach_0')"
    )


class BatchTelemetryDTO(BaseModel):
    """Batch-телеметрия: все камеры одного перекрёстка в одном запросе"""
    intersection_id: str
    cameras: list[CameraTelemetryDTO]


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
    responses: list[SingleResponseDTO]
    emergency_corridor_active: bool = Field(
        default=False,
        description="Активен ли зелёный коридор на этом перекрёстке"
    )
    emergency_corridor_phase: str | None = Field(
        default=None,
        description="Фаза зелёного коридора (например 'EW' или 'NS')"
    )