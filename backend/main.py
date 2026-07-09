# backend/main.py
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from backend.models.traffic import IntersectionUpdateDTO, CongestionData
from backend.services.orchestrator import TrafficOrchestrator

app = FastAPI(title="Smart Crossroads UTC-UX Distributed Network", version="0.5.0")

class ConnectionManager:
    def __init__(self):
        self.active_connections = []
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
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()
# Внедряем зависимость (в идеале юзать Depends, но для лабы можно и так)
orchestrator = TrafficOrchestrator(ws_manager=manager)

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
    # Мэйн просто делегирует задачу сервису
    result = await orchestrator.handle_telemetry(update)
    return {
        "status": "processed",
        "current_phase": result["target_phase"],
        "cascade_applied": result["cascade_applied"]
    }

@app.post("/api/v1/update-congestion")
async def update_congestion(data: CongestionData):
    # Логика из старого server_receiver.py теперь живет здесь
    print(f"[Камера: {data.camera_id}] Индекс затора: {data.congestion_index * 100:.1f}%")
    return {"status": "success", "message": f"Данные с камеры '{data.camera_id}' обработаны."}

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8050, reload=True)