# backend/services/cloud_orchestrator.py
import asyncio
import json
from typing import Dict, List
from backend.services.graph_manager import traffic_network
from backend.services.green_wave import green_wave_coordinator
from backend.services.statistics import traffic_stats
from backend.core.logger import debug, info, warning, error


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
        self._last_cascade_commands: List[dict] = []
        
        # Emergency "зелёный коридор"
        self._emergency_active: bool = False
        self._emergency_intersection: str = None
        self._emergency_approach: str = None
        self._emergency_phase: str = None
        self._emergency_timer: float = 10.0  # Длительность удержания emergency
        self._emergency_cascade_done: bool = False
        
        # Для отслеживания изменений (статистика)
        self._prev_green_wave_active: bool = False
        self._prev_intersection_phases: Dict[str, str] = {}
    
    def report_emergency(self, intersection_id: str, approach: str, phase: str):
        """
        Сообщить Cloud о детекции спецтранспорта.
        Cloud каскадирует emergency на upstream перекрёстки.
        """
        self._emergency_active = True
        self._emergency_intersection = intersection_id
        self._emergency_approach = approach
        self._emergency_phase = phase
        self._emergency_timer = 10.0  # Сброс таймера
        self._emergency_cascade_done = False
        traffic_stats.start_emergency(intersection_id, approach, phase)
        info("CloudOrchestrator", f"🚨 EMERGENCY: {intersection_id}/{approach} phase={phase}")

    def start(self):
        """Запустить фоновый тикер (раз в секунду)"""
        if not self._running:
            self._running = True
            self._tick_task = asyncio.create_task(self._tick_loop())
            debug("CloudOrchestrator", "Started (ticker every 1s)")

    async def stop(self):
        """Остановить тикер"""
        self._running = False
        if self._tick_task:
            self._tick_task.cancel()
        debug("CloudOrchestrator", "Stopped")

    async def _tick_loop(self):
        """Фоновый цикл: анализ графа и рассылка команд"""
        while self._running:
            try:
                await self._cascade_tick()
            except Exception as e:
                error("CloudOrchestrator", f"Tick error: {e}")

            await asyncio.sleep(1.0)

    async def _cascade_tick(self):
        commands = traffic_network.calculate_cascade()
        
        # Добавляем команды зелёной волны
        green_wave_commands = green_wave_coordinator.calculate_green_wave()
        
        # Логируем зелёные волны в статистике (только при изменении состояния)
        current_gw_active = len([c for c in green_wave_commands if c.get("action") == "GREEN_WAVE_SYNC"]) > 0
        if current_gw_active and not self._prev_green_wave_active:
            # Волна началась
            for wave in green_wave_commands:
                if wave.get("action") == "GREEN_WAVE_SYNC":
                    corridor = wave.get("corridor", [])
                    gw_phase = wave.get("phase", "UNKNOWN")
                    if corridor:
                        traffic_stats.start_green_wave(corridor, gw_phase)
        elif not current_gw_active and self._prev_green_wave_active:
            # Волна закончилась
            traffic_stats.end_green_wave()
        self._prev_green_wave_active = current_gw_active
        
        commands.extend(green_wave_commands)
        
        # ===== EMERGENCY "ЗЕЛЁНЫЙ КОРИДОР" =====
        if self._emergency_active:
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
                        # Определяем фазу на upstream перекрёстке для этого подхода
                        up_phase = None
                        for lane_id, data in traffic_network.lane_pool.items():
                            if data["intersection_id"] == up_inter:
                                up_phase = traffic_network.get_phase_for_approach(up_inter, data["approach"])
                                break
                        
                        cascade_cmd = {
                            "target_intersection": up_inter,
                            "action": "EMERGENCY_GREEN",
                            "approach": self._emergency_approach,
                            "phase": up_phase,
                            "reason": f"🚨 Каскад: спецтранспорт едет к {self._emergency_intersection}",
                            "emergency": True,
                        }
                        commands.insert(0, cascade_cmd)
                        info("CloudOrchestrator", f"🚨 Cascade EMERGENCY on {up_inter} phase={up_phase}")
                
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
        
        self._last_cascade_commands = commands

        # Считаем агрегаты и пишем статистику
        total_cars = sum(d["car_count"] for d in traffic_network.lane_pool.values())
        inter_summary = {}
        lane_congestions_by_inter: Dict[str, Dict[str, float]] = {}
        for lane_id, data in traffic_network.lane_pool.items():
            iid = data["intersection_id"]
            if iid not in inter_summary:
                inter_summary[iid] = {"total_lanes": 0, "total_cars": 0, "avg_congestion": 0.0}
                lane_congestions_by_inter[iid] = {}
            inter_summary[iid]["total_lanes"] += 1
            inter_summary[iid]["total_cars"] += data["car_count"]
            inter_summary[iid]["avg_congestion"] += data.get("congestion_index", 0)
            lane_congestions_by_inter[iid][lane_id] = data.get("congestion_index", 0.0)

        for iid in inter_summary:
            lanes = inter_summary[iid]["total_lanes"]
            inter_summary[iid]["avg_congestion"] /= max(lanes, 1)

        # Отслеживаем переключения фаз и записываем congestion snapshot
        for iid in inter_summary:
            # Определяем текущую фазу перекрёстка из lane_pool или cascade команд
            current_phase = self._get_intersection_phase(iid, commands)
            
            # Если фаза изменилась с предыдущего тика - это переключение
            prev_phase = self._prev_intersection_phases.get(iid)
            if prev_phase and prev_phase != current_phase:
                traffic_stats.record_phase_switch(iid)
            self._prev_intersection_phases[iid] = current_phase
            
            traffic_stats.record_congestion_snapshot(
                intersection_id=iid,
                lane_congestions=lane_congestions_by_inter.get(iid, {}),
                total_cars=inter_summary[iid]["total_cars"],
                active_lanes=inter_summary[iid]["total_lanes"],
                phase=current_phase,
            )

        if self.ws_manager:
            # Подсчитываем активные зелёные волны
            active_waves = [c for c in commands if c.get("action") == "GREEN_WAVE_SYNC"]
            green_wave_active = len(active_waves) > 0
            
            # Получаем статистику для UI (передаём через WebSocket, т.к. админка в другом процессе)
            stats_data = traffic_stats.get_full_statistics()
            
            state_payload = {
                "type": "cloud_state",
                "total_cars_on_network": total_cars,
                "intersections_summary": inter_summary,
                "cascade_commands": commands,
                "green_wave_active": green_wave_active,
                "green_wave_corridors": [c.get("corridor", []) for c in active_waves],
                "statistics": stats_data,  # Полная статистика для UI
            }
            await self.ws_manager.broadcast(json.dumps(state_payload))

        # 3. Если есть команды — логируем
        # if commands:
        #     for cmd in commands:
        #         print(f"  ☁️ [Cloud] Команда: {cmd['target_intersection']} -> {cmd['action']}")

    def _get_intersection_phase(self, intersection_id: str, commands: List[dict]) -> str:
        """
        Определить текущую фазу перекрёстка.
        Сначала смотрит в cascade commands, потом в lane_pool.
        """
        # Ищем в командах
        for cmd in commands:
            if cmd.get("target_intersection") == intersection_id:
                phase = cmd.get("phase")
                if phase:
                    return phase
        
        # Ищем в lane_pool
        for lane_id, data in traffic_network.lane_pool.items():
            if data["intersection_id"] == intersection_id:
                phase = data.get("current_phase")
                if phase:
                    return phase
        
        return "UNKNOWN"

    def get_cascade_commands(self) -> List[dict]:
        """Последние каскадные команды (для Fog-контроллеров)"""
        return self._last_cascade_commands
