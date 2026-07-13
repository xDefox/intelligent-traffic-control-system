# backend/services/cloud_orchestrator.py
import asyncio
import json
from typing import Dict, List
from backend.services.graph_manager import traffic_network
from backend.services.traffic_brain import AdaptiveTrafficBrain


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
        """Один тик: проверить граф, разослать команды"""
        # 1. Получаем каскадные команды от графа
        commands = traffic_network.calculate_cascade()
        self._last_cascade_commands = commands

        # 2. Шлём состояние в UI
        if self.ws_manager:
            state_payload = {
                "type": "cloud_state",
                "cascade_commands": commands,
                "green_wave_active": any(
                    c["action"] == "GREEN_WAVE" for c in commands
                ),
            }
            await self.ws_manager.broadcast(json.dumps(state_payload))

        # 3. Если есть команды — логируем
        # if commands:
        #     for cmd in commands:
        #         print(f"  ☁️ [Cloud] Команда: {cmd['target_intersection']} -> {cmd['action']}")

    def get_cascade_commands(self) -> List[dict]:
        """Последние каскадные команды (для Fog-контроллеров)"""
        return self._last_cascade_commands