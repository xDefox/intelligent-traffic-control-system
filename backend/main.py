# backend/main.py
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import os

# Исправленные пути: так как корень исполнения — папка backend, импортируем напрямую
from backend.models.traffic import IntersectionUpdateDTO
from backend.services.traffic_brain import AdaptiveTrafficBrain

app = FastAPI(title="Smart Crossroads UTC-UX Prototype", version="0.3.0")

# Инжектим наш сервис управления
traffic_brain = AdaptiveTrafficBrain()


class ConnectionManager:
    def __init__(self):
        # Храним активные сокеты: {"camera_1": websocket, "traffic_light_controller": websocket}
        self.active_connections: dict[str, WebSocket] = {}
        # Храним текущее состояние очередей на перекрестке
        self.traffic_state = {
            "north_queue": 0,
            "south_queue": 0,
            "east_queue": 0,
            "west_queue": 0
        }

    async def connect(self, client_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        print(f"[CONNECT] Подключился клиент: {client_id}")

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            print(f"[DISCONNECT] Отключился клиент: {client_id}")

    async def send_command(self, client_id: str, message: dict):
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_text(json.dumps(message))

    def update_queue(self, camera_id: str, car_count: int):
        """Привязываем ID камеры к направлению графа"""
        if "north" in camera_id.lower():
            self.traffic_state["north_queue"] = car_count
        elif "south" in camera_id.lower():
            self.traffic_state["south_queue"] = car_count
        elif "east" in camera_id.lower():
            self.traffic_state["east_queue"] = car_count
        elif "west" in camera_id.lower():
            self.traffic_state["west_queue"] = car_count


manager = IntersectionManager()


@app.get("/")
async def root():
    return {"status": "fog_node_online", "infrastructure": "Belarus_ITS"}


@app.post("/api/v1/telemetry")
async def receive_telemetry(update: IntersectionUpdateDTO):
    # Вызываем распределенный метод обработки данных (DIP)
    target_phase = traffic_brain.process_telemetry(update)

    os.system('cls' if os.name == 'nt' else 'clear')

    # Красивый вывод логов в консоль Uvicorn
    print(f"\n[TELEMETRY] Перекресток: {update.intersection_id} | Источник: {update.camera_id}")
    print(f"   Обработано полос: {len(update.lanes)}")

    # Пополосный вывод (как было раньше, но теперь динамически)
    for lane in update.lanes:
        print(f"     📍 {lane.lane_id}: {lane.car_count} авт. (скорость: {lane.avg_speed} км/ч)")

    print(f"   🤖 [ИИ Решение]: Выставлена фаза -> {target_phase}")

    # Возвращаем Unity команду управления внутри HTTP-ответа
    return {
        "status": "processed",
        "current_phase": target_phase
    }

@app.websocket("/ws/traffic-control")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            print(f"[UNITY feedback]: {data}")
    except WebSocketDisconnect:
        manager.disconnect(client_id)