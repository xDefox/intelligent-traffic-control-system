# backend/services/orchestrator.py
import json
from typing import Dict, List
from backend.models.traffic import (
    IntersectionUpdateDTO, BatchTelemetryDTO, SingleResponseDTO, CameraTelemetryDTO
)
from backend.services.traffic_brain import (
    AdaptiveTrafficBrain,
    _get_intersection_phase_state,
)
from backend.services.graph_manager import traffic_network
from backend.services.cloud_orchestrator import CloudOrchestrator
from backend.services.green_wave import green_wave_coordinator
import time


class TrafficOrchestrator:
    """
    Оркестратор.
    
    Dubai-style: каждый светофор имеет независимый контроллер (per-lane).
    Но при batch: одно решение на перекрёсток.
    """

    def __init__(self, ws_manager, cloud: CloudOrchestrator = None):
        self.traffic_brains: Dict[str, AdaptiveTrafficBrain] = {}
        self.ws_manager = ws_manager
        self.cloud = cloud
        # Each intersection has its own independent phase state
        self._intersection_phase_states: Dict[str, dict] = {}

    async def handle_telemetry(self, update: IntersectionUpdateDTO) -> dict:
        """Обработать телеметрию с одной камеры."""
        inter_id = update.intersection_id
        camera_id = update.camera_id

        if camera_id not in self.traffic_brains:
            self.traffic_brains[camera_id] = AdaptiveTrafficBrain(camera_id, is_per_lane=True)

        brain = self.traffic_brains[camera_id]

        if self.cloud:
            for cmd in self.cloud.get_cascade_commands():
                if cmd.get("target_intersection") == inter_id:
                    brain.apply_cascade_command(cmd)

        target_command, green_duration = brain.process_lane_telemetry(update)

        # Определяем активную фазу для этого перекрёстка
        # Определяем активную фазу
        approach = camera_id.replace(f"{inter_id}_", "")
        lane_phase = traffic_network.get_phase_for_approach(inter_id, approach)
        phase_state = _get_intersection_phase_state(inter_id)
        active_phase = phase_state.get("active_phase", "UNKNOWN")

        ui_lanes = []
        for lane in update.lanes:
            lane_state = traffic_network.lane_pool.get(lane.lane_id, {})
            ui_lanes.append({
                "lane_id": lane.lane_id,
                "car_count": lane.car_count,
                "avg_speed": lane.avg_speed,
                "load_pct": int(lane_state.get("congestion_index", 0) * 100),
                "light": target_command,
                "phase_name": lane_phase or "UNKNOWN",
                "max_capacity": lane.max_capacity,
            })

        ui_payload = {
            "type": "lane_update",
            "intersection_id": inter_id,
            "lane_id": camera_id,
            "command": target_command,
            "current_phase": active_phase,
            "green_duration": green_duration,
            "lanes": ui_lanes,
        }
        if self.ws_manager:
            await self.ws_manager.broadcast(json.dumps(ui_payload))

        return {"target_phase": target_command, "green_duration": green_duration, "cascade_applied": False}

    async def handle_batch_telemetry(self, batch: BatchTelemetryDTO) -> list:
        """
        Обработать batch-телеметрию от ВСЕХ камер одного перекрёстка.
        
        1. Обновляем lane_pool от ВСЕХ камер
        2. Принимаем ОДНО решение о фазе
        3. Сразу возвращаем ответы (без цикла по handle_telemetry!)
        """
        inter_id = batch.intersection_id
        # print(f"📥 [{inter_id}] Получен batch: {len(batch.cameras)} камер")
        
        # Получаем команды зелёной волны
        green_wave_commands = green_wave_coordinator.calculate_green_wave()
        
        # ШАГ 1: Обновляем lane_pool от ВСЕХ камер
        for cam in batch.cameras:
            for lane in cam.lanes:
                traffic_network.update_lane_state(
                    lane_id=lane.lane_id,
                    car_count=lane.car_count,
                    avg_speed=lane.avg_speed,
                    max_capacity=lane.max_capacity,
                )
        
        # ШАГ 2: Конфиг фаз
        phases_config = traffic_network.intersection_phases.get(inter_id, {})
        phase_names = list(phases_config.keys())
        
        if not phase_names:
            return [SingleResponseDTO(camera_id=c.camera_id, target_phase="RED", green_duration=0.0) for c in batch.cameras]
        
        # ШАГ 3: Решение о фазе (ИСПОЛЬЗУЕМ ПЕРЕКРЁСТКО-СПЕЦИФИЧНОЕ СОСТОЯНИЕ)
        if inter_id not in self._intersection_phase_states:
            self._intersection_phase_states[inter_id] = {
                "active_phase": None,
                "phase_start_time": 0,
                "min_duration": 8.0,
                "max_duration": 30.0,
            }
            
        phase_state = self._intersection_phase_states[inter_id]
        active_phase = phase_state.get("active_phase")
        phase_start_time = phase_state.get("phase_start_time", 0)
        elapsed = time.time() - phase_start_time if active_phase else 999
        
        # Проверяем, есть ли команда зелёной волны для этого перекрёстка
        green_wave_override = None
        for gw_cmd in green_wave_commands:
            if gw_cmd.get("target_intersection") == inter_id:
                green_wave_override = gw_cmd
                break
        
        # Считаем машины на каждой фазе
        phase_cars = {pn: 0 for pn in phase_names}
        for lane_id, data in traffic_network.lane_pool.items():
            if data["intersection_id"] != inter_id:
                continue
            lane_phase = traffic_network.get_phase_for_approach(inter_id, data["approach"])
            # print(f"  🔎 [{inter_id}] {lane_id} (approach {data['approach']}) → фаза {lane_phase}, машин: {data['car_count']}")
            if lane_phase in phase_cars:
                phase_cars[lane_phase] += data["car_count"]
        
        # print(f"  📊 [{inter_id}] Машины по фазам: {phase_cars}")
        # print(f"  🔍 [{inter_id}] Lane pool: {[(k, v['approach'], v['car_count']) for k,v in traffic_network.lane_pool.items() if v['intersection_id'] == inter_id]}")
        
        # Применяем зелёную волну если есть команда
        if green_wave_override:
            gw_phase = green_wave_override.get("phase")
            gw_offset = green_wave_override.get("offset", 0.0)
            
            # Проверяем, поддерживается ли эта фаза на перекрёстке
            if gw_phase in phase_names:
                # Рассчитываем, должна ли сейчас гореть зелёная волна
                # Используем offset как сдвиг времени начала цикла
                cycle_time = 60.0  # Полный цикл светофора (примерно)
                normalized_time = (time.time() + gw_offset) % cycle_time
                
                # Зелёная волна активна в определённом окне времени
                # Простое правило: если offset < 15 секунд, то эта фаза должна быть активна
                if gw_offset < 15.0:
                    active_phase = gw_phase
                    phase_state["active_phase"] = active_phase
                    phase_state["phase_start_time"] = time.time()
                print(f"  🟢 [{inter_id}] GREEN WAVE: фаза {active_phase} (offset {gw_offset:.1f}с)")
        
        if active_phase is None:
            # Выбираем фазу с машинами (или первую, если машин нет)
            if phase_names:
                active_phase = phase_names[0]
                for pn in phase_names:
                    if phase_cars.get(pn, 0) > 0:
                        active_phase = pn
                        break
                phase_state["active_phase"] = active_phase
                phase_state["phase_start_time"] = time.time()
                # print(f"  🚦 [{inter_id}] Начальная фаза: {active_phase} {phase_cars}")
        else:
            # Режим одного направления: только одна фаза, просто держим её зелёной
            if len(phase_names) == 1:
                # Одна фаза - всегда зелёная, просто сбрасываем таймер если нужно
                if elapsed >= phase_state.get("max_duration", 30.0):
                    # print(f"  🔄 [{inter_id}] Перезапуск фазы {active_phase} (лимит {elapsed:.0f}с)")
                    phase_state["phase_start_time"] = time.time()
            else:
                # Две фазы - стандартная логика переключения
                opposite = phase_names[1] if phase_names[0] == active_phase else phase_names[0]
                this = phase_cars.get(active_phase, 0)
                other = phase_cars.get(opposite, 0)
                
                # print(f"  🔍 [{inter_id}] Текущая: {active_phase} (машин: {this}), Противоположная: {opposite} (машин: {other}), Прошло: {elapsed:.1f}с, Мин: {phase_state['min_duration']}с")
                
                # Условие 1: на этой фазе нет машин, на другой есть → переключаем
                if elapsed >= phase_state["min_duration"] and this == 0 and other > 0:
                    # print(f"  🔄 [{inter_id}] {active_phase}→{opposite} (на {opposite} есть {other} машин)")
                    active_phase = opposite
                    phase_state["active_phase"] = opposite
                    phase_state["phase_start_time"] = time.time()
                # Условие 2: обе фазы пусты дольше 8с → переключаем
                elif elapsed >= phase_state["min_duration"] and this == 0 and other == 0:
                    # print(f"  🔄 [{inter_id}] {active_phase}→{opposite} (обе пусты, прошло {elapsed:.0f}с)")
                    active_phase = opposite
                    phase_state["active_phase"] = opposite
                    phase_state["phase_start_time"] = time.time()
                # Условие 3: фаза горит дольше 30 секунд → принудительно переключаем
                elif elapsed >= phase_state.get("max_duration", 30.0):
                    # print(f"  🔄 [{inter_id}] {active_phase}→{opposite} (лимит {elapsed:.0f}с, машин: {this})")
                    active_phase = opposite
                    phase_state["active_phase"] = opposite
                    phase_state["phase_start_time"] = time.time()
        
        # ШАГ 4: Длительность зелёного
        total_cars = sum(phase_cars.values())
        if total_cars > 0 and active_phase:
            load = phase_cars.get(active_phase, 0) / max(total_cars, 1)
            green_duration = min(25.0, max(8.0, 10.0 + load * 15.0))
        else:
            green_duration = 10.0
        
        # ШАГ 5: Определяем подходы активной фазы
        active_approaches = []
        for pn, pd in phases_config.items():
            if pn == active_phase:
                active_approaches = pd.get("approaches", []) if isinstance(pd, dict) else pd
                break
        
        # ШАГ 6: Формируем ответы (БЕЗ вызова handle_telemetry!) и одно UI-сообщение
        responses = []
        batch_ui_payloads = []
        for cam in batch.cameras:
            approach = cam.camera_id.replace(f"{inter_id}_", "")
            is_active = approach in active_approaches
            cmd = "GREEN" if is_active else "RED"
            dur = green_duration if is_active else 0.0
            
            responses.append(SingleResponseDTO(
                camera_id=cam.camera_id,
                target_phase=cmd,
                green_duration=dur,
            ))
            
            # UI — обогащённое сообщение
            ui_lanes = []
            for lane in cam.lanes:
                lane_state = traffic_network.lane_pool.get(lane.lane_id, {})
                lane_phase = traffic_network.get_phase_for_approach(inter_id, approach)
                ui_lanes.append({
                    "lane_id": lane.lane_id,
                    "car_count": lane.car_count,
                    "avg_speed": lane.avg_speed,
                    "load_pct": int(lane_state.get("congestion_index", 0) * 100),
                    "light": cmd,
                    "phase_name": lane_phase or "UNKNOWN",
                    "max_capacity": lane_state.get("max_capacity", 10),
                })

            phase_elapsed = round(elapsed, 1) if active_phase else 0.0
            
            green_wave_info = None
            if green_wave_override:
                green_wave_info = {
                    "active": True,
                    "phase": green_wave_override.get("phase"),
                    "offset": green_wave_override.get("offset", 0.0),
                    "corridor": green_wave_override.get("corridor", []),
                }
            
            batch_ui_payloads.append({
                "type": "lane_update",
                "intersection_id": inter_id,
                "lane_id": cam.camera_id,
                "command": cmd,
                "current_phase": active_phase or "UNKNOWN",
                "green_duration": dur,
                "phase_elapsed": phase_elapsed,
                "lanes": ui_lanes,
                "green_wave": green_wave_info,
            })

        # Одно broadcast-сообщение для всех камер перекрёстка
        if self.ws_manager and batch_ui_payloads:
            await self.ws_manager.broadcast(json.dumps({
                "type": "batch_lane_update",
                "intersection_id": inter_id,
                "current_phase": active_phase or "UNKNOWN",
                "phase_elapsed": round(elapsed, 1) if active_phase else 0.0,
                "cameras": batch_ui_payloads,
            }))

        return responses
