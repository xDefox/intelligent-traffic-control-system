# backend/services/cloud_orchestrator.py
import asyncio
import json

from backend.core.logger import debug, error, info
from backend.services.graph_manager import traffic_network
from backend.services.green_wave import green_wave_coordinator
from backend.services.statistics import traffic_stats


class CloudOrchestrator:
    """
    Cloud-уровень: агрегирует данные со всех перекрёстков,
    раз в секунду запускает каскадный анализ,
    даёт команды Fog-контроллерам.
    
    Поддерживает emergency "зелёный коридор" для спецтранспорта:
    - Перекрёсток с emergency принудительно ставит GREEN на фазе спецтранспорта
    - Upstream перекрёстки получают команду GREEN_WAVE для той же фазы
    - Команды emergency имеют приоритет над обычными каскадными командами
    """

    def __init__(self, ws_manager=None):
        self.ws_manager = ws_manager
        self._tick_task: asyncio.Task = None
        self._running = False
        self._last_cascade_commands: list[dict] = []
        
        # Emergency "зелёный коридор"
        self._emergency_active: bool = False
        self._emergency_intersection: str = None
        self._emergency_approach: str = None
        self._emergency_phase: str = None
        self._emergency_timer: float = 10.0  # Длительность удержания emergency
        self._emergency_cascade_done: bool = False
        
        # Для отслеживания изменений (статистика)
        self._prev_green_wave_active: bool = False
        self._prev_intersection_phases: dict[str, str] = {}
    
    def report_emergency(self, intersection_id: str, approach: str, phase: str):
        """
        Сообщить Cloud о детекции спецтранспорта.
        Cloud каскадирует emergency на upstream перекрёстки.

        Вызывается на КАЖДОМ batch, где камера видит спецтранспорт. Поэтому
        повторные вызовы для того же очага не должны создавать новые события в
        статистике или перезапускать каскад — иначе счётчик emergency раздувается,
        а каскадные команды дублируются каждую секунду.
        """
        same_episode = (
            self._emergency_active
            and self._emergency_intersection == intersection_id
            and self._emergency_approach == approach
        )

        if same_episode:
            # Продолжение того же очага: обновляем фазу и продлеваем таймер.
            self._emergency_phase = phase
            self._emergency_timer = 10.0
            return

        # Новый очаг спецтранспорта (другая точка/подход).
        if self._emergency_active:
            traffic_stats.end_emergency()
        traffic_stats.start_emergency(intersection_id, approach, phase)
        info("CloudOrchestrator", f"🚨 EMERGENCY START: {intersection_id}/{approach} phase={phase}")

        self._emergency_active = True
        self._emergency_intersection = intersection_id
        self._emergency_approach = approach
        self._emergency_phase = phase
        self._emergency_timer = 10.0
        self._emergency_cascade_done = False
    
    def start(self):
        """Запустить фоновый тикер (раз в секунду)"""
        if not self._running:
            self._running = True
            self._tick_task = asyncio.create_task(self._tick_loop())
            debug("CloudOrchestrator", "Started (ticker every 1s)")
    
    async def stop(self):
        """Остановить тикер и дождаться завершения фоновой задачи."""
        self._running = False
        if self._tick_task:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
            self._tick_task = None
        debug("CloudOrchestrator", "Stopped")
    
    async def _tick_loop(self):
        """Фоновый цикл: анализ графа и рассылка команд"""
        while self._running:
            try:
                await self._cascade_tick()
            except Exception as e:  # noqa: BLE001 — тикер обязан выживать после любой ошибки тика
                error("CloudOrchestrator", f"Tick error: {e}")

            await asyncio.sleep(1.0)
    
    async def _cascade_tick(self):
        """Главный тик: собрать команды, обработать emergency и green wave, записать статистику, broadcast."""
        # 1. Базовые каскадные команды + green wave
        commands = traffic_network.calculate_cascade()
        green_wave_commands = green_wave_coordinator.calculate_green_wave()
        
        # 2. Обработка green wave (логирование в статистике)
        self._process_green_wave(green_wave_commands)
        commands.extend(green_wave_commands)
        
        # 3. Обработка emergency "зелёного коридора"
        self._process_emergency(commands)
        
        # 4. Сохраняем команды
        self._last_cascade_commands = commands
        
        # 5. Вычисляем агрегаты по перекрёсткам
        total_cars, inter_summary = self._compute_intersection_summary()
        
        # 6. Запись статистики (переключения фаз; congestion snapshot пишет orchestrator)
        self._record_statistics(inter_summary, commands)
        
        # 7. Broadcast состояния
        await self._broadcast_state(commands, total_cars, inter_summary)

    # ===================== ВЫДЕЛЕННЫЕ МЕТОДЫ =====================

    def _process_green_wave(self, green_wave_commands: list[dict]):
        """Обработать команды зелёной волны: логирование в статистике при изменении состояния."""
        gw_cmds = [c for c in green_wave_commands if c.get("action") == "GREEN_WAVE_SYNC"]
        current_gw_active = len(gw_cmds) > 0
        if current_gw_active and not self._prev_green_wave_active:
            # Волна началась. Дедуп по коридору: команд в коридоре столько же,
            # сколько перекрёстков, а событие волны должно быть одно.
            seen_corridors = set()
            for wave in gw_cmds:
                corridor = tuple(wave.get("corridor", []))
                if corridor and corridor not in seen_corridors:
                    seen_corridors.add(corridor)
                    traffic_stats.start_green_wave(list(corridor), wave.get("phase", "UNKNOWN"))
        elif not current_gw_active and self._prev_green_wave_active:
            # Волна закончилась
            traffic_stats.end_green_wave()
        self._prev_green_wave_active = current_gw_active

    def _process_emergency(self, commands: list[dict]):
        """Обработать emergency "зелёный коридор": добавить команды и каскадировать на upstream."""
        if not self._emergency_active:
            return

        self._emergency_timer -= 1.0  # Тик раз в секунду
        
        # Добавляем EMERGENCY команду для текущего перекрёстка
        emergency_cmd = {
            "target_intersection": self._emergency_intersection,
            "action": "EMERGENCY_GREEN",
            "approach": self._emergency_approach,
            "phase": self._emergency_phase,
            "reason": f"🚨 Спецтранспорт на {self._emergency_approach}",
            "emergency": True,
        }
        commands.insert(0, emergency_cmd)  # Приоритет над всеми
        
        # Каскадируем на upstream перекрёстки (те, откуда едет спецтранспорт)
        if not self._emergency_cascade_done:
            upstream_map = traffic_network.get_upstream_intersections(self._emergency_intersection)
            if self._emergency_approach in upstream_map:
                for up_inter in upstream_map[self._emergency_approach]:
                    # Подход на upstream-перекрёстке, по которому едет спецтранспорт,
                    # определяется по ребру графа (up_inter, up_app) -> (emergency, approach).
                    up_approach = traffic_network.get_approach_toward(
                        up_inter, self._emergency_intersection, self._emergency_approach)
                    up_phase = None
                    if up_approach:
                        up_phase = traffic_network.get_phase_for_approach(up_inter, up_approach)
                    if up_phase is None:
                        # Fallback: первый зарегистрированный подход upstream-перекрёстка
                        for data in traffic_network.lane_pool.values():
                            if data["intersection_id"] == up_inter:
                                up_phase = traffic_network.get_phase_for_approach(up_inter, data["approach"])
                                break

                    cascade_cmd = {
                        "target_intersection": up_inter,
                        "action": "EMERGENCY_GREEN",
                        "approach": up_approach if up_approach else self._emergency_approach,
                        "phase": up_phase,
                        "reason": f"🚨 Каскад: спецтранспорт едет к {self._emergency_intersection}",
                        "emergency": True,
                    }
                    commands.insert(0, cascade_cmd)
                    info("CloudOrchestrator", f"🚨 Cascade EMERGENCY on {up_inter} approach={up_approach} phase={up_phase}")
            
            self._emergency_cascade_done = True
        
        # Если таймер истёк — сбрасываем emergency
        if self._emergency_timer <= 0:
            info("CloudOrchestrator", f"✅ Emergency completed on {self._emergency_intersection}")
            traffic_stats.end_emergency()
            self._emergency_active = False
            self._emergency_intersection = None
            self._emergency_approach = None
            self._emergency_phase = None
            self._emergency_cascade_done = False

    def _compute_intersection_summary(self) -> tuple[int, dict[str, dict]]:
        """
        Вычислить агрегаты по перекрёсткам: общее число машин
        и сводка по каждому перекрёстку.
        """
        total_cars = sum(d["car_count"] for d in traffic_network.lane_pool.values())
        inter_summary: dict[str, dict] = {}
        
        for data in traffic_network.lane_pool.values():
            iid = data["intersection_id"]
            if iid not in inter_summary:
                inter_summary[iid] = {"total_lanes": 0, "total_cars": 0, "avg_congestion": 0.0}
            inter_summary[iid]["total_lanes"] += 1
            inter_summary[iid]["total_cars"] += data["car_count"]
            inter_summary[iid]["avg_congestion"] += data.get("congestion_index", 0)
        
        for summary in inter_summary.values():
            summary["avg_congestion"] /= max(summary["total_lanes"], 1)
        
        return total_cars, inter_summary

    def _record_statistics(self, inter_summary: dict[str, dict], commands: list[dict]):
        """Отслеживание переключений фаз по тикам (раз в секунду).

        Congestion-snapshot записывается в orchestrator._record_statistics()
        по каждой пришедшей телеметрии — здесь повторно не дублируем.
        """
        for iid in inter_summary:
            # Определяем текущую фазу перекрёстка из lane_pool или cascade команд
            current_phase = self._get_intersection_phase(iid, commands)
            
            # Если фаза изменилась с предыдущего тика - это переключение
            prev_phase = self._prev_intersection_phases.get(iid)
            if prev_phase and prev_phase != current_phase:
                traffic_stats.record_phase_switch(iid)
            self._prev_intersection_phases[iid] = current_phase

        # Агрегат по сети (для графика загрузки во времени) — раз в тик.
        if inter_summary:
            total_cars = sum(s["total_cars"] for s in inter_summary.values())
            avg_cong = sum(s["avg_congestion"] for s in inter_summary.values()) / len(inter_summary)
            traffic_stats.record_network_snapshot(total_cars, avg_cong, len(inter_summary))

    async def _broadcast_state(self, commands: list[dict], total_cars: int,
                               inter_summary: dict[str, dict]):
        """Рассылка состояния через WebSocket для админ-панели."""
        if not self.ws_manager:
            return
        
        # Подсчитываем активные зелёные волны (дедуп по коридору: в коридоре
        # по одной GREEN_WAVE_SYNC-команде на перекрёсток).
        seen_corridors = set()
        unique_corridors = []
        for c in commands:
            if c.get("action") == "GREEN_WAVE_SYNC":
                key = tuple(c.get("corridor", []))
                if key and key not in seen_corridors:
                    seen_corridors.add(key)
                    unique_corridors.append(list(key))
        green_wave_active = len(unique_corridors) > 0

        # Получаем статистику для UI (передаём через WebSocket, т.к. админка в другом процессе)
        stats_data = traffic_stats.get_full_statistics()

        state_payload = {
            "type": "cloud_state",
            "total_cars_on_network": total_cars,
            "intersections_summary": inter_summary,
            "cascade_commands": commands,
            "green_wave_active": green_wave_active,
            "green_wave_corridors": unique_corridors,
            "statistics": stats_data,  # Полная статистика для UI
        }
        await self.ws_manager.broadcast(json.dumps(state_payload))

    def _get_intersection_phase(self, intersection_id: str, commands: list[dict]) -> str:
        """
        Определить текущую фазу перекрёстка.

        Сначала lane_pool (фактическая фаза, которую опубликовал оркестратор),
        потом cascade-команды. Раньше смотрели команды В ПЕРВУЮ очередь — из-за
        этого команда зелёной волны «навязывала» фазу и счётчик переключений
        дрожал на каждом тике.
        """
        # 1) Фактическая фаза из lane_pool
        for data in traffic_network.lane_pool.values():
            if data["intersection_id"] == intersection_id:
                phase = data.get("current_phase")
                if phase:
                    return phase

        # 2) Fallback: фаза из команды
        for cmd in commands:
            if cmd.get("target_intersection") == intersection_id:
                phase = cmd.get("phase")
                if phase:
                    return phase

        return "UNKNOWN"

    def get_cascade_commands(self) -> list[dict]:
        """Последние каскадные команды (для Fog-контроллеров)"""
        return self._last_cascade_commands