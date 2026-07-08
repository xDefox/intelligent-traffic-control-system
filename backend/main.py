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
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)


manager = ConnectionManager()


@app.get("/")
async def root():
    return {"status": "running", "target_infrastructure": "Belarus_ITS"}


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
        manager.disconnect(websocket)
        print("[DISCONNECT] Unity-клиент отключился.")