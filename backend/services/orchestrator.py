# backend/services/orchestrator.py
import json
from typing import Dict
from backend.models.traffic import IntersectionUpdateDTO, BatchTelemetryDTO, SingleResponseDTO, CameraTelemetryDTO
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

        # 3. Мозг обрабатывает телеметрию.
        #    ВАЖНО: process_lane_telemetry() уже обновляет lane_pool внутри себя через
        #    traffic_network.update_lane_state(), поэтому НЕ дублируем вызов.
        target_command, green_duration = brain.process_lane_telemetry(update)

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

    async def handle_batch_telemetry(self, batch: BatchTelemetryDTO) -> list:
        """
        Обработать batch-телеметрию от ВСЕХ камер одного перекрёстка.
        
        В Dubai-архитектуре каждый светофор независим, но мы объединяем
        N HTTP запросов в 1 для снижения накладных расходов.
        """
        inter_id = batch.intersection_id
        responses = []

        for cam in batch.cameras:
            # Создаём IntersectionUpdateDTO для каждой камеры (сохраняем Dubai-архитектуру)
            fake_update = IntersectionUpdateDTO(
                intersection_id=inter_id,
                camera_id=cam.camera_id,
                lanes=cam.lanes,
            )

            result = await self.handle_telemetry(fake_update)

            responses.append(SingleResponseDTO(
                camera_id=cam.camera_id,
                target_phase=result["target_phase"],
                green_duration=result.get("green_duration", 0.0),
            ))

        return responses
