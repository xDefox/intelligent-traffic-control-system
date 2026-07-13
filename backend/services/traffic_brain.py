# backend/services/traffic_brain.py
from typing import Dict, Optional
from backend.models.traffic import IntersectionUpdateDTO
from backend.services.graph_manager import traffic_network
import time


# Глобальный словарь для отслеживания активных фаз на каждом перекрёстке
# {intersection_id: {"active_phase": str, "phase_start_time": float, "min_duration": float}}
_intersection_phase_state: Dict[str, dict] = {}


def _get_intersection_phase_state(intersection_id: str) -> dict:
    """Получить или создать состояние фазы для перекрёстка"""
    if intersection_id not in _intersection_phase_state:
        _intersection_phase_state[intersection_id] = {
            "active_phase": None,
            "phase_start_time": 0,
            "min_duration": 5.0,
        }
    return _intersection_phase_state[intersection_id]


class AdaptiveTrafficBrain:
    """
    Локальный мозг перекрёстка (Fog-уровень).

    Решает:
    - Для режима per-intersection: какая фаза сейчас горит
    - Для режима per-lane (Dubai-style): команда для конкретного светофора
    """

    def __init__(self, intersection_id: str, is_per_lane: bool = False):
        self.intersection_id = intersection_id
        self.is_per_lane = is_per_lane
        
        if is_per_lane:
            # Dubai-style: независимый контроллер для каждого светофора
            self._current_command: str = "RED"
            self._last_car_count: int = 0
            self._green_start_time: float = 0
            self._min_green_duration: float = 5.0
        else:
            # Legacy: контроллер по фазам
            self._current_phase: str = "NS"
            self._phase_start_time: float = time.time()
            self._min_duration: float = 5.0

    def process_lane_telemetry(self, update: IntersectionUpdateDTO) -> tuple:
        """
        Dubai-style: обработать телеметрию с ОДНОЙ камеры.
        Вернуть (команда, длительность_зелёного) для конкретного светофора.
        
        Логика:
        - Координируем фазы на уровне перекрёстка (не даём конфликты)
        - Учитываем загруженность downstream дорог
        - Если есть машины → GREEN с длительностью на основе загруженности
        - Если машин нет → RED
        - Не переключаемся чаще чем раз в 5 секунд
        - Длительность зелёного: 5-30 секунд в зависимости от количества машин
        """
        if not self.is_per_lane:
            return self.process_telemetry(update), 0.0
        
        # 1. Обновляем состояние полосы
        for lane in update.lanes:
            traffic_network.update_lane_state(
                lane_id=lane.lane_id,
                car_count=lane.car_count,
                avg_speed=lane.avg_speed,
                max_capacity=lane.max_capacity,
            )
            self._last_car_count = lane.car_count
            self._last_max_capacity = lane.max_capacity

        # 2. Проверяем, можно ли включить эту полосу (координация на уровне перекрёстка)
        intersection_id = update.intersection_id
        lane_id = update.camera_id
        
        # Получаем фазу для этого подхода
        phase_name = traffic_network.get_phase_for_approach(intersection_id, lane_id.replace(f"{intersection_id}_", ""))
        
        # Проверяем, разрешена ли эта фаза сейчас
        if not self._is_phase_allowed(intersection_id, phase_name):
            # Фаза не разрешена - держим красный
            if self._current_command != "RED":
                self._current_command = "RED"
                self._green_start_time = 0
                print(f"  🔴 [{lane_id}] Фаза {phase_name} заблокирована конфликтной → RED")
            return self._current_command, 0.0
        
        # 3. Проверяем downstream загруженность
        downstream_congestion = self._get_downstream_congestion(intersection_id, lane_id)
        if downstream_congestion > 0.8:
            # Downstream перегружен >80% - не пускаем новых машин
            if self._current_command != "RED":
                self._current_command = "RED"
                self._green_start_time = 0
                print(f"  🔴 [{lane_id}] Downstream перегружен ({downstream_congestion:.0%}) → RED")
            return self._current_command, 0.0
        
        # 4. Решение для конкретного светофора
        elapsed = time.time() - self._green_start_time if self._green_start_time > 0 else 999
        
        # Если только что включили зелёный - ждём минимум времени
        if self._current_command == "GREEN" and elapsed < self._min_green_duration:
            return self._current_command, 0.0  # Длительность уже задана
        
        # Если есть машины → вычисляем длительность и включаем зелёный
        if self._last_car_count > 0:
            if self._current_command != "GREEN":
                self._current_command = "GREEN"
                self._green_start_time = time.time()
                
                # Вычисляем длительность на основе загруженности
                congestion_ratio = self._last_car_count / self._last_max_capacity if self._last_max_capacity > 0 else 0.5
                green_duration = 5.0 + (congestion_ratio * 25.0)  # 5-30 секунд
                green_duration = min(30.0, max(5.0, green_duration))  # Ограничиваем 5-30 сек
                
                self._current_duration = green_duration
                print(f"  🟢 [{lane_id}] Машины: {self._last_car_count}/{self._last_max_capacity} "
                      f"({congestion_ratio:.0%}) → GREEN на {green_duration:.1f}с")
                
                return self._current_command, green_duration
            else:
                # Уже зелёный, возвращаем текущую длительность
                return self._current_command, 0.0
        else:
            # Машин нет → красный
            if self._current_command != "RED":
                self._current_command = "RED"
                self._green_start_time = 0
                print(f"  🔴 [{lane_id}] Пусто → RED")
            
            return self._current_command, 0.0
    
    def _is_phase_allowed(self, intersection_id: str, phase_name: str) -> bool:
        """
        Проверить, разрешена ли фаза на перекрёстке.
        Не даём одновременно гореть противоречивым фазам (NS vs EW).
        """
        if not phase_name:
            return False
        
        # Получаем состояние фазы для этого перекрёстка
        phase_state = _get_intersection_phase_state(intersection_id)
        active_phase = phase_state["active_phase"]
        
        # Если фаза ещё не активна - разрешаем и устанавливаем
        if active_phase is None:
            phase_state["active_phase"] = phase_name
            phase_state["phase_start_time"] = time.time()
            phase_state["min_duration"] = 5.0
            return True
        
        # Если активна эта же фаза - разрешаем
        if active_phase == phase_name:
            return True
        
        # Если активна противоположная фаза - проверяем, можно ли переключиться
        phases = traffic_network.intersection_phases.get(intersection_id, {})
        if len(phases) < 2:
            return True
        
        phase_names = list(phases.keys())
        opposite_phase = phase_names[1] if phase_names[0] == phase_name else phase_names[0]
        
        # Если активна противоположная фаза - проверяем минимальное время
        if active_phase == opposite_phase:
            elapsed = time.time() - phase_state["phase_start_time"]
            if elapsed < phase_state["min_duration"]:
                # Минимальное время не прошло - блокируем переключение
                return False
            else:
                # Можно переключиться
                phase_state["active_phase"] = phase_name
                phase_state["phase_start_time"] = time.time()
                phase_state["min_duration"] = 5.0
                print(f"  🔄 [{intersection_id}] Переключение фазы: {active_phase} → {phase_name}")
                return True
        
        # Разрешаем переключение
        return True
    
    def _get_downstream_congestion(self, intersection_id: str, lane_id: str) -> float:
        """
        Получить загруженность downstream дорог.
        Если downstream перегружен >80%, не пускаем новых машин.
        """
        # Получаем downstream перекрёстки
        downstream_map = traffic_network.get_downstream_intersections(intersection_id)
        
        # Извлекаем подход из lane_id
        if "_approach_" in lane_id:
            approach = lane_id.split("_approach_")[-1]
            approach = f"approach_{approach}"
        else:
            return 0.0
        
        if approach not in downstream_map:
            return 0.0
        
        # Считаем среднюю загруженность downstream перекрёстков
        total_congestion = 0.0
        count = 0
        for down_inter in downstream_map[approach]:
            # Получаем все полосы downstream перекрёстка
            lanes = traffic_network.get_lanes_for_intersection(down_inter)
            for lane in lanes:
                total_congestion += lane.get("congestion_index", 0.0)
                count += 1
        
        return total_congestion / count if count > 0 else 0.0

    def process_telemetry(self, update: IntersectionUpdateDTO) -> str:
        """
        Обработать телеметрию с камер. Вернуть имя фазы, которая должна гореть.
        """
        # 1. Обновляем состояние полос
        for lane in update.lanes:
            traffic_network.update_lane_state(
                lane_id=lane.lane_id,
                car_count=lane.car_count,
                avg_speed=lane.avg_speed,
                max_capacity=lane.max_capacity,
            )

        # 2. Считаем загруженность фаз
        phases = list(traffic_network.intersection_phases.get(self.intersection_id, {}).keys())
        if len(phases) < 2:
            return self._current_phase

        current_phase = self._current_phase
        other_phase = phases[1] if phases[0] == current_phase else phases[0]

        current_load = traffic_network.get_congestion_for_phase(self.intersection_id, current_phase)
        other_load = traffic_network.get_congestion_for_phase(self.intersection_id, other_phase)

        elapsed = time.time() - self._phase_start_time

        # 3. Решение о переключении
        needs_switch = False

        # Если обе фазы пустые — не трогаем текущую фазу
        both_empty = (current_load == 0 and other_load == 0)

        if elapsed < self._min_duration:
            # Фаза ещё не отгорела — не трогаем
            pass
        elif both_empty:
            # Обе фазы пустые — оставляем как есть, не дёргаем
            pass
        elif current_load == 0 and other_load > 0:
            # На этой фазе никого — переключаем
            needs_switch = True
        elif other_load > current_load + 0.1:
            # Другая фаза загружена больше — переключаем
            needs_switch = True
        elif current_load < 0.3 and elapsed > 10.0:
            # Текущая фаза почти пустая и прошло 10+ секунд — отдаём время другой
            needs_switch = True

        if needs_switch:
            self._current_phase = other_phase
            self._phase_start_time = time.time()
            self._min_duration = 5.0  # Сбрасываем минимум

        # Лог только при смене фазы (убрал спам каждые 0.5с)
        # if needs_switch:
        #     print(f"🧠 [{self.intersection_id}] Фаза: {self._current_phase} "
        #           f"(прошло: {elapsed:.0f}с, {current_phase}: {current_load:.0%}, "
        #           f"{other_phase}: {other_load:.0%})")

        return self._current_phase

    def apply_cascade_command(self, command: dict):
        """Применить каскадную команду от CloudOrchestrator"""
        action = command.get("action", "")

        if action == "REDUCE_GREEN":
            reduce_by = command.get("reduce_green_by", 5)
            # Устанавливаем минимальное время горения фазы меньше
            self._min_duration = max(3.0, 8.0 - reduce_by)
            # Лог только при ошибках/каскадных командах
            print(f"  ⏱️  {self.intersection_id} -> каскад: зелёный урезан до "
                  f"{self._min_duration:.0f}с ({command.get('reason')})")

        elif action == "GREEN_WAVE":
            extend_by = command.get("extend_green_by", 3)
            self._min_duration = 15.0 + extend_by
            # Лог только при ошибках/каскадных командах
            print(f"  🟢  {self.intersection_id} -> зелёная волна: +{extend_by}с "
                  f"(мин. {self._min_duration:.0f}с)")

    def get_state(self) -> dict:
        """Текущее состояние мозга"""
        return {
            "current_phase": self._current_phase,
            "phase_elapsed": round(time.time() - self._phase_start_time, 1),
        }