# backend/main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import asyncio
import json

app = FastAPI(title="Smart Crossroads UTC-UX Prototype", version="0.1.0")


# Схема данных, которую нам будет присылать CV-модуль
class TrafficState(BaseModel):
    intersection_id: int
    north_queue: int
    south_queue: int
    east_queue: int
    west_queue: int
    emergency_vehicle_detected: bool = False


# Глобальное состояние нашего светофора (для демонстрации)
current_traffic_light_state = {
    "current_phase": "NORTH_SOUTH_GREEN",  # Текущая активная фаза
    "phase_timer": 30,  # Сколько секунд осталось
    "system_status": "ONLINE"
}


# Хранилище активных WebSocket соединений (например, для Unity-клиента)
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


# 1. Эндпоинт для приема телеметрии от CV / Unity
@app.post("/api/v1/telemetry")
async def receive_telemetry(state: TrafficState):
    print(f"\n[TELEMETRY] Перекресток {state.intersection_id}:")
    print(
        f"   Очереди -> С: {state.north_queue} | Ю: {state.south_queue} | В: {state.east_queue} | З: {state.west_queue}")

    # Триггер приоритетного проезда (Пункт 07:37 стратегии Дубая)
    if state.emergency_vehicle_detected:
        print("[🚨 CRITICAL] Обнаружен спецтранспорт! Инициирован зелёный коридор.")
        current_traffic_light_state["current_phase"] = "EMERGENCY_OVERRIDE"
        current_traffic_light_state["phase_timer"] = 15
        # Тут будет мгновенная отправка команды в Unity через WebSocket
        await manager.broadcast(json.dumps({"command": "SET_EMERGENCY_PHASE"}))
        return {"status": "override_activated"}

    # Здесь в будущем будет вызов traffic_brain для адаптивного пересчета
    return {"status": "processed", "current_phase": current_traffic_light_state["current_phase"]}


# 2. WebSocket для связи с Unity (управление светофорами в реальном времени)
@app.websocket("/ws/traffic-control")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Ждем данные от Unity (например, подтверждение переключения фазы)
            data = await websocket.receive_text()
            print(f"[UNITY feedback]: {data}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print("[DISCONNECT] Unity-клиент отключился.")