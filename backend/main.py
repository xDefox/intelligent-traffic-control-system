# backend/main.py
import json
import os  # 1. Добавляем импорт для работы с системой
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.models.traffic import IntersectionUpdateDTO
from backend.services.traffic_brain import AdaptiveTrafficBrain

app = FastAPI(title="Smart Crossroads UTC-UX Prototype", version="0.3.0")
traffic_brain = AdaptiveTrafficBrain()


# ... (класс ConnectionManager оставляем без изменений)

@app.post("/api/v1/telemetry")
async def receive_telemetry(update: IntersectionUpdateDTO):
    # Вызываем метод обработки данных
    target_phase = traffic_brain.process_telemetry(update)

    # 2. Очищаем консоль перед выводом.
    # 'cls' сработает на Windows (CMD/PowerShell), 'clear' — на Linux/MacOS
    os.system('cls' if os.name == 'nt' else 'clear')

    # Красивый статичный вывод (теперь он будет просто обновляться на месте)
    print("=" * 60)
    print(f"[MONITORING] Перекресток: {update.intersection_id} | Источник: {update.camera_id}")
    print(f" Активных направлений: {len(update.lanes)}")
    print("-" * 60)

    for lane in update.lanes:
        print(f"   📍 {lane.lane_id:<12} : {lane.car_count:<2} авт. (скорость: {lane.avg_speed} км/ч)")

    print("-" * 60)
    print(f" 🤖 [ИИ РЕШЕНИЕ]: Текущая фаза светофоров -> {target_phase}")
    print("=" * 60)

    return {
        "status": "processed",
        "current_phase": target_phase
    }