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
            self.traffic_brains[camera_id] = AdaptiveTrafficBrain(camera_id, self.phase_manager, is_per_lane=True)

        brain = self.traffic_brains[camera_id]

        if self.cloud:
            for cmd in self.cloud.get_cascade_commands():
                if cmd.get("target_intersection") == inter_id:
                    brain.apply_cascade_command(cmd)

        async with traffic_network.lane_pool_lock:
            target_command, green_duration = brain.process_lane_telemetry(update)

        # Получаем фазу из единого PhaseManager (единственный источник истины)
        approach = camera_id.replace(f"{inter_id}_", "")
        lane_phase = traffic_network.get_phase_for_approach(inter_id, approach)
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
        
        КАМЕРО-ЦЕНТРИЧНАЯ АРХИТЕКТУРА:
        1. Регистрируем камеры (с direction, position, rotation)
        2. Автоматически строим граф
        3. Обновляем lane_pool от ВСЕХ камер
        4. Принимаем ОДНО решение о фазе
        5. Сразу возвращаем ответы
        """
        inter_id = batch.intersection_id
        
        # DEBUG: логируем входящий batch
        print(f"[DEBUG] Batch from {inter_id}: {len(batch.cameras)} cameras")
        for cam in batch.cameras:
            print(f"[DEBUG]   Camera: {cam.camera_id}")
        
        # ШАГ 0.5: Проверяем emergency (спецтранспорт)
        emergency_detected = False
        emergency_approach = None
        emergency_phase = None
        
        for cam in batch.cameras:
            if cam.emergency_vehicle_detected and cam.emergency_approach:
                emergency_detected = True
                emergency_approach = cam.emergency_approach
                # Определяем фазу для emergency подхода
                emergency_phase = traffic_network.get_phase_for_approach(inter_id, emergency_approach)
                print(f"[EMERGENCY] 🚨 Спецтранспорт на {inter_id}/{emergency_approach} → фаза {emergency_phase}")
                break  # Первый обнаруженный = приоритет
        
        # Если emergency — сообщаем Cloud для каскадирования
        if emergency_detected and self.cloud:
            self.cloud.report_emergency(inter_id, emergency_approach, emergency_phase)
        
        # Получаем команды зелёной волны
        green_wave_commands = green_wave_coordinator.calculate_green_wave()
        
        # ШАГ 0.5: Регистрация камер (Camera-First Design)
        for cam in batch.cameras:
            if cam.direction and cam.world_position:
                traffic_network.register_camera({
                    "camera_id": cam.camera_id,
                    "intersection_id": inter_id,
                    "direction": cam.direction,
                    "world_position": cam.world_position,
                    "world_rotation": cam.world_rotation,
                })
        
        # ШАГ 1: Обновляем lane_pool от ВСЕХ камер
        async with traffic_network.lane_pool_lock:
            for cam in batch.cameras:
                for lane in cam.lanes:
                    # Нормализуем lane_id: добавляем префикс "lane_" если его нет
                    lane_id = lane.lane_id if lane.lane_id.startswith("lane_") else f"lane_{lane.lane_id}"
                    print(f"[DEBUG]   Lane: {lane_id}, cars={lane.car_count}, max_cap={lane.max_capacity}")
                    traffic_network.update_lane_state(
                        lane_id=lane_id,
                        car_count=lane.car_count,
                        avg_speed=lane.avg_speed,
                        max_capacity=lane.max_capacity,
                    )
        
        # ШАГ 2: Конфиг фаз (автоматически сгенерирован из направлений камер)
        phases_config = traffic_network.intersection_phases.get(inter_id, {})
        phase_names = list(phases_config.keys())
        
        print(f"[DEBUG]   Intersection {inter_id}: approaches={traffic_network.intersection_approaches.get(inter_id)}, phases={phase_names}")
        print(f"[DEBUG]   Graph nodes: {len(traffic_network.graph.nodes)}, edges: {len(traffic_network.graph.edges)}")
        
        if not phase_names:
            return [SingleResponseDTO(camera_id=cam.camera_id, target_phase="RED", green_duration=0.0) for cam in batch.cameras]
        
        # ШАГ 3: Решение о фазе через PhaseManager (единый источник истины)
        phase_state = self.phase_manager.get_or_create(inter_id)
        active_phase = phase_state.active_phase
        elapsed = phase_state.elapsed
        
        # Проверяем EMERGENCY команды (приоритет над всем)
        emergency_override = None
        for cmd in green_wave_commands:
            if cmd.get("target_intersection") == inter_id and cmd.get("action") == "EMERGENCY_GREEN":
                emergency_override = cmd
                break
        
        # Проверяем, есть ли команда зелёной волны для этого перекрёстка
        green_wave_override = None
        if not emergency_override:  # Emergency имеет приоритет
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
        
        # ===== EMERGENCY OVERRIDE =====
        if emergency_override:
            emergency_phase = emergency_override.get("phase")
            if emergency_phase and emergency_phase in phase_names:
                print(f"[EMERGENCY] 🚨 Принудительная фаза {emergency_phase} на {inter_id}")
                active_phase = emergency_phase
                self.phase_manager.switch_phase(inter_id, active_phase)
                phase_state = self.phase_manager.get_or_create(inter_id)
                elapsed = phase_state.elapsed
        # Применяем зелёную волну если есть команда (и нет emergency)
        elif green_wave_override:
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
            active_phase = self._pick_phase(phase_names, phase_cars)
            self.phase_manager.switch_phase(inter_id, active_phase)
            phase_state = self.phase_manager.get_or_create(inter_id)
            elapsed = phase_state.elapsed
        elif not emergency_override:  # Emergency не позволяет обычное переключение
            # ДИНАМИЧЕСКОЕ переключение для ЛЮБОГО числа фаз (1..N дорог)
            this = phase_cars.get(active_phase, 0)

            if elapsed >= phase_state.max_duration:
                # Фаза горит слишком долго — переключаемся на лучшую другую
                active_phase = self._pick_phase(phase_names, phase_cars, exclude=active_phase)
                self.phase_manager.switch_phase(inter_id, active_phase)
                phase_state = self.phase_manager.get_or_create(inter_id)
                elapsed = phase_state.elapsed
            elif elapsed >= phase_state.min_duration and this == 0:
                # На активной фазе нет машин — переключаемся, если где-то есть
                candidate = self._pick_phase(phase_names, phase_cars, exclude=active_phase)
                if candidate != active_phase and phase_cars.get(candidate, 0) > 0:
                    active_phase = candidate
                    self.phase_manager.switch_phase(inter_id, candidate)
                    phase_state = self.phase_manager.get_or_create(inter_id)
                    elapsed = phase_state.elapsed
        
        # ШАГ 4: Длительность зелёного
        total_cars = sum(phase_cars.values())
        if total_cars > 0 and active_phase:
            load = phase_cars.get(active_phase, 0) / max(total_cars, 1)
            green_duration = min(25.0, max(8.0, 10.0 + load * 15.0))
        else:
            green_duration = 10.0
        
        # ШАГ 5: Определяем направления активной фазы
        active_approaches = []
        for pn, pd in phases_config.items():
            if pn == active_phase:
                active_approaches = pd.get("approaches", []) if isinstance(pd, dict) else pd
                break
        
        # ШАГ 6: Формируем ответы и одно UI-сообщение
        responses = []
        batch_ui_payloads = []
        seen_lanes = set()  # Для дедупликации lane_id
        
        for cam in batch.cameras:
            # direction = approach (извлекаем из camera_id)
            direction = cam.camera_id.split("_approach_")[-1] if "_approach_" in cam.camera_id else cam.camera_id
            direction = f"approach_{direction}" if not direction.startswith("approach_") else direction
            is_active = direction in active_approaches
            cmd = "GREEN" if is_active else "RED"
            dur = green_duration if is_active else 0.0
            
            responses.append(SingleResponseDTO(
                camera_id=cam.camera_id,
                target_phase=cmd,
                green_duration=dur,
            ))
            
            ui_lanes = []
            for lane in cam.lanes:
                # Нормализуем lane_id ТАК ЖЕ, как при register_lane_data (префикс "lane_")
                norm_lane_id = lane.lane_id if lane.lane_id.startswith("lane_") else f"lane_{lane.lane_id}"
                
                # ДЕДУПЛИКАЦИЯ: пропускаем если уже добавляли этот lane_id
                if norm_lane_id in seen_lanes:
                    continue
                seen_lanes.add(norm_lane_id)
                
                lane_state = traffic_network.lane_pool.get(norm_lane_id, {})
                lane_phase = traffic_network.get_phase_for_approach(inter_id, direction)
                
                # ИСПРАВЛЕНИЕ: берем max_capacity из lane_state (от камеры), а не из lane.max_capacity
                max_cap = lane_state.get("max_capacity", 1) or 1
                
                ui_lanes.append({
                    "lane_id": lane.lane_id,
                    "car_count": lane.car_count,
                    "avg_speed": lane.avg_speed,
                    "load_pct": int(lane_state.get("congestion_index", 0) * 100),
                    "light": cmd,
                    "phase_name": lane_phase or "UNKNOWN",
                    "max_capacity": max_cap,
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

        # Возвращаем responses + emergency информацию
        emergency_phase_for_response = emergency_phase
        if emergency_override:
            emergency_phase_for_response = emergency_override.get("phase")
        return responses, emergency_detected or (emergency_override is not None), emergency_phase_for_response

    @staticmethod
    def _pick_phase(phase_names: List[str], phase_cars: Dict[str, int],
                    exclude: str = None) -> str:
        """
        Выбрать фазу для переключения.

        Работает для ЛЮБОГО числа фаз (1..N), не только для 2.
        Берёт фазу с наибольшим числом машин; если машин нет —
        первую подходящую (кроме exclude).
        """
        candidates = [pn for pn in phase_names if pn != exclude]
        if not candidates:
            return exclude if exclude in phase_names else (phase_names[0] if phase_names else None)

        best = max(candidates, key=lambda pn: phase_cars.get(pn, 0))
        if phase_cars.get(best, 0) > 0:
            return best
        return candidates[0]
