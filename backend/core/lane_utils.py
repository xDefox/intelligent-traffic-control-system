"""
Утилиты для работы с lane_id.

Единый источник правил нормализации и парсинга lane_id.
Убирает дублирование кода из orchestrator.py, graph_manager.py, admin_panel.py, main.py.

Формат lane_id: "lane_intersection_1_approach_0" или "intersection_1_approach_0"
"""

from typing import Tuple, Optional


def normalize_lane_id(lane_id: str) -> str:
    """
    Добавить префикс 'lane_' если его нет.

    Примеры:
        "intersection_1_approach_0" -> "lane_intersection_1_approach_0"
        "lane_intersection_1_approach_0" -> "lane_intersection_1_approach_0" (без изменений)
    """
    if not lane_id:
        return lane_id
    return lane_id if lane_id.startswith("lane_") else f"lane_{lane_id}"


def denormalize_lane_id(lane_id: str) -> str:
    """
    Убрать префикс 'lane_' если есть.

    Примеры:
        "lane_intersection_1_approach_0" -> "intersection_1_approach_0"
        "intersection_1_approach_0" -> "intersection_1_approach_0" (без изменений)
    """
    if not lane_id:
        return lane_id
    return lane_id[5:] if lane_id.startswith("lane_") else lane_id


def parse_lane_id(lane_id: str) -> Tuple[str, str]:
    """
    Разобрать lane_id на (intersection_id, approach).

    Поддерживает форматы:
        "lane_intersection_1_approach_0" -> ("intersection_1", "approach_0")
        "intersection_1_approach_0" -> ("intersection_1", "approach_0")

    Если формат не распознан, возвращает ("unknown", lane_id).
    """
    if not lane_id:
        return ("unknown", "")

    # Убираем префикс "lane_"
    if lane_id.startswith("lane_"):
        lane_id = lane_id[5:]

    idx = lane_id.find("_approach_")
    if idx == -1:
        return ("unknown", lane_id)

    inter = lane_id[:idx]
    approach = lane_id[idx + 1:]  # "approach_0"
    return (inter, approach)


def extract_approach_from_camera_id(camera_id: str) -> str:
    """
    Извлечь approach из camera_id.

    Примеры:
        "intersection_1_approach_0" -> "approach_0"
        "intersection_1_approach_2" -> "approach_2"
        "INT_001_E" -> "INT_001_E" (формат не распознан, возвращаем как есть)
    """
    if not camera_id:
        return ""

    if "_approach_" in camera_id:
        direction = camera_id.split("_approach_")[-1]
        return f"approach_{direction}" if not direction.startswith("approach_") else direction

    return camera_id


def extract_intersection_id_from_camera_id(camera_id: str) -> str:
    """
    Извлечь intersection_id из camera_id.

    Пример:
        "intersection_1_approach_0" -> "intersection_1"
    """
    if not camera_id:
        return ""

    if "_approach_" in camera_id:
        return camera_id.split("_approach_")[0]

    return camera_id
