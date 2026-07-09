# backend/main.py
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from backend.models.traffic import IntersectionUpdateDTO
from backend.services.traffic_brain import AdaptiveTrafficBrain
from backend.services.graph_manager import traffic_network  # Наш граф!

app = FastAPI(title="Smart Crossroads UTC-UX Distributed Network", version="0.4.0")

# Хранилище мозгов для каждого перекрёстка, чтобы они не путали свои фазы
traffic_brains: dict[str, AdaptiveTrafficBrain] = {}


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except (WebSocketDisconnect, Exception):
                self.disconnect(connection)


manager = ConnectionManager()


@app.websocket("/ws/monitor")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post("/api/v1/telemetry")
async def receive_telemetry(update: IntersectionUpdateDTO):
    inter_id = update.intersection_id

    # Динамически создаем мозг перекрёстку, если его ещё нет в сети
    if inter_id not in traffic_brains:
        traffic_brains[inter_id] = AdaptiveTrafficBrain()

    brain = traffic_brains[inter_id]

    # 1. Считаем локальное решение ИИ для этого перекрёстка
    target_phase = brain.process_telemetry(update)

    ui_lanes = []
    print(f"\n📡 [TELEMETRY] Node: {inter_id} reporting...")

    for lane in update.lanes:
        # 2. Обновляем веса в глобальном графе дорожной сети
        current_weight = traffic_network.update_lane_congestion(inter_id, lane.lane_id, lane.car_count)

        lane_lower = lane.lane_id.lower()
        lane_phase = "SIDE_GREEN" if any(x in lane_lower for x in ["side", "north", "south"]) else "MAIN_GREEN"

        ui_lanes.append({
            "lane_id": lane.lane_id,
            "car_count": lane.car_count,
            "avg_speed": lane.avg_speed,
            "light": "green" if lane_phase == target_phase else "red"
        })

    # 3. Проверяем каскадные заторы по всему городу
    cascade_actions = traffic_network.get_cascade_commands(inter_id)
    if cascade_actions:
        print(f"⚠️ [CASCADE TRIGGERED BY {inter_id}]: {cascade_actions}")
        # Здесь в будущем полетит команда оптимизации на соседний перекрёсток!

    # UI Payload теперь полностью изолирован по intersection_id
    ui_payload = {
        "intersection_id": inter_id,
        "current_phase": target_phase,
        "lanes": ui_lanes
    }

    await manager.broadcast(json.dumps(ui_payload))

    return {
        "status": "processed",
        "current_phase": target_phase,
        "cascade_applied": inter_id in cascade_actions
    }