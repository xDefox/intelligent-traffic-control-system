"""
Минимальный конфиг дорожной сети.

Здесь только связи между перекрёстками для каскадного управления.
Формат: lane_id одного перекрёстка -> lane_id другого перекрёстка.
"""

ROADS = {
    "links": [
        # Intersection_1 (T) соединён с Intersection_2 (X) по оси -X
        # lane_intersection_1_X_1 → lane_intersection_2_X_0
        "lane_intersection_1_X_1 -> lane_intersection_2_X_0",
        # обратно
        "lane_intersection_2_X_1 -> lane_intersection_1_X_0",
    ],
}