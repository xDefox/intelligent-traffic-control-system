# backend/services/orchestrator.py
import json
from backend.models.traffic import IntersectionUpdateDTO
from backend.services.traffic_brain import AdaptiveTrafficBrain
from backend.services.graph_manager import traffic_network


class TrafficOrchestrator:
    def __init__(self, ws_manager):
        self.traffic_brains = {}
        self.ws_manager = ws_manager

    async def handle_telemetry(self, update: IntersectionUpdateDTO):
        inter_id = update.intersection_id

        if inter_id not in self.traffic_brains:
            self.traffic_brains[inter_id] = AdaptiveTrafficBrain()

        brain = self.traffic_brains[inter_id]
        target_phase = brain.process_telemetry(update)

        ui_lanes = []
        for lane in update.lanes:
            traffic_network.update_lane_congestion(inter_id, lane.lane_id, lane.car_count)

            lane_axis = brain._resolve_lane_axis(lane.lane_id)

            # Зеленый горит на оси Z, если фаза Z_GREEN, или на оси X, если фаза X_GREEN
            is_green = (lane_axis == "Z" and target_phase == "Z_GREEN") or \
                       (lane_axis == "X" and target_phase == "X_GREEN")

            ui_lanes.append({
                "lane_id": lane.lane_id,
                "car_count": lane.car_count,
                "avg_speed": lane.avg_speed,
                "light": "green" if is_green else "red"
            })

        cascade_actions = traffic_network.get_cascade_commands(inter_id)

        ui_payload = {
            "intersection_id": inter_id,
            "current_phase": target_phase,
            "lanes": ui_lanes
        }
        await self.ws_manager.broadcast(json.dumps(ui_payload))

        return {
            "target_phase": target_phase,
            "cascade_applied": inter_id in cascade_actions
        }