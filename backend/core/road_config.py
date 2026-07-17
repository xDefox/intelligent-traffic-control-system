"""
Конфигурация дорожной сети — ДИНАМИЧЕСКАЯ И ЛЕГКО РАСШИРЯЕМАЯ.

Что здесь задаётся (пространственная топология, легко редактируется):
  - intersections: перечень перекрёстков (id, тип, позиция x/z для раскладки графа)
  - links:        физические связи (дороги) между подходами перекрёстков

Что НЕ задаётся жёстко (определяется из данных):
  - Количество дорог/подходов (1-4) у каждого перекрёстка
  - Фазы светофоров (NS/EW/...)
  Всё это выводится ДИНАМИЧЕСКИ из телеметрии камер
  (см. backend/services/graph_manager.py -> generate_phases / register_approach).

Расширяемость:
  При запуске модуль пытается загрузить road_network.json из той же папки.
  Если файл есть — он переопределяет встроенный DEFAULT_NETWORK (можно
  добавлять перекрёстки/связи, не трогая код). Если файла нет — используется
  встроенный набор. В любом случае число дорог у перекрёстка берётся из данных.
"""

import json
import os


# Встроенный набор (используется, если нет внешнего road_network.json).
# Легко править: добавьте словарь в "intersections" или строку в "links".
DEFAULT_NETWORK = {
    "intersections": [
        {"id": "intersection_1", "type": "T", "position": {"x": 100, "z": 0}},
        {"id": "intersection_2", "type": "X", "position": {"x": 50, "z": 0}},
        {"id": "intersection_3X", "type": "X", "position": {"x": 0, "z": 0}},
        {"id": "intersection_3Z", "type": "X", "position": {"x": 50, "z": 50}},
    ],
    "links": [
        "lane_intersection_1_approach_1 -> lane_intersection_2_approach_0",
        "lane_intersection_2_approach_1 -> lane_intersection_1_approach_0",
        "lane_intersection_2_approach_1 -> lane_intersection_3_approach_0",
        "lane_intersection_3X_approach_1 -> lane_intersection_2_approach_0",
        "lane_intersection_3Z_approach_2 -> lane_intersection_2_approach_3",
    ],
}


def _load_network() -> dict:
    """Загрузить топологию: внешний JSON (если есть) или встроенный набор."""
    here = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(here, "road_network.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and ("intersections" in data or "links" in data):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_NETWORK


_RAW = _load_network()

# Публичный вид конфига, совместимый со старым API (ROADS[intersection_id] -> {...}).
ROADS: dict = {}

for _inter in _RAW.get("intersections", []):
    _iid = _inter["id"]
    ROADS[_iid] = {
        "type": _inter.get("type", "X"),
        "position": _inter.get("position", {"x": 0, "z": 0}),
    }

ROADS["links"] = list(_RAW.get("links", []))


def get_intersection_ids() -> list:
    """Список id всех известных перекрёстков (для инициализации графа)."""
    return [iid for iid in ROADS if iid != "links"]


def get_links() -> list:
    """Список строк-связей 'a -> b'."""
    return list(ROADS.get("links", []))


def get_position(intersection_id: str) -> dict:
    """Позиция перекрёстка {x, z} (для раскладки графа)."""
    return ROADS.get(intersection_id, {}).get("position", {"x": 0, "z": 0})