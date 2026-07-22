# План рефакторинга и чистки проекта

## Этап 1: Архитектурная чистка (удаление dead code)
- [x] Проанализировать все файлы проекта
- [x] Составить план рефакторинга
- [x] Удалить `traffic_brain.py` (dead code — не используется в batch-режиме)
- [x] Удалить `createModel.py` (скрипт тренировки, не используется в runtime)
- [x] Удалить `CameraA.md` (концептуальная документация, уже реализована)
- [x] Удалить `dune_article.html` (справочный материал)
- [x] Удалить `DTO/TrafficDTOs.cs` (устаревший, логика в IntersectionVisionManager.cs)
- [x] Удалить `IntersectionTargetDetector.cs` (дублирует IntersectionRightOfWay.cs)
- [x] Удалить `OncomingTrafficDetector.cs` (не используется, не решает проблему "танки")
- [x] Очистить артефакты: `runs/`, `__pycache__/`, `.pt` файлы из `core/`

## Этап 2: Выделение общих утилит (backend)
- [x] Создать `backend/core/lane_utils.py` — normalize_lane_id(), denormalize_lane_id(), parse_lane_id(), extract_approach_from_camera_id()
- [x] Создать `backend/core/emergency.py` — EmergencyDetector (единый класс)
- [x] Обновить `graph_manager.py` — заменить `_resolve_intersection_and_approach` на `parse_lane_id`

## Этап 3: Рефакторинг orchestrator.py (SRP)
- [x] Разбить `handle_batch_telemetry` на методы (_register_cameras, _update_lane_pool, _decide_phase, _calculate_green_duration, _build_responses, _record_statistics)
- [x] Вынести emergency-логику из main.py в orchestrator (apply_emergency_override)
- [x] Удалить `handle_telemetry()` (per-lane endpoint)

## Этап 4: Рефакторинг cloud_orchestrator.py (SRP)
- [] Разбить `_cascade_tick` на методы (_process_green_wave, _process_emergency, _compute_intersection_summary, _record_statistics, _broadcast_state)

## Этап 5: Рефакторинг admin_panel.py
- [ ] Убрать debug print → logger (debug())
- [ ] Использовать `denormalize_lane_id` из lane_utils
- [ ] Разбить на модули: ui/map.py, ui/panels.py (оставлено на будущую итерацию)
- [ ] Исправить хардкод индексов в TrafficMap.refresh() (оставлено на будущую итерацию)

## Этап 6: Unity — чистка и дедупликация
- [ ] Переименовать `LightController.cs` → `IntersectionManager.cs`
- [ ] Унифицировать GetWorldDirection() в static helper (оставлено на будущую итерацию)

## Этап 7: Инфраструктура
- [x] Заполнить `pyproject.toml` зависимостями (fastapi, uvicorn, pydantic, networkx, flet, websockets, python-dotenv)
- [x] Добавить `.env.example`

## Этап 8: Визуализация
- [ ] Переписать Readme файлы (оставлено на будущую итерацию)
