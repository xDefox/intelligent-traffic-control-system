# backend/main.py
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from backend.models.traffic import BatchResponseDTO, BatchTelemetryDTO
from backend.services.cloud_orchestrator import CloudOrchestrator
from backend.services.orchestrator import TrafficOrchestrator


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом: запуск/остановка cloud-тикера.

    Современная замена deprecated @app.on_event("startup"/"shutdown").
    Ссылается на глобальный `cloud`, создаваемый ниже по модулю.
    """
    cloud.start()
    try:
        yield
    finally:
        await cloud.stop()


app = FastAPI(
    title="Smart Crossroads UTC-UX Distributed Network",
    version="0.7.0",
    lifespan=lifespan,
)


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
            except Exception:  # noqa: BLE001 — мёртвый сокет не должен ронять broadcast остальным
                self.disconnect(connection)


manager = ConnectionManager()

# Cloud-уровень: запускает тикер раз в секунду для каскадного управления
cloud = CloudOrchestrator(ws_manager=manager)

# Fog-оркестратор: принимает телеметрию, общается с Cloud
orchestrator = TrafficOrchestrator(ws_manager=manager, cloud=cloud)


@app.websocket("/ws/monitor")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post("/api/v1/telemetry/batch")
async def receive_batch_telemetry(batch: BatchTelemetryDTO):
    """Batch-эндпоинт: принимает телеметрию от ВСЕХ камер перекрёстка в одном запросе.
    
    Unity шлёт:  1 POST вместо N отдельных POST.
    Backend отвечает: массив команд для каждой камеры.
    """
    responses, emergency_active, emergency_phase = await orchestrator.handle_batch_telemetry(batch)
    
    # Применяем emergency_override через orchestrator (единый источник логики)
    responses = orchestrator.apply_emergency_override(responses, batch.intersection_id, emergency_phase)
    
    return BatchResponseDTO(
        responses=responses,
        emergency_corridor_active=emergency_active,
        emergency_corridor_phase=emergency_phase
    )


@app.get("/api/v1/state")
async def get_full_state():
    """Полное состояние системы для отладки"""
    from backend.services.graph_manager import traffic_network
    return {
        "intersections": traffic_network.get_full_state(),
        "cascade_commands": cloud.get_cascade_commands(),
    }


@app.get("/api/v1/congestion-map")
async def get_congestion_map():
    """
    Camera-First Design: карта загруженности всех полос.
    Unity-машины запрашивают этот endpoint, чтобы не выбирать переполненные дороги.
    Возвращает: { "lane_intersection_1_approach_0": 0.6, ... }
    """
    from backend.services.graph_manager import traffic_network
    return traffic_network.get_congestion_map()


@app.get("/api/v1/statistics")
async def get_statistics():
    """
    Получить статистику трафика.
    
    Возвращает:
    - Время ожидания (average waiting time)
    - Нагруженность (congestion level)
    - Эффективность переключения фаз
    - Исторические данные
    """
    from backend.services.statistics import traffic_stats
    return traffic_stats.get_full_statistics()


@app.get("/api/v1/statistics/intersection/{intersection_id}")
async def get_intersection_statistics(
    intersection_id: str,
    seconds: int = Query(300, ge=1, le=86400, description="Окно истории в секундах"),
):
    """История загрузки перекрёстка (time series) — для карты/графиков."""
    from backend.services.statistics import traffic_stats
    return {
        "intersection_id": intersection_id,
        "history": traffic_stats.get_intersection_timeseries(intersection_id, seconds),
    }


@app.get("/api/v1/statistics/{lane_id}")
async def get_lane_statistics(
    lane_id: str,
    seconds: int = Query(300, ge=1, le=86400, description="Окно истории в секундах"),
    limit: int = Query(50, ge=1, le=3600, description="Максимум записей"),
):
    """
    Получить исторические данные для полосы (time series загрузки её перекрёстка).

    Args:
        lane_id: ID полосы (например, "lane_intersection_1_approach_0")
        seconds: окно истории (по умолчанию 300)
        limit: количество записей (по умолчанию 50)
    """
    from backend.core.lane_utils import parse_lane_id
    from backend.services.statistics import traffic_stats
    inter_id, _ = parse_lane_id(lane_id)
    intersection_id = inter_id if inter_id != "unknown" else lane_id
    history = traffic_stats.get_intersection_timeseries(intersection_id, seconds)
    return {
        "lane_id": lane_id,
        "intersection_id": intersection_id,
        "history": history[:limit],
    }


if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=os.getenv("BACKEND_HOST", "127.0.0.1"),
        port=int(os.getenv("BACKEND_PORT", "8050")),
        reload=True,
    )