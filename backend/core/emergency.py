"""
Единый детектор спецтранспорта (emergency vehicle).

Централизует логику обнаружения спецтранспорта из batch-телеметрии.
Раньше эта логика дублировалась в orchestrator.py и main.py.
"""


from backend.models.traffic import BatchTelemetryDTO, CameraTelemetryDTO
from backend.services.graph_manager import traffic_network


class EmergencyDetector:
    """
    Детектор спецтранспорта.

    Сканирует batch-телеметрию и определяет:
    - emergency_detected: True если любая камера сообщила о спецтранспорте
    - emergency_approach: подход, на котором обнаружен спецтранспорт
    - emergency_phase: фаза, соответствующая этому подходу
    """

    @staticmethod
    def detect(batch: BatchTelemetryDTO) -> tuple[bool, str | None, str | None]:
        """
        Проверить batch на наличие спецтранспорта.

        Returns:
            (emergency_detected, emergency_approach, emergency_phase)
        """
        for cam in batch.cameras:
            if cam.emergency_vehicle_detected and cam.emergency_approach:
                emergency_approach = cam.emergency_approach
                emergency_phase = traffic_network.get_phase_for_approach(
                    batch.intersection_id, emergency_approach
                )
                return (True, emergency_approach, emergency_phase)

        return (False, None, None)

    @staticmethod
    def get_emergency_approaches(cameras: list[CameraTelemetryDTO]) -> list[str]:
        """Получить список всех подходов с обнаруженным спецтранспортом."""
        return [
            cam.emergency_approach
            for cam in cameras
            if cam.emergency_vehicle_detected and cam.emergency_approach
        ]
