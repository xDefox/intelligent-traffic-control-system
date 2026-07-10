# backend/services/orchestrator.py
import json
from typing import Dict
from backend.models.traffic import IntersectionUpdateDTO
from backend.services.traffic_brain import AdaptiveTrafficBrain
from backend.services.graph_manager import traffic_network
from backend.services.cloud_orchestrator import CloudOrchestrator


class TrafficOrchestrator:
    """
    Оркестратор: связывает телеметрию от Unity → Fog-мозг → Cloud → Unity (через WS).
    
    Архитектура Dubai-style: каждый светофор имеет независимый контроллер.
    """

    def __init__(self, ws_manager, cloud: CloudOrchestrator = None):
        # Ключ: lane_id (например "intersection_1_approach_0")
        self.traffic_brains: Dict[str, AdaptiveTrafficBrain] = {}
        self.ws_manager = ws_manager
        self.cloud = cloud

    async def handle_telemetry(self, update: IntersectionUpdateDTO) -> dict:
        """
        Обработать телеметрию с ОДНОЙ камеры/подхода.
        Каждый светофор работает независимо, не ждёт свою фазу.
        """
        inter_id = update.intersection_id
        camera_id = update.camera_id  # Уникальный ID камеры/подхода

        # 1. Создаём независимый мозг для конкретного светофора, если ещё нет
        if camera_id not in self.traffic_brains:
            self.traffic_brains[camera_id] = AdaptiveTrafficBrain(camera_id, is_per_lane=True)
            print(f"🧠 Создан независимый контроллер для {camera_id}")

        brain = self.traffic_brains[camera_id]

        # 2. Применяем каскадные команды от Cloud, если есть
        if self.cloud:
            cascade_commands = self.cloud.get_cascade_commands()
            for cmd in cascade_commands:
                if cmd.get("target_intersection") == inter_id:
                    brain.apply_cascade_command(cmd)

        # 3. Мозг обрабатывает телеметрию и решает команду для этого конкретного светофора
        target_command, green_duration = brain.process_lane_telemetry(update)

        # 4. Обновляем состояние полосы в графе
        for lane in update.lanes:
            traffic_network.update_lane_state(
                lane_id=lane.lane_id,
                car_count=lane.car_count,
                avg_speed=lane.avg_speed,
                max_capacity=lane.max_capacity,
            )

        # 5. Собираем состояние для UI
        ui_lanes = []
        for lane in update.lanes:
            lane_state = traffic_network.lane_pool.get(lane.lane_id, {})
            load_pct = int(lane_state.get("congestion_index", 0) * 100)

            ui_lanes.append({
                "lane_id": lane.lane_id,
                "car_count": lane.car_count,
                "avg_speed": lane.avg_speed,
                "load_pct": load_pct,
                "light": "unknown",  # Контроллер сам решает
            })

        # 6. Шлём состояние в UI через WebSocket
        ui_payload = {
            "type": "lane_update",
            "intersection_id": inter_id,
            "lane_id": camera_id,
            "command": target_command,
            "lanes": ui_lanes,
        }
        if self.ws_manager:
            await self.ws_manager.broadcast(json.dumps(ui_payload))

        return {
            "target_phase": target_command,
            "green_duration": green_duration,
            "cascade_applied": False,
        }
