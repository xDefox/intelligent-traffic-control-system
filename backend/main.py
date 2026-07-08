# backend/main.py
import json
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from backend.models.traffic import IntersectionUpdateDTO
from backend.services.traffic_brain import AdaptiveTrafficBrain

app = FastAPI(title="Smart Crossroads UTC-UX Prototype", version="0.3.0")
traffic_brain = AdaptiveTrafficBrain()


# --- МЕНЕДЖЕР ВЕБ-СОКЕТОВ ---
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
        # Итерируемся по копии списка, чтобы избежать багов при удалении во время цикла
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
            await websocket.receive_text()  # Удерживаем соединение открытым
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post("/api/v1/telemetry")
async def receive_telemetry(update: IntersectionUpdateDTO):
    # Вызываем метод обработки данных в мозге
    target_phase = traffic_brain.process_telemetry(update)

    # Очищаем консоль перед выводом
    os.system('cls' if os.name == 'nt' else 'clear')

    # Красивый статичный вывод в терминал
    print("=" * 60)
    print(f"[MONITORING] Перекресток: {update.intersection_id} | Источник: {update.camera_id}")
    print(f" Активных направлений: {len(update.lanes)}")
    print("-" * 60)

    ui_lanes = []
    for lane in update.lanes:
        print(f"   📍 {lane.lane_id:<12} : {lane.car_count:<2} авт. (скорость: {lane.avg_speed} км/ч)")

        # Заменяем обращение к приватному методу мозга на чистую валидацию строк
        lane_lower = lane.lane_id.lower()
        if "side" in lane_lower or "north" in lane_lower or "south" in lane_lower:
            lane_phase = "SIDE_GREEN"
        else:
            lane_phase = "MAIN_GREEN"

        # Формируем структуру полосы для отправки по сокету
        ui_lanes.append({
            "lane_id": lane.lane_id,
            "car_count": lane.car_count,
            "avg_speed": lane.avg_speed,
            "light": "green" if lane_phase == target_phase else "red"
        })

    print("-" * 60)
    print(f" 🤖 [ИИ РЕШЕНИЕ]: Текущая фаза светофоров -> {target_phase}")
    print("=" * 60)

    # Пакет для независимой админки Flet
    ui_payload = {
        "intersection_id": update.intersection_id,
        "current_phase": target_phase,
        "lanes": ui_lanes
    }

    # Рассылаем данные клиентам
    await manager.broadcast(json.dumps(ui_payload))

    return {
        "status": "processed",
        "current_phase": target_phase
    }