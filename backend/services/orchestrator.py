# backend/services/orchestrator.py
import json
from typing import Dict, List
from backend.models.traffic import (
    IntersectionUpdateDTO, BatchTelemetryDTO, SingleResponseDTO, CameraTelemetryDTO
)
from backend.services.traffic_brain import AdaptiveTrafficBrain
from backend.services.graph_manager import traffic_network
from backend.services.cloud_orchestrator import CloudOrchestrator
from backend.services.green_wave import green_wave_coordinator
from backend.services.phase_manager import PhaseManager
import time


class TrafficOrchestrator:
    """
    Оркестратор.
    
    Dubai-style: каждый светофор имеет независимый контроллер (per-lane).
    Но при batch: одно решение на перекрёсток.
    
    Фазы управляются через PhaseManager (единый источник истины).
    """

    def __init__(self, ws_manager, cloud: CloudOrchestrator = None):
        self.traffic_brains: Dict[str, AdaptiveTrafficBrain] = {}
        self.ws_manager = ws_manager
        self.cloud = cloud
        self.phase_manager = PhaseManager()

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

        async with traffic_network.lane_pool_lock:
            target_command, green_duration = brain.process_lane_telemetry(update)

        # Получаем фазу из единого PhaseManager
        approach = camera_id.replace(f"{inter_id}_", "")
        lane_phase = traffic_network.get_phase_for_approach(inter_id, approach)
        
        # Синхронизация: в per-lane режиме фаза хранится в traffic_brain._get_intersection_phase_state,
        # переносим в PhaseManager для единого доступа
        from backend.services.traffic_brain import _get_intersection_phase_state
        brain_phase_state = _get_intersection_phase_state(inter_id)
        brain_active = brain_phase_state.get("active_phase")
        phase_state = self.phase_manager.get_or_create(inter_id)
        if brain_active is not None and brain_active != phase_state.active_phase:
            self.phase_manager.switch_phase(inter_id, brain_active)
            phase_state = self.phase_manager.get_or_create(inter_id)
        active_phase = phase_state.active_phase or "UNKNOWN"

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
        
        # Получаем команды зелёной волны
        green_wave_commands = green_wave_coordinator.calculate_green_wave()
        
        # ШАГ 1: Обновляем lane_pool от ВСЕХ камер
        async with traffic_network.lane_pool_lock:
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
        
        # ШАГ 3: Решение о фазе через PhaseManager (единый источник истины)
        phase_state = self.phase_manager.get_or_create(inter_id)
        active_phase = phase_state.active_phase
        elapsed = phase_state.elapsed
        
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
            if lane_phase in phase_cars:
                phase_cars[lane_phase] += data["car_count"]
        
        # Применяем зелёную волну если есть команда
        if green_wave_override:
            gw_phase = green_wave_override.get("phase")
            
            if gw_phase in phase_names:
                # Зелёная волна уже отфильтрована в calculate_green_wave() по времени
                # Если команда пришла — значит сейчас окно зелёной волны
                active_phase = gw_phase
                self.phase_manager.switch_phase(inter_id, active_phase)
                phase_state = self.phase_manager.get_or_create(inter_id)
                elapsed = phase_state.elapsed
        
        if active_phase is None:
            # Выбираем фазу с машинами (или первую, если машин нет)
            if phase_names:
                active_phase = phase_names[0]
                for pn in phase_names:
                    if phase_cars.get(pn, 0) > 0:
                        active_phase = pn
                        break
                self.phase_manager.switch_phase(inter_id, active_phase)
                phase_state = self.phase_manager.get_or_create(inter_id)
                elapsed = phase_state.elapsed
        else:
            # Режим одного направления: только одна фаза
            if len(phase_names) == 1:
                if elapsed >= phase_state.max_duration:
                    phase_state.phase_start_time = time.time()
            else:
                # Две фазы - стандартная логика переключения
                opposite = phase_names[1] if phase_names[0] == active_phase else phase_names[0]
                this = phase_cars.get(active_phase, 0)
                other = phase_cars.get(opposite, 0)
                
                # Условие 1: на этой фазе нет машин, на другой есть
                if elapsed >= phase_state.min_duration and this == 0 and other > 0:
                    active_phase = opposite
                    self.phase_manager.switch_phase(inter_id, opposite)
                    phase_state = self.phase_manager.get_or_create(inter_id)
                    elapsed = phase_state.elapsed
                # Условие 2: обе фазы пусты
                elif elapsed >= phase_state.min_duration and this == 0 and other == 0:
                    active_phase = opposite
                    self.phase_manager.switch_phase(inter_id, opposite)
                    phase_state = self.phase_manager.get_or_create(inter_id)
                    elapsed = phase_state.elapsed
                # Условие 3: фаза горит дольше max_duration
                elif elapsed >= phase_state.max_duration:
                    active_phase = opposite
                    self.phase_manager.switch_phase(inter_id, opposite)
                    phase_state = self.phase_manager.get_or_create(inter_id)
                    elapsed = phase_state.elapsed
        
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
        
        # ШАГ 6: Формируем ответы и одно UI-сообщение
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