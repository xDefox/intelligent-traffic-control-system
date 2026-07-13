# backend/main.py
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from backend.models.traffic import IntersectionUpdateDTO, BatchTelemetryDTO, BatchResponseDTO, SingleResponseDTO
from backend.services.orchestrator import TrafficOrchestrator
from backend.services.cloud_orchestrator import CloudOrchestrator

app = FastAPI(title="Smart Crossroads UTC-UX Distributed Network", version="0.6.0")


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

# Cloud-уровень: запускает тикер раз в секунду для каскадного управления
cloud = CloudOrchestrator(ws_manager=manager)

# Fog-оркестратор: принимает телеметрию, общается с Cloud
orchestrator = TrafficOrchestrator(ws_manager=manager, cloud=cloud)


@app.on_event("startup")
async def startup():
    cloud.start()


@app.on_event("shutdown")
async def shutdown():
    await cloud.stop()


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
    result = await orchestrator.handle_telemetry(update)
    return {
        "status": "processed",
        "target_phase": result["target_phase"],
        "green_duration": result.get("green_duration", 0.0),
        "cascade_applied": result["cascade_applied"],
    }


@app.post("/api/v1/telemetry/batch")
async def receive_batch_telemetry(batch: BatchTelemetryDTO):
    """Batch-эндпоинт: принимает телеметрию от ВСЕХ камер перекрёстка в одном запросе.
    
    Unity шлёт:  1 POST вместо N отдельных POST.
    Backend отвечает: массив команд для каждой камеры.
    """
    responses = await orchestrator.handle_batch_telemetry(batch)
    return BatchResponseDTO(responses=responses)


@app.get("/api/v1/state")
async def get_full_state():
    """Полное состояние системы для отладки"""
    from backend.services.graph_manager import traffic_network
    return {
        "intersections": traffic_network.get_full_state(),
        "cascade_commands": cloud.get_cascade_commands(),
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8050, reload=True)