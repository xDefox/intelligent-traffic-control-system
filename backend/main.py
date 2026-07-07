import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

app = FastAPI(
    title="Smart Crossroads - Dubai Fog Server",
    description="Управляющий контроллер перекрестка. Принимает данные с камер по WS и считает фазы.",
    version="0.2.0"
)


# Глобальный менеджер WebSocket-соединений
class IntersectionManager:
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


# Единый WebSocket-канал для симуляции Unity
@app.websocket("/ws/traffic-control/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(client_id, websocket)
    try:
        while True:
            # Принимаем текстовый пакет от Unity (камеры или контроллера светофоров)
            data = await websocket.receive_text()
            payload = json.loads(data)

            # Если пакет пришел от камеры ИИ
            if "camera" in client_id.lower():
                cars = payload.get("car_count", 0)
                congestion = payload.get("congestion_index", 0.0)

                # Обновляем состояние очередей для нашего графа
                manager.update_queue(client_id, cars)

                print(f"[DATA] {client_id} -> Машин: {cars} | Затор: {congestion * 100:.1f}%")
                print(f"       Текущие очереди на перекрестке: {manager.traffic_state}")

                # Тут в будущем будет дергаться `engine.py` для пересчета таймингов
                # И если нужно поменять фазу, отправляем команду светофору:
                # await manager.send_command("traffic_light_controller", {"command": "SET_PHASE", "phase": "EAST_WEST"})

            # Если пакет пришел от самого светофора (например, подтверждение смены фазы)
            elif client_id == "traffic_light_controller":
                print(f"[LIGHT FEEDBACK]: {payload}")

    except WebSocketDisconnect:
        manager.disconnect(client_id)