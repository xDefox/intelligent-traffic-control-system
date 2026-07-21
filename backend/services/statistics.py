"""
Statistics Service - аналитическая система трафика.

Собирает:
- Congestion snapshots по времени (time series)
- Тренды загрузки (линейная регрессия)
- Рейтинг загруженности перекрёстков
- Emergency-события
- Эффективность зелёной волны
- Аномалии и превышения порогов
"""

import time
import math
from typing import Dict, List, Optional, Tuple
from collections import deque
from dataclasses import dataclass, field
from backend.core.logger import debug, info, warning


# Константы
MAX_HISTORY_SIZE = 3600  # 1 час при 1 snapshot/сек
TREND_WINDOW = 60  # окно для тренда (60 секунд)
CONGESTION_HIGH_THRESHOLD = 0.7
CONGESTION_CRITICAL_THRESHOLD = 0.9
ANOMALY_Z_SCORE = 2.5  # порог z-score для аномалий


@dataclass
class CongestionSnapshot:
    """Снимок загрузки перекрёстка в момент времени"""
    timestamp: float
    avg_congestion: float  # 0..1
    max_congestion: float  # максимальная загруженность среди полос
    total_cars: int
    active_lanes: int
    phase: str = "UNKNOWN"


@dataclass
class EmergencyEvent:
    """Запись о событии спецтранспорта"""
    timestamp: float
    intersection_id: str
    approach: str
    phase: str
    cascade_intersections: List[str] = field(default_factory=list)
    duration: float = 0.0  # сколько секунд длился emergency


@dataclass
class GreenWaveEvent:
    """Запись о зелёной волне"""
    timestamp: float
    corridor: List[str]  # цепочка перекрёстков
    phase: str
    duration: float = 0.0


@dataclass
class AnomalyRecord:
    """Запись об аномалии"""
    timestamp: float
    intersection_id: str
    metric: str  # "congestion", "wait_time", "phase_switches"
    value: float
    threshold: float
    severity: str  # "warning", "critical"
    message: str


@dataclass
class IntersectionAnalytics:
    """Аналитика одного перекрёстка"""
    intersection_id: str
    congestion_history: deque = field(default_factory=lambda: deque(maxlen=MAX_HISTORY_SIZE))
    total_cars_served: int = 0
    total_phase_switches: int = 0
    green_wave_hits: int = 0  # сколько раз попал в зелёную волну
    
    # Агрегированные метрики
    peak_congestion: float = 0.0
    peak_congestion_time: float = 0.0
    avg_congestion: float = 0.0
    avg_congestion_calculated_at: float = 0.0
    
    # Аномалии
    anomalies: deque = field(default_factory=lambda: deque(maxlen=100))
    
    def add_snapshot(self, snapshot: CongestionSnapshot):
        self.congestion_history.append(snapshot)
        
        # Peak congestion
        if snapshot.avg_congestion > self.peak_congestion:
            self.peak_congestion = snapshot.avg_congestion
            self.peak_congestion_time = snapshot.timestamp
    
    def get_recent_congestion(self, seconds: int = 60) -> List[CongestionSnapshot]:
        """Получить свежие снимки за последние N секунд"""
        cutoff = time.time() - seconds
        return [s for s in self.congestion_history if s.timestamp >= cutoff]
    
    def get_trend(self, seconds: int = TREND_WINDOW) -> float:
        """
        Тренд загрузки (линейная регрессия).
        Возвращает: наклон (slope) — congestion в секунду.
        > 0.001: растёт
        < -0.001: падает
        иначе: стабильно
        """
        recent = self.get_recent_congestion(seconds)
        if len(recent) < 10:
            return 0.0
        
        n = len(recent)
        x_avg = n / 2.0
        y_avg = sum(s.avg_congestion for s in recent) / n
        
        num = 0.0
        den = 0.0
        for i, s in enumerate(recent):
            xi = i - x_avg
            yi = s.avg_congestion - y_avg
            num += xi * yi
            den += xi * xi
        
        if den == 0:
            return 0.0
        return num / den
    
    def get_trend_direction(self) -> str:
        """Направление тренда"""
        slope = self.get_trend()
        if slope > 0.001:
            return "↗ rising"
        elif slope < -0.001:
            return "↘ falling"
        return "→ stable"
    
    def detect_anomalies(self) -> List[AnomalyRecord]:
        """Обнаружить аномалии на основе последних данных"""
        found = []
        recent = self.get_recent_congestion(60)
        
        if len(recent) < 10:
            return found
        
        # Z-score анализ congestion
        values = [s.avg_congestion for s in recent]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = math.sqrt(variance) if variance > 0 else 0.0001
        
        last_val = values[-1]
        z = (last_val - mean) / std
        
        if z > ANOMALY_Z_SCORE:
            severity = "critical" if last_val > CONGESTION_CRITICAL_THRESHOLD else "warning"
            found.append(AnomalyRecord(
                timestamp=recent[-1].timestamp,
                intersection_id=self.intersection_id,
                metric="congestion",
                value=last_val,
                threshold=mean + ANOMALY_Z_SCORE * std,
                severity=severity,
                message=f"Загрузка {last_val:.0%} (z={z:.1f}, норма ~{mean:.0%})",
            ))
        
        return found


class TrafficStatistics:
    """
    Аналитическая система трафика.
    
    Собирает и анализирует:
    - Time series congestion по перекрёсткам
    - Тренды и прогнозы
    - Рейтинг загруженности
    - Emergency события
    - Green wave эффективность
    - Аномалии
    """
    
    def __init__(self):
        self.analytics: Dict[str, IntersectionAnalytics] = {}
        
        # Emergency логи
        self.emergency_events: deque = deque(maxlen=50)
        self._current_emergency: Optional[EmergencyEvent] = None
        
        # Green wave логи
        self.green_wave_events: deque = deque(maxlen=50)
        self._current_green_wave: Optional[GreenWaveEvent] = None
        
        # Глобальная статистика
        self._start_time = time.time()
        self._total_emergency_events = 0
        self._total_green_wave_events = 0
    
    def get_or_create(self, intersection_id: str) -> IntersectionAnalytics:
        if intersection_id not in self.analytics:
            self.analytics[intersection_id] = IntersectionAnalytics(intersection_id=intersection_id)
        return self.analytics[intersection_id]
    
    def record_congestion_snapshot(
        self,
        intersection_id: str,
        lane_congestions: Dict[str, float],
        total_cars: int,
        active_lanes: int,
        phase: str = "UNKNOWN",
    ):
        """
        Записать снимок загрузки перекрёстка.
        
        Args:
            intersection_id: ID перекрёстка
            lane_congestions: {lane_id: congestion_level (0..1)}
            total_cars: всего машин на перекрёстке
            active_lanes: количество активных полос
            phase: текущая фаза
        """
        analytics = self.get_or_create(intersection_id)
        
        if lane_congestions:
            avg_cong = sum(lane_congestions.values()) / len(lane_congestions)
            max_cong = max(lane_congestions.values())
        else:
            avg_cong = 0.0
            max_cong = 0.0
        
        snapshot = CongestionSnapshot(
            timestamp=time.time(),
            avg_congestion=avg_cong,
            max_congestion=max_cong,
            total_cars=total_cars,
            active_lanes=active_lanes,
            phase=phase,
        )
        
        analytics.add_snapshot(snapshot)
        analytics.total_cars_served = max(analytics.total_cars_served, total_cars)
        
        # Проверка аномалий
        anomalies = analytics.detect_anomalies()
        for anomaly in anomalies:
            analytics.anomalies.append(anomaly)
            if anomaly.severity == "critical":
                warning("Statistics", f"CRITICAL anomaly: {anomaly.message}")
    
    def record_phase_switch(self, intersection_id: str):
        """Записать переключение фазы"""
        analytics = self.get_or_create(intersection_id)
        analytics.total_phase_switches += 1
    
    def start_emergency(self, intersection_id: str, approach: str, phase: str, cascade: List[str] = None):
        """Начать emergency-событие"""
        self._current_emergency = EmergencyEvent(
            timestamp=time.time(),
            intersection_id=intersection_id,
            approach=approach,
            phase=phase,
            cascade_intersections=cascade or [],
        )
        self._total_emergency_events += 1
        info("Statistics", f"🚨 Emergency START: {intersection_id}/{approach} (phase {phase})")
    
    def end_emergency(self):
        """Завершить emergency-событие"""
        if self._current_emergency:
            self._current_emergency.duration = time.time() - self._current_emergency.timestamp
            self.emergency_events.append(self._current_emergency)
            info("Statistics", f"🚨 Emergency END: {self._current_emergency.intersection_id} "
                 f"({self._current_emergency.duration:.1f}s)")
            self._current_emergency = None
    
    def start_green_wave(self, corridor: List[str], phase: str):
        """Начать зелёную волну"""
        self._current_green_wave = GreenWaveEvent(
            timestamp=time.time(),
            corridor=corridor,
            phase=phase,
        )
        self._total_green_wave_events += 1
        
        # Отмечаем перекрёстки, попавшие в зелёную волну
        for inter_id in corridor:
            analytics = self.get_or_create(inter_id)
            analytics.green_wave_hits += 1
        
        info("Statistics", f"🟢 Green Wave START: {corridor} (phase {phase})")
    
    def end_green_wave(self):
        """Завершить зелёную волну"""
        if self._current_green_wave:
            self._current_green_wave.duration = time.time() - self._current_green_wave.timestamp
            self.green_wave_events.append(self._current_green_wave)
            info("Statistics", f"🟢 Green Wave END: ({self._current_green_wave.duration:.1f}s)")
            self._current_green_wave = None
    
    def get_congestion_ranking(self) -> List[dict]:
        """
        Рейтинг перекрёстков по загруженности.
        Возвращает отсортированный список от самых загруженных к свободным.
        """
        rankings = []
        for inter_id, analytics in self.analytics.items():
            recent = analytics.get_recent_congestion(30)
            if not recent:
                continue
            
            avg = sum(s.avg_congestion for s in recent) / len(recent)
            peak = max(s.avg_congestion for s in recent)
            
            rankings.append({
                "intersection_id": inter_id,
                "avg_congestion": round(avg, 3),
                "peak_congestion": round(peak, 3),
                "total_cars": analytics.total_cars_served,
                "trend": analytics.get_trend_direction(),
                "trend_slope": round(analytics.get_trend(), 5),
                "green_wave_hits": analytics.green_wave_hits,
                "phase_switches": analytics.total_phase_switches,
                "anomalies_count": len(analytics.anomalies),
            })
        
        # Сортировка: сначала самые загруженные
        rankings.sort(key=lambda r: r["avg_congestion"], reverse=True)
        return rankings
    
    def get_emergency_log(self, limit: int = 20) -> List[dict]:
        """Последние emergency-события"""
        events = list(self.emergency_events)
        events.reverse()
        return [
            {
                "timestamp": e.timestamp,
                "intersection_id": e.intersection_id,
                "approach": e.approach,
                "phase": e.phase,
                "cascade": e.cascade_intersections,
                "duration": round(e.duration, 1),
            }
            for e in events[:limit]
        ]
    
    def get_green_wave_log(self, limit: int = 20) -> List[dict]:
        """Последние зелёные волны"""
        events = list(self.green_wave_events)
        events.reverse()
        return [
            {
                "timestamp": e.timestamp,
                "corridor": e.corridor,
                "phase": e.phase,
                "duration": round(e.duration, 1),
            }
            for e in events[:limit]
        ]
    
    def get_anomaly_log(self, limit: int = 50) -> List[dict]:
        """Последние аномалии по всем перекрёсткам"""
        all_anomalies = []
        for analytics in self.analytics.values():
            all_anomalies.extend(analytics.anomalies)
        
        all_anomalies.sort(key=lambda a: a.timestamp, reverse=True)
        return [
            {
                "timestamp": a.timestamp,
                "intersection_id": a.intersection_id,
                "metric": a.metric,
                "value": round(a.value, 3),
                "threshold": round(a.threshold, 3),
                "severity": a.severity,
                "message": a.message,
            }
            for a in all_anomalies[:limit]
        ]
    
    def get_full_statistics(self) -> dict:
        """Полная аналитика системы"""
        uptime = time.time() - self._start_time
        
        # Общие метрики
        total_intersections = len(self.analytics)
        total_cars = sum(a.total_cars_served for a in self.analytics.values())
        total_switches = sum(a.total_phase_switches for a in self.analytics.values())
        
        # Средняя загрузка по сети
        all_avg_congestions = []
        for a in self.analytics.values():
            recent = a.get_recent_congestion(30)
            if recent:
                all_avg_congestions.append(sum(s.avg_congestion for s in recent) / len(recent))
        
        network_avg_congestion = sum(all_avg_congestions) / len(all_avg_congestions) if all_avg_congestions else 0.0
        
        # Перекрёстки с аномалиями
        intersections_with_anomalies = sum(
            1 for a in self.analytics.values() if len(a.anomalies) > 0
        )
        
        # Emergency статистика
        total_emergency_duration = sum(
            e.duration for e in self.emergency_events
        )
        
        # Green wave статистика
        total_gw_duration = sum(e.duration for e in self.green_wave_events)
        
        return {
            "network_summary": {
                "uptime_seconds": round(uptime, 1),
                "uptime_display": self._format_uptime(uptime),
                "total_intersections": total_intersections,
                "total_cars_served": total_cars,
                "total_phase_switches": total_switches,
                "network_avg_congestion": round(network_avg_congestion, 3),
                "total_emergency_events": self._total_emergency_events,
                "total_emergency_duration": round(total_emergency_duration, 1),
                "total_green_wave_events": self._total_green_wave_events,
                "total_green_wave_duration": round(total_gw_duration, 1),
                "intersections_with_anomalies": intersections_with_anomalies,
                "green_wave_efficiency": self._calculate_green_wave_efficiency(),
            },
            "congestion_ranking": self.get_congestion_ranking(),
            "emergency_log": self.get_emergency_log(),
            "green_wave_log": self.get_green_wave_log(),
            "anomaly_log": self.get_anomaly_log(),
        }
    
    def get_intersection_timeseries(
        self, intersection_id: str, seconds: int = 300
    ) -> List[dict]:
        """
        Time series данных загрузки для конкретного перекрёстка.
        Используется для построения графиков.
        """
        analytics = self.analytics.get(intersection_id)
        if not analytics:
            return []
        
        cutoff = time.time() - seconds
        snapshots = [s for s in analytics.congestion_history if s.timestamp >= cutoff]
        
        return [
            {
                "timestamp": s.timestamp,
                "avg_congestion": round(s.avg_congestion, 3),
                "max_congestion": round(s.max_congestion, 3),
                "total_cars": s.total_cars,
                "active_lanes": s.active_lanes,
                "phase": s.phase,
            }
            for s in snapshots
        ]
    
    def _format_uptime(self, seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    
    def _calculate_green_wave_efficiency(self) -> float:
        """
        Эффективность зелёной волны.
        Процент успешных прохождений от общего числа.
        """
        if not self.green_wave_events:
            return 0.0
        
        # Простой metric: отношение длительности волн к общему времени
        total_gw_time = sum(e.duration for e in self.green_wave_events)
        uptime = time.time() - self._start_time
        if uptime == 0:
            return 0.0
        
        return min(1.0, total_gw_time / (uptime * 0.1))  # нормализация


# Синглтон
traffic_stats = TrafficStatistics()