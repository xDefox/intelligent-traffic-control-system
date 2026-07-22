# backend/services/orchestrator.py
import json
from typing import Dict, List, Optional, Tuple
from backend.models.traffic import (
    BatchTelemetryDTO, SingleResponseDTO, CameraTelemetryDTO
)
from backend.services.graph_manager import traffic_network
from backend.services.cloud_orchestrator import CloudOrchestrator
from backend.services.green_wave import green_wave_coordinator
from backend.services.phase_manager import PhaseManager
from backend.services.statistics import traffic_stats, CongestionSnapshot
from backend.core.logger import debug, info, warning, error
from backend.core.lane_utils import normalize_lane_id, extract_approach_from_camera_id
from backend.core.emergency import EmergencyDetector
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
        debug("Orchestrator", f"Batch from {inter_id}: {len(batch.cameras)} cameras")
        for cam in batch.cameras:
            debug("Orchestrator", f"  Camera: {cam.camera_id}")
        
        # ШАГ 0.5: Проверяем emergency (спецтранспорт)
        emergency_detected, emergency_approach, emergency_phase = self._detect_emergency(batch)
        
        # Если emergency — сообщаем Cloud для каскадирования
        if emergency_detected and self.cloud:
            self.cloud.report_emergency(inter_id, emergency_approach, emergency_phase)
        
        # Получаем команды зелёной волны
        green_wave_commands = green_wave_coordinator.calculate_green_wave()
        
        # ШАГ 0.5: Регистрация камер (Camera-First Design)
        self._register_cameras(batch.cameras, inter_id)
        
        # ШАГ 1: Обновляем lane_pool от ВСЕХ камер
        async with traffic_network.lane_pool_lock:
            self._update_lane_pool(batch.cameras)
        
        # ШАГ 2: Конфиг фаз (автоматически сгенерирован из направлений камер)
        phases_config = traffic_network.intersection_phases.get(inter_id, {})
        phase_names = list(phases_config.keys())
        
        debug("Orchestrator", f"  Intersection {inter_id}: approaches={traffic_network.intersection_approaches.get(inter_id)}, phases={phase_names}")
        debug("Orchestrator", f"  Graph nodes: {len(traffic_network.graph.nodes)}, edges: {len(traffic_network.graph.edges)}")
        
        if not phase_names:
            return [SingleResponseDTO(camera_id=cam.camera_id, target_phase="RED", green_duration=0.0) for cam in batch.cameras]
        
        # ШАГ 3: Решение о фазе через PhaseManager (единый источник истины)
        active_phase, elapsed, emergency_override, green_wave_override = self._decide_phase(
            inter_id, phase_names, green_wave_commands, emergency_approach
        )
        
        # ШАГ 4: Длительность зелёного
        green_duration = self._calculate_green_duration(active_phase, phase_names)
        
        # ШАГ 5: Определяем направления активной фазы
        active_approaches = self._get_active_approaches(phases_config, active_phase)
        
        # ШАГ 5.5-5.6: Запись статистики
        self._record_statistics(batch, inter_id, active_phase)
        
        # ШАГ 6: Формируем ответы и одно UI-сообщение
        responses, batch_ui_payloads = self._build_responses(
            batch, inter_id, active_phase, active_approaches, green_duration,
            elapsed, green_wave_override
        )
        
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

    # ===================== ВЫДЕЛЕННЫЕ МЕТОДЫ =====================

    def _detect_emergency(self, batch: BatchTelemetryDTO) -> Tuple[bool, Optional[str], Optional[str]]:
        """ШАГ 0.5: Проверить batch на наличие спецтранспорта."""
        inter_id = batch.intersection_id
        emergency_detected, emergency_approach, emergency_phase = EmergencyDetector.detect(batch)
        if emergency_detected:
            info("Orchestrator", f"🚨 Emergency: {inter_id}/{emergency_approach} → phase {emergency_phase}")
        return emergency_detected, emergency_approach, emergency_phase

    def _register_cameras(self, cameras: List[CameraTelemetryDTO], inter_id: str):
        """ШАГ 0.5: Регистрация камер (Camera-First Design)."""
        for cam in cameras:
            if cam.direction and cam.world_position:
                traffic_network.register_camera({
                    "camera_id": cam.camera_id,
                    "intersection_id": inter_id,
                    "direction": cam.direction,
                    "world_position": cam.world_position,
                    "world_rotation": cam.world_rotation,
                })

    def _update_lane_pool(self, cameras: List[CameraTelemetryDTO]):
        """ШАГ 1: Обновить lane_pool от ВСЕХ камер."""
        for cam in cameras:
            for lane in cam.lanes:
                lane_id = normalize_lane_id(lane.lane_id)
                debug("Orchestrator", f"  Lane: {lane_id}, cars={lane.car_count}, max_cap={lane.max_capacity}")
                traffic_network.update_lane_state(
                    lane_id=lane_id,
                    car_count=lane.car_count,
                    avg_speed=lane.avg_speed,
                    max_capacity=lane.max_capacity,
                )

    def _decide_phase(self, inter_id: str, phase_names: List[str],
                      green_wave_commands: List[dict],
                      emergency_approach: Optional[str]) -> Tuple[str, float, Optional[dict], Optional[dict]]:
        """ШАГ 3: Принять решение о фазе через PhaseManager."""
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
                info("Orchestrator", f"🚨 Emergency override: phase {emergency_phase} on {inter_id}")
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
        
        return active_phase, elapsed, emergency_override, green_wave_override

    def _calculate_green_duration(self, active_phase: str, phase_names: List[str]) -> float:
        """ШАГ 4: Длительность зелёного на основе загруженности."""
        # Считаем машины на каждой фазе
        phase_cars = {pn: 0 for pn in phase_names}
        for lane_id, data in traffic_network.lane_pool.items():
            lane_phase = traffic_network.get_phase_for_approach(data["intersection_id"], data["approach"])
            if lane_phase in phase_cars:
                phase_cars[lane_phase] += data["car_count"]
        
        total_cars = sum(phase_cars.values())
        if total_cars > 0 and active_phase:
            load = phase_cars.get(active_phase, 0) / max(total_cars, 1)
            green_duration = min(25.0, max(8.0, 10.0 + load * 15.0))
        else:
            green_duration = 10.0
        
        return green_duration

    def _get_active_approaches(self, phases_config: Dict[str, dict], active_phase: str) -> List[str]:
        """ШАГ 5: Определить направления активной фазы."""
        for pn, pd in phases_config.items():
            if pn == active_phase:
                return pd.get("approaches", []) if isinstance(pd, dict) else pd
        return []

    def _record_statistics(self, batch: BatchTelemetryDTO, inter_id: str, active_phase: str):
        """ШАГ 5.5-5.6: Записать статистику переключения фазы и congestion snapshot."""
        # Записываем статистику переключения фазы
        prev_phase = self.phase_manager.get_state(inter_id)
        prev_active_phase = prev_phase.active_phase if prev_phase else None
        
        # Если фаза изменилась - записываем статистику
        if prev_active_phase != active_phase:
            traffic_stats.record_phase_switch(inter_id)
        
        # Записываем congestion snapshot для перекрёстка
        lane_congestions = {}
        total_cars = 0
        active_lanes_count = 0
        
        for cam in batch.cameras:
            for lane in cam.lanes:
                norm_lane_id = normalize_lane_id(lane.lane_id)
                lane_state = traffic_network.lane_pool.get(norm_lane_id, {})
                congestion = lane_state.get("congestion_index", 0.0)
                lane_congestions[norm_lane_id] = congestion
                total_cars += lane.car_count
                active_lanes_count += 1
        
        traffic_stats.record_congestion_snapshot(
            intersection_id=inter_id,
            lane_congestions=lane_congestions,
            total_cars=total_cars,
            active_lanes=active_lanes_count,
            phase=active_phase or "UNKNOWN",
        )

    def apply_emergency_override(self, responses: List[SingleResponseDTO],
                                 inter_id: str, emergency_phase: Optional[str]) -> List[SingleResponseDTO]:
        """
        Применить emergency_override к ответам: пометить камеры,
        принадлежащие emergency фазе, флагом emergency_override=True.
        """
        if not emergency_phase:
            return responses

        phases_config = traffic_network.intersection_phases.get(inter_id, {})
        emergency_approaches = []
        for pn, pd in phases_config.items():
            if pn == emergency_phase:
                emergency_approaches = pd.get("approaches", []) if isinstance(pd, dict) else pd
                break

        for resp in responses:
            direction = extract_approach_from_camera_id(resp.camera_id)
            if direction in emergency_approaches:
                resp.emergency_override = True

        return responses

    def _build_responses(self, batch: BatchTelemetryDTO, inter_id: str,
                         active_phase: str, active_approaches: List[str],
                         green_duration: float, elapsed: float,
                         green_wave_override: Optional[dict]) -> Tuple[List[SingleResponseDTO], List[dict]]:
        """ШАГ 6: Сформировать ответы и UI-сообщения для всех камер."""
        responses = []
        batch_ui_payloads = []
        seen_lanes = set()  # Для дедупликации lane_id
        
        for cam in batch.cameras:
            # direction = approach (извлекаем из camera_id)
            direction = extract_approach_from_camera_id(cam.camera_id)
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
                norm_lane_id = normalize_lane_id(lane.lane_id)
                
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
        
        return responses, batch_ui_payloads

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