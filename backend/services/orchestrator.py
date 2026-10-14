# backend/services/orchestrator.py
import json
import traceback
from typing import Dict, List, Optional, Tuple
from backend.models.traffic import (
    BatchTelemetryDTO, SingleResponseDTO, CameraTelemetryDTO
)
from backend.services.graph_manager import traffic_network
from backend.services.cloud_orchestrator import CloudOrchestrator
from backend.services.green_wave import green_wave_coordinator
from backend.services.phase_manager import PhaseManager
from backend.services.statistics import traffic_stats
from backend.core.logger import debug, info, error
from backend.core.lane_utils import normalize_lane_id, extract_approach_from_camera_id
from backend.core.emergency import EmergencyDetector


# ---------------------------------------------------------------------------
# Параметры длительности зелёного и каскадного metering
# ---------------------------------------------------------------------------
GREEN_DURATION_MIN = 8.0       # нижняя граница зелёного, сек
GREEN_DURATION_MAX = 25.0      # верхняя граница зелёного, сек
GREEN_DURATION_BASE = 10.0     # базовый зелёный при нулевой нагрузке, сек
GREEN_DURATION_LOAD_SPAN = 15.0  # добавка к базовому при полной загрузке фазы
CASCADE_MAX_EXTEND = 15.0      # предел продления фазы каскадом сверх её max


class TrafficOrchestrator:
    """
    Оркестратор.
    
    Dubai-style: каждый светофор имеет независимый контроллер (per-lane).
    Но при batch: одно решение на перекрёсток.
    
    Фазы управляются через PhaseManager (единый источник истины).
    """

    def __init__(self, ws_manager, cloud: CloudOrchestrator = None):
        self.ws_manager = ws_manager
        self.cloud = cloud
        self.phase_manager = PhaseManager()

    async def handle_batch_telemetry(
        self, batch: BatchTelemetryDTO
    ) -> Tuple[List[SingleResponseDTO], bool, Optional[str]]:
        """Обработать batch-телеметрию с защитой от любых сбоев.

        Ни одна ошибка обработки не должна ронять эндпоинт (500): при сбое
        отдаём fail-safe ответ «всем RED» и пишем ошибку в лог. Плюс страховка
        от пустого batch (без камер) — иначе дальнейший код может упасть.
        """
        if not batch.cameras:
            return [], False, None
        try:
            return await self._handle_batch_telemetry_impl(batch)
        except Exception as exc:
            error("Orchestrator", f"batch {batch.intersection_id} failed: {exc}")
            traceback.print_exc()
            return (
                [SingleResponseDTO(camera_id=cam.camera_id, target_phase="RED", green_duration=0.0)
                 for cam in batch.cameras],
                False,
                None,
            )

    async def _handle_batch_telemetry_impl(
        self, batch: BatchTelemetryDTO
    ) -> Tuple[List[SingleResponseDTO], bool, Optional[str]]:
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
        
        # Команды каскада (metering магистрали), посчитанные cloud-тикером:
        # забитый перекрёсток заставляет upstream урезать зелёный на вливе.
        cascade_commands = self._get_cascade_commands()
        
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
            # Нет фаз (например, некорректные lane_id) — всё RED, но возвращаем
            # ПОЛНЫЙ 3-кортеж, иначе main.py упадёт на распаковке.
            return (
                [SingleResponseDTO(camera_id=cam.camera_id, target_phase="RED", green_duration=0.0)
                 for cam in batch.cameras],
                emergency_detected,
                emergency_phase,
            )
        
        # ШАГ 3: Решение о фазе через PhaseManager (единый источник истины)
        active_phase, elapsed, emergency_override, green_wave_override = self._decide_phase(
            inter_id, phase_names, green_wave_commands, emergency_approach, cascade_commands
        )
        
        # ШАГ 4: Длительность зелёного (с поправкой каскада)
        green_duration = self._calculate_green_duration(inter_id, active_phase, phase_names)
        reduce_by, extend_by = self._cascade_adjustment(inter_id, active_phase, cascade_commands)
        green_duration = max(
            GREEN_DURATION_MIN,
            min(GREEN_DURATION_MAX, green_duration + extend_by - reduce_by),
        )
        
        # ШАГ 5: Определяем направления активной фазы
        active_approaches = self._get_active_approaches(phases_config, active_phase)
        
        # Публикуем текущую фазу в lane_pool (для статистики переключений и UI)
        traffic_network.set_intersection_phase(inter_id, active_phase or "UNKNOWN")

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
        """ШАГ 0.5: Регистрация камер (Camera-First Design).

        Топология перестраивается ОДИН раз на батч (см. register_cameras):
        это O(C²) вместо O(C³) при построении после каждой камеры.
        """
        new_cameras = []
        for cam in cameras:
            if cam.direction and cam.world_position:
                new_cameras.append({
                    "camera_id": cam.camera_id,
                    "intersection_id": inter_id,
                    "direction": cam.direction,
                    "world_position": cam.world_position,
                    "world_rotation": cam.world_rotation,
                })
        if new_cameras:
            traffic_network.register_cameras(new_cameras)

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
                      emergency_approach: Optional[str],
                      cascade_commands: Optional[List[dict]] = None) -> Tuple[str, float, Optional[dict], Optional[dict]]:
        """ШАГ 3: Принять решение о фазе через PhaseManager."""
        phase_state = self.phase_manager.get_or_create(inter_id)
        self._apply_phase_bounds(inter_id, phase_state)
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
        phase_cars = self._count_phase_cars(inter_id, phase_names)
        
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

            # Каскадный metering: если забитый сосед просит притормозить влив
            # (REDUCE_GREEN) — укорачиваем эту фазу; если зелёная волна
            # (GREEN_WAVE) — продлеваем. Границы не даём пробить.
            reduce_by, extend_by = self._cascade_adjustment(inter_id, active_phase, cascade_commands)
            eff_max = phase_state.max_duration + extend_by - reduce_by
            eff_max = max(
                phase_state.min_duration,
                min(eff_max, phase_state.max_duration + CASCADE_MAX_EXTEND),
            )

            if elapsed >= eff_max:
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

    def _calculate_green_duration(self, inter_id: str, active_phase: str,
                                  phase_names: List[str]) -> float:
        """ШАГ 4: Длительность зелёного на основе загруженности.

        ВАЖНО: машины считаются ТОЛЬКО по этому перекрёстку. Раньше цикл шёл по
        всему lane_pool без фильтра, и фазы соседних перекрёстков (с теми же
        именами "EW"/"NS") подмешивались в расчёт — длительность зелёного
        зависела от чужого трафика.
        """
        phase_cars = self._count_phase_cars(inter_id, phase_names)

        total_cars = sum(phase_cars.values())
        if total_cars > 0 and active_phase:
            load = phase_cars.get(active_phase, 0) / max(total_cars, 1)
            green_duration = min(
                GREEN_DURATION_MAX,
                max(GREEN_DURATION_MIN,
                    GREEN_DURATION_BASE + load * GREEN_DURATION_LOAD_SPAN),
            )
        else:
            green_duration = GREEN_DURATION_BASE

        return green_duration

    def _get_cascade_commands(self) -> List[dict]:
        """Команды каскадного metering от cloud-тикера (пусто, если облака нет)."""
        if not self.cloud:
            return []
        return self.cloud.get_cascade_commands()

    def _cascade_adjustment(self, inter_id: str, active_phase: Optional[str],
                            cascade_commands: Optional[List[dict]]) -> Tuple[float, float]:
        """Суммарная урезка/продление зелёного активной фазы от каскада.

        Возвращает (reduce_by, extend_by) в секундах. Команда учитывается,
        только если её подход принадлежит активной фазе — иначе мы бы укоротили
        (или продлили) не тот зелёный.
        """
        reduce_by = 0.0
        extend_by = 0.0
        if not active_phase or not cascade_commands:
            return reduce_by, extend_by

        for cmd in cascade_commands:
            if cmd.get("target_intersection") != inter_id:
                continue
            approach = cmd.get("approach")
            if approach and traffic_network.get_phase_for_approach(inter_id, approach) != active_phase:
                continue
            action = cmd.get("action")
            if action == "REDUCE_GREEN":
                reduce_by += float(cmd.get("reduce_green_by", 0.0))
            elif action == "GREEN_WAVE":
                extend_by += float(cmd.get("extend_green_by", 0.0))
        return reduce_by, extend_by

    def _count_phase_cars(self, inter_id: str, phase_names: List[str]) -> Dict[str, int]:
        """Сумма машин по фазам ТОЛЬКО указанного перекрёстка.

        Единый источник подсчёта (используется и при выборе фазы, и при расчёте
        длительности зелёного) — чтобы логика не расходилась между вызовами.
        """
        phase_cars = {pn: 0 for pn in phase_names}
        for data in traffic_network.lane_pool.values():
            if data["intersection_id"] != inter_id:
                continue
            lane_phase = traffic_network.get_phase_for_approach(inter_id, data["approach"])
            if lane_phase in phase_cars:
                phase_cars[lane_phase] += data["car_count"]
        return phase_cars

    def _apply_phase_bounds(self, inter_id: str, phase_state):
        """Применить min/max длительности фазы из конфига перекрёстка.

        Раньше PhaseState всегда использовал дефолты (8/30) и игнорировал
        min_duration/max_duration из generate_phases(). Теперь конфиг — источник
        истины (берим значения первой фазы; в generate_phases они одинаковы).
        """
        phases_config = traffic_network.intersection_phases.get(inter_id, {})
        for pd in phases_config.values():
            if isinstance(pd, dict):
                if "min_duration" in pd:
                    phase_state.min_duration = max(3.0, pd["min_duration"])
                if "max_duration" in pd:
                    phase_state.max_duration = max(5.0, pd["max_duration"])
                break

    def _get_active_approaches(self, phases_config: Dict[str, dict], active_phase: str) -> List[str]:
        """ШАГ 5: Определить направления активной фазы."""
        for pn, pd in phases_config.items():
            if pn == active_phase:
                return pd.get("approaches", []) if isinstance(pd, dict) else pd
        return []

    def _record_statistics(self, batch: BatchTelemetryDTO, inter_id: str, active_phase: str):
        """ШАГ 5.5: Записать congestion snapshot для перекрёстка.

        Переключения фаз учитываются в cloud_orchestrator._record_statistics()
        (по изменению фазы между тиками); здесь фаза уже переключена заранее.
        """
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