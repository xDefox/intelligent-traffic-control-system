# backend/services/cloud_orchestrator.py
import asyncio
import json
from typing import Dict, List
from backend.services.graph_manager import traffic_network
from backend.services.traffic_brain import AdaptiveTrafficBrain
from backend.services.green_wave import green_wave_coordinator


class CloudOrchestrator:
    """
    Cloud-уровень: агрегирует данные со всех перекрёстков,
    раз в секунду запускает каскадный анализ,
    даёт команды Fog-контроллерам.
    """

    def __init__(self, ws_manager=None):
        self.ws_manager = ws_manager
        self._tick_task: asyncio.Task = None
        self._running = False
        self._last_cascade_commands: List[dict] = []

    def start(self):
        """Запустить фоновый тикер (раз в секунду)"""
        if not self._running:
            self._running = True
            self._tick_task = asyncio.create_task(self._tick_loop())
            # print("[CloudOrchestrator] Запущен (тикер раз в 1с)")

    async def stop(self):
        """Остановить тикер"""
        self._running = False
        if self._tick_task:
            self._tick_task.cancel()
        # print("[CloudOrchestrator] Остановлен")

    async def _tick_loop(self):
        """Фоновый цикл: анализ графа и рассылка команд"""
        while self._running:
            try:
                await self._cascade_tick()
            except Exception as e:
                 print(f"❌ [CloudOrchestrator] Ошибка тика: {e}")

            await asyncio.sleep(1.0)

    async def _cascade_tick(self):
        commands = traffic_network.calculate_cascade()
        
        # Добавляем команды зелёной волны
        green_wave_commands = green_wave_coordinator.calculate_green_wave()
        commands.extend(green_wave_commands)
        
        self._last_cascade_commands = commands

        # Считаем агрегаты
        total_cars = sum(d["car_count"] for d in traffic_network.lane_pool.values())
        inter_summary = {}
        for lane_id, data in traffic_network.lane_pool.items():
            iid = data["intersection_id"]
            if iid not in inter_summary:
                inter_summary[iid] = {"total_lanes": 0, "total_cars": 0, "avg_congestion": 0.0}
            inter_summary[iid]["total_lanes"] += 1
            inter_summary[iid]["total_cars"] += data["car_count"]
            inter_summary[iid]["avg_congestion"] += data.get("congestion_index", 0)

        for iid in inter_summary:
            lanes = inter_summary[iid]["total_lanes"]
            inter_summary[iid]["avg_congestion"] /= max(lanes, 1)

        if self.ws_manager:
            # Подсчитываем активные зелёные волны
            active_waves = [c for c in commands if c.get("action") == "GREEN_WAVE_SYNC"]
            green_wave_active = len(active_waves) > 0
            
            state_payload = {
                "type": "cloud_state",
                "total_cars_on_network": total_cars,
                "intersections_summary": inter_summary,
                "cascade_commands": commands,
                "green_wave_active": green_wave_active,
                "green_wave_corridors": [c.get("corridor", []) for c in active_waves],
            }
            await self.ws_manager.broadcast(json.dumps(state_payload))

        # 3. Если есть команды — логируем
        # if commands:
        #     for cmd in commands:
        #         print(f"  ☁️ [Cloud] Команда: {cmd['target_intersection']} -> {cmd['action']}")

    def get_cascade_commands(self) -> List[dict]:
        """Последние каскадные команды (для Fog-контроллеров)"""
        return self._last_cascade_commands