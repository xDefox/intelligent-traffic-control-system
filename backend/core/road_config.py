"""
Конфиг дорожной сети.

Содержит:
1. Определения перекрёстков с фазами светофоров
2. Связи между перекрёстками для каскадного управления
"""

ROADS = {
    # ========== ПЕРЕКРЁСТКИ ==========
    "intersection_1": {
        "type": "T",
        "position": {"x": 0, "z": 0},
        "phases": {
            # NS = север-юг зелёный (подходы 2,3)
            "NS": {
                "approaches": ["approach_2", "approach_3"],
                "min_duration": 5.0,
                "max_duration": 30.0,
            },
            # EW = восток-запад зелёный (подходы 0,1)
            "EW": {
                "approaches": ["approach_0", "approach_1"],
                "min_duration": 5.0,
                "max_duration": 30.0,
            },
        },
    },
    "intersection_2": {
        "type": "X",
        "position": {"x": -50, "z": 0},
        "phases": {
            "NS": {
                "approaches": ["approach_2", "approach_3"],
                "min_duration": 5.0,
                "max_duration": 30.0,
            },
            "EW": {
                "approaches": ["approach_0", "approach_1"],
                "min_duration": 5.0,
                "max_duration": 30.0,
            },
        },
    },
    
    # ========== СВЯЗИ МЕЖДУ ПЕРЕКРЁСТКАМИ ==========
    "links": [
        # Intersection_1 (подход 1, восток) → Intersection_2 (подход 0, запад)
        "lane_intersection_1_approach_1 -> lane_intersection_2_approach_0",
        # Обратно: Intersection_2 (подход 1, восток) → Intersection_1 (подход 0, запад)
        "lane_intersection_2_approach_1 -> lane_intersection_1_approach_0",
    ],
}
