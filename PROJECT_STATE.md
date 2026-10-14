# Project State Analysis: Smart Crossroads Belarus (Intelligent Traffic Control System)

> **Дата анализа:** 20.07.2026 (последняя ревизия: 14.10.2026)
> **Версия бэкенда:** 0.7.0 ("Camera-First Design + Fallback")
> **Репозиторий:** https://github.com/xDefox/intelligent-traffic-control-system.git

---

## 0. ЖУРНАЛ РЕВИЗИИ (сессия 13.10.2026)

### 0.1. Исправлено (код бэкенда)

| Файл | Что исправлено | Почему / обоснование |
|------|----------------|----------------------|
| `backend/core/road_config.py` | Ссылка `lane_intersection_2_approach_1 -> lane_intersection_3X_approach_0` | Ранее была ссылка на несуществующий узел `intersection_3` — перекрёсток фактически называется `intersection_3X`, поэтому рёбра/каскады по этому линку не строились. |
| `backend/services/orchestrator.py` | Удалены `import time`, аннотация `self.traffic_brains: Dict[str, AdaptiveTrafficBrain]` (имя не определено), мёртвый блок записи переключений фаз внутри `_record_statistics` | Неиспользуемые импорты/символы — шум и риск `NameError`; блок переключений фаз в stats **никогда не срабатывал** (проверял фазу до переключения, а она уже переключена) и дублировал cloud-тикер. |
| `backend/services/cloud_orchestrator.py` | Удалён дублирующий вызов записи congestion-snapshot; `_compute_intersection_summary()` возвращает `(total_cars, inter_summary)`; `_record_statistics(inter_summary, commands)`; удалён неиспользуемый импорт `warning` | До дедупликации за один батч (≈3 тика cloud) писала 3 снапшота загрузки на перекрёсток — статистика «звенела». Теперь snapshot пишет **только** fog-оркестратор (per batch), переключения фаз — **только** cloud-тикер. |
| `backend/services/graph_manager.py` | Удалён мёртвый `_build_static_topology()`; добавлены `register_cameras()` (батч), `rebuild_topology()`, `get_approach_toward()`; `_build_edges_from_cameras()` теперь делает `graph.clear()` | См. 0.2 (Big-O) и 0.3 (Emergency). `graph.clear()` устраняет накопление **устаревших рёбер** при смене метаданных камеры (рёбра ранее не чистились никогда). |
| `backend/models/traffic.py` | Удалён `IntersectionUpdateDTO` | Мёртвый DTO, нигде не используется. |
| `backend/UI/ui/panels.py`, `map.py`, `core/lane_utils.py`, `core/logger.py`, `green_wave.py`, `statistics.py` | Вычищены неиспользуемые импорты (ruff F401/F841), `except Exception as e` без использования `e` | Чистота по `ruff check backend/ --select F401,F821,F811,F841` → **All checks passed**. |
| `backend/main.py` | Версия 0.7.0; `uvicorn.run("backend.main:app", ...)`; `load_dotenv()` в начале модуля; хост/порт из `BACKEND_HOST`/`BACKEND_PORT` | `import main` ломался (модуль лежит в пакете `backend`, top-level `main` не существует); env-конфигурация теперь зачитывается до импорта настроек. |
| `backend/main.py` | **Найден и исправлен сломанный роут** `GET /api/v1/statistics/{lane_id}`: вызывал несуществующий `traffic_stats.get_lane_history(...)` → гарантированный 500 | Переписан на существующий `get_intersection_timeseries(inter_id, seconds)` через `parse_lane_id(lane_id)` (параметры `seconds`/`limit`). |
| `backend/services/cloud_orchestrator.py` | Каскад emergency — см. 0.3 | — |
| `backend/services/orchestrator.py` | `_register_cameras()` зовёт `traffic_network.register_cameras(...)` **один раз** на батч | См. 0.2 (Big-O). |

### 0.2. Big-O: батч-регистрация камер (O(C³) → O(C²))

**Проблема:** `register_camera()` вызывался для каждой камеры в цикле, и каждая запись перестраивала топологию целиком — полных перестроек было `C`, каждая стоимостью `O(C²)` (попарная проверка «связаны ли камеры»), итого **O(C³)** на батч.

**Решение:** добавлен `register_cameras(cameras: List[dict])` — все метаданные пишутся в `camera_registry` (O(1) на камеру), затем **одна** `rebuild_topology()` → O(C²) на батч. `register_camera()` сохранён как публичный API (семантика прежняя: метаданные + перестроение) — для внешних вызовов.

**Замер (до/после, 4 уникальных перекрёстка, идентичная итоговая топология):**

| C (камер) | old `register_camera` | new `register_cameras` | ускорение |
|-----------|----------------------|------------------------|-----------|
| 10 | 0.3 ms | 0.0 ms | ×7.7 |
| 20 | 0.5 ms | 0.0 ms | ×12.9 |
| 40 | 1.0 ms | 0.0 ms | ×25.2 |

Рост ускорения ≈ ×2 на удвоение C подтверждает смену класса сложности O(C³)→O(C²).

### 0.3. Emergency: каскад на точный upstream-подход

**Проблема:** в `_process_emergency()` фаза для upstream-перекрёстка бралась от **первой попавшейся** полосы `up_inter` (`for ... in lane_pool: break`) — «зелёный коридор» мог зеленить не ту дорогу, откуда реально едет спецтранспорт.

**Решение:** добавлен `graph_manager.get_approach_toward(source_inter, target_inter, target_approach)` — ищет входящее ребро `(up_inter, up_app) -> (emergency_inter, emergency_approach)` в графе (O(indegree) через `in_edges`), возвращает `up_app`; фаза берётся `get_phase_for_approach(up_inter, up_app)`; в `cascade_cmd["approach"]` кладётся реальный подход. Fallback на первый зарегистрированный подход сохранён (`lane_pool`).

**Тест** (`/tmp/opencode/emergency_test.py`, 10/10 PASS): каскад по ребру 2↔3X ставит на `intersection_2` **approach_1** с корректной фазой (ранее — произвольный первый подход). Emergency сбрасывается по таймеру (10 тиков).

### 0.4. Что протестировано (все зелёные)

- **Setup:** создан `.venv` (Python 3.14.7, требования >=3.13 выполнены), установлено `pip install -e ".[dev]" httpx`.
- **py_compile:** все `.py` бэкенда и `analysis/` — OK.
- **ruff:** `ruff check backend/ --select F401,F821,F811,F841` → All checks passed.
- **Smoke** (`/tmp/opencode/smoke_test.py`, запуск из корня репо `.venv/bin/python`): импорт приложения, версия 0.7.0, валидность `road_config` (все ссылки), POST batch для intersection_2/1/3X → 200, `GET /state`, `/congestion-map`, `/statistics`, `/statistics/{lane_id}` (lane history не пуста), и **дедупликация снапшотов**: ровно 1 запись загрузки на перекрёсток несмотря на ~3 тика cloud на батч.
- **Emergency** (см. 0.3).
- **Benchmark** (см. 0.2).

### 0.5. Инфраструктура / документация (в этой ревизии)

- `pyproject.toml`: добавлен `[tool.setuptools.packages.find] include = ["backend*"]` (иначе `pip install -e .` из README падал: «no package»), entrypoint исправлен `diplom = "backend.main:app"` (был `"main:app"` — top-level модуля нет). Установка зависимостей из README теперь работает.
- `README.md`: `pip install -e .`, Unity 6 (6000.2.5f1) вместо 2022.x, чекбоксы эмулятора — `[ ]` TODO.
- Добавлены в репозиторий **аналитика для конкурса «100 идей для Беларуси»**: `analysis/economics_model.py` + `docs/ECONOMICS.md` (см. раздел 10).

### 0.6. Осталось сделать (см. также раздел 6)

1. **Автотесты в репозитории** — `backend/tests/` на pytest (сейчас проверки — внешние smoke/emergency-скрипты в `/tmp/opencode/`; их нужно перенести в репозиторий и формализовать).
2. **Unity: детекция спецтранспорта** (классы YOLO 2,3,5,7) — бэкенд-каскад уже не зависит от этого, но корректный триггер со стороны камер не реализован.
3. ~~**Rename файла** `TraddicLightViewer.cs` → `TrafficLightViewer.cs`~~ — **сделано** (см. раздел 11.2).
4. **Green Wave**: нелинейные коридоры, реальная скорость потока, динамический `cycle_time` (упомянуто в 4.2 и 6.1).
5. **Прогнозная аналитика** (`backend/services/predictor.py`) и **Ramp Metering** (`ramp_metering.py`) — из задела «как в Дубае».
6. Косметика: `delta_time`/константы по фазовым длительностям в orchestrator (8.0, 25.0, 10.0, 15.0) вынести в константы.

---

## 0b. РЕВИЗИЯ (сессия 14.10.2026): логика фаз, статистика, API

> Unity-часть — см. раздел 11. Ниже — только бэкенд.

### 0b.1. Логика фаз (`orchestrator.py`)

| Что | Было | Стало / почему |
|-----|------|----------------|
| **Расчёт длительности зелёного** | `_calculate_green_duration` шёл по **всему** `lane_pool` без фильтра, суммируя машины по именам фаз (`EW`/`NS`) **всех** перекрёстков | Добавлен фильтр `data["intersection_id"] != inter_id → continue`. Раньше длительность зелёного на перекрёстке A зависела от трафика на B (одинаковые имена фаз). |
| **Дублирование подсчёта** | подсчёт `phase_cars` был продублирован в `_decide_phase` и `_calculate_green_duration`, причём в одном месте с фильтром, в другом — без | Вынесен единый `_count_phase_cars(inter_id, phase_names)` — один источник, невозможно рассогласовать. |
| **min/max длительности фаз** | `generate_phases()` задаёт `min_duration=5`/`max_duration=30`, но `PhaseState` всегда использовал дефолты 8/30 — конфиг был **мёртвым** | `_apply_phase_bounds()` переносит границы из конфига в `PhaseState`. Полы безопасности: min ≥ 3с, max ≥ 5с. |
| **Ранний выход без фаз** | при пустом `phase_names` возвращался **голый список** ответов, а `main.py` распаковывает 3 значения → `ValueError` → 500 при некорректных `lane_id` | Возвращается полный 3-кортеж `(responses, emergency_detected, emergency_phase)`. |
| **Аннотация возврата** | `-> list`, хотя фактически кортеж | `-> Tuple[List[SingleResponseDTO], bool, Optional[str]]`. |

### 0b.2. Статистика

- **Переключения фаз не считались вообще** (`total_phase_switches` всегда 0). Cloud-тикер определял фазу через `_get_intersection_phase`, который читал `lane_pool[...]["current_phase"]`, а это поле **никто не заполнял** → всегда `UNKNOWN` → сравнение `UNKNOWN == UNKNOWN` → 0 переключений.
  **Фикс:** добавлен `graph_manager.set_intersection_phase(inter_id, phase)`; оркестратор публикует активную фазу после каждого решения. Теперь cloud-тикер видит реальную смену фазы и пишет ровно одно переключение на переход.
- **Emergency-события считались на каждый batch.** `report_emergency` вызывался на каждой телеметрии, где камера видит спецтранспорт (несколько раз в секунду) → `_total_emergency_events` раздувался, `_current_emergency` перезаписывался (потеря записей), а `_emergency_cascade_done` сбрасывался → **дублирование каскадных команд каждую секунду**.
  **Фикс:** повторный вызов для того же очага `(intersection, approach)` только продлевает таймер; новое событие/каскад — только при новом очаге.

### 0b.3. API (`main.py`)

- `@app.on_event("startup"/"shutdown")` → **`lifespan`** (deprecated в FastAPI/Starlette; современный способ).
- `GET /api/v1/statistics/{lane_id}`: параметры `seconds`/`limit` ограничены через `Query(..., ge=..., le=...)` (раньше принимались отрицательные значения).

### 0b.4. Каскад (metering магистрали) — ВКЛЮЧЁН

**Было:** `traffic_network.calculate_cascade()` формировал команды `REDUCE_GREEN`/`GREEN_WAVE`, но их **никто не потреблял** — оркестратор читал только команды `green_wave_coordinator` (у тех есть `phase`), а эти команды жили лишь в `/api/v1/state` и WS-broadcast (отображение). Плюс `REDUCE_GREEN` нёс подход *забитого* перекрёстка, а upstream-цели нужен **свой** вливающий подход.

**Стало (metering):**
- `calculate_cascade()` теперь кладёт в `REDUCE_GREEN` подход самого upstream (`get_approach_toward(up_inter, inter_id, approach)`) и добавляет `trigger_intersection`/`trigger_approach` для трассировки. Пороги/коэффициенты вынесены в константы: `CASCADE_CONGESTION_HIGH=0.7`, `CASCADE_CONGESTION_LOW=0.3`, `CASCADE_REDUCE_BASE=5`, `CASCADE_REDUCE_SLOPE=10`, `CASCADE_EXTEND_BY=3`.
- Оркестратор читает команды cloud-тикера (`cloud.get_cascade_commands()`) и применяет их (`orchestrator._cascade_adjustment`):
  - `REDUCE_GREEN` — **укорачивает** удержание активной фазы, `GREEN_WAVE` — **продлевает**.
  - Команда применяется, только если её подход принадлежит **активной** фазе (иначе укоротили бы не тот зелёный).
  - Реальный рычаг — решение бэкенда о фазе: в `_decide_phase` вместо `phase_state.max_duration` используется `eff_max = max(min, min(base_max + extend − reduce, base_max + CASCADE_MAX_EXTEND))`. Поле `green_duration` в ответе тоже корректируется (для UI/Unity).
  - Константы длительности: `GREEN_DURATION_MIN/MAX/BASE/LOAD_SPAN = 8/25/10/15`.

**Смысл:** забитый перекрёсток (>70%) заставляет upstream резать зелёный на вливе — очереди выравниваются (напр. 8-2-4 → ближе к 4-4-4); свободный (<30%) — продлевает для зелёной волны.

> Направление по графу: «забитый притормаживает тех, кто в него вливается». Внутри перекрёстка адаптация (больше зелёного загруженной фазе) реализована отдельно в `_calculate_green_duration` (раздел 0b.1).

### 0b.5. Проверки (все зелёные)

- `py_compile` по `backend/` — OK.
- `ruff check backend/ --select F401,F811,F821,F841,F541` — All checks passed.
- Ad-hoc (`/tmp/opencode/verify_fixes.py`): изоляция подсчёта машин по перекрёстку, публикация `current_phase`, one-shot emergency (3 вызова → 1 событие), 3-кортеж при пустых фазах — 6/6 PASS.
- Ad-hoc (`/tmp/opencode/verify_cascade.py`): каскад A→B — забитый B (90%) даёт `REDUCE_GREEN` для A/approach_0 (−14с); применение только на активной фазе; удержание фазы реально укорачивается (elapsed=25 при base max=30 → переключение); свободный сценарий даёт `GREEN_WAVE` (+3с) — все PASS.
- E2E через `TestClient` (реальный `app` + lifespan): batch → 200; смена EW→NS переключает ответы RED/GREEN и инкрементит `total_phase_switches`.

---

## 0c. РЕВИЗИЯ (сессия 14.10.2026): Flet UI — карта и аналитика

> Unity-часть — см. раздел 11. Ниже — только Flet-дашборд (`backend/UI/`).

### 0c.1. Обработка ошибок

| Что | Было | Стало |
|-----|------|-------|
| **Отказ batch-обработки** | исключение в `handle_batch_telemetry` → 500/обрыв; Unity не получает фаз | Тонкая обёртка: пустой batch → `([], False, None)`; `try/except` вокруг `_handle_batch_telemetry_impl` → **fail-safe** «всем RED» (`green_duration=0.0`) + `error()`/traceback. Раньше при сбое перекрёсток «залипал» на последней фазе. |
| **Остановка cloud-тикера** | `stop()` не дожидался отмены `_tick_task` → «Task was destroyed but it is pending» | `stop()` корректно `await`-ит отмену (`except asyncio.CancelledError`). |

### 0c.2. Вкладка «Карта» (`map.py` + `panels.py`)

- **Было:** `TrafficMap` создавался, но **не добавлялся ни в одну вкладку** — мёртвый код; дашборд был только списком карточек.
- **Стало:** вкладка «🗺 Карта» (Tabs: Список / Карта / Статистика):
  - узел — цвет по `congestion` (свободно/средне/загружено) и размер по загрузке;
  - связи окрашены по загруженности;
  - стрелки: `REDUCE_GREEN` (оранжевая, upstream→trigger), `GREEN_WAVE`/`GREEN_WAVE_SYNC` (голубая), `EMERGENCY_GREEN` — красное кольцо на узле;
  - **клик по узлу** (`ft.GestureDetector`-оверлей, т.к. у `cv.Canvas`/`Shape` нет `on_click`) → панель деталей: фаза, загрузка, машины, подходы, мини-график загрузки за 5 мин;
  - легенда.
- Новый эндпоинт `GET /api/v1/statistics/intersection/{intersection_id}?seconds=` (история для графиков; добавлен **перед** роутом `{lane_id}`, чтобы не перехватывался).

### 0c.3. Аналитика (вкладка «Статистика»)

- **График загрузки сети во времени:** в `statistics.py` добавлены `network_history` + `record_network_snapshot()` + `get_network_timeseries()`; cloud-тикер пишет снимок раз в тик. Рисуется canvas-линией, порог 70% → перегруз красным.
- **Таблица подходов:** `DataTable` по всем полосам (перекрёсток, подход, фаза, машины/вместимость, загрузка %, свет).
- **Экспорт CSV:** кнопка «💾 Экспорт CSV» → `backend/UI/exports/traffic_<ts>.csv` (сводка сети + таблица подходов), путь в SnackBar. Папка добавлена в `.gitignore`.
- **Адаптив vs фикс. таймер (оценка):** идеализированная модель (адаптив отдаёт фазе долю зелёного ∝ спросу; фикс. таймер делит поровну). Показывает долю зелёного критичной фазы и долю обслуживаемого спроса. **Явно помечено как оценка**, не замер задержек.

### 0c.4. Проверки (все зелёные)

- `py_compile`/`compileall` по `backend/` — OK; `ruff check backend --select F` — All checks passed.
- Headless-конструирование UI (stub `Page`): `build_ui` → 3 вкладки; `_render_detail`, `_sparkline`, `_draw_network_chart`, `_draw_lane_table`, `_draw_fixed_vs_adaptive`, `_export_csv` — без ошибок.
- E2E через `TestClient`: batch → 200; `cloud._cascade_tick()` ×3 → `network_timeseries` len=3; `GET /statistics/intersection/intersection_1` → 200.

---

## 1. ОБЩАЯ КОНЦЕПЦИЯ ПРОЕКТА

### 1.1. Назначение
Прототип асинхронного веб-сервиса для интеллектуального регулирования светофорных объектов на основе компьютерного зрения и прогнозной аналитики. Проект вдохновлён архитектурой UTC-UX Fusion (Дубай) и адаптирован под инфраструктурные особенности Республики Беларусь.

### 1.2. Ключевая архитектура: 3-слойная модель (Edge → Fog → Cloud)

```
┌─────────────────────────────────────────────────────────────┐
│                    CLOUD LAYER                               │
│  CloudOrchestrator (тикер 1с)                                │
│  - Каскадный анализ графа                                    │
│  - Green Wave Coordinator                                    │
│  - Агрегация данных со всех перекрёстков                     │
│  - WebSocket broadcast состояния                             │
├─────────────────────────────────────────────────────────────┤
│                    FOG LAYER                                 │
│  TrafficOrchestrator                                         │
│  - Приём телеметрии (REST API)                               │
│  - PhaseManager (единый источник истины о фазах)             │
│  - Принятие решений о переключении фаз                       │
│  - Green Wave override                                       │
├─────────────────────────────────────────────────────────────┤
│                    EDGE LAYER (Unity)                        │
│  IntersectionVisionManager                                   │
│  - YOLO inference на GPU (Barracuda)                         │
│  - Batch telemetry (1 POST / intersection)                   │
│  - EdgeVisionCamera (ROI, NMS, bounding boxes)               │
│  - IntersectionManager (управление светофорами)              │
│  - TrafficGenerator (спавн машин)                            │
│  - WaypointNavigator (навигация + ПДД)                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. ТЕКУЩЕЕ СОСТОЯНИЕ КОМПОНЕНТОВ

### 2.1. БЭКЕНД (Python FastAPI) — `backend/`

#### 2.1.1. `main.py` — Точка входа
- **Статус:** ✅ Работает
- **Порт:** 8050 (env: `BACKEND_HOST`/`BACKEND_PORT`, `.env` через `load_dotenv`)
- **Эндпоинты:**
  - `POST /api/v1/telemetry/batch` — **batch-телеметрия** (все камеры перекрёстка в 1 POST)
  - `GET /api/v1/state` — полное состояние системы
  - `GET /api/v1/congestion-map` — карта загруженности полос
  - `GET /api/v1/statistics` — статистика трафика
  - `GET /api/v1/statistics/{lane_id}` — история загрузки полосы (trend+history, исправлено в ревизии 13.10)
  - `WS /ws/monitor` — WebSocket для UI
- **Запуск:** `uvicorn.run("backend.main:app", ...)` (ранее `"main:app"` — не импортировался)
- **Зависимости:** FastAPI, Uvicorn, Pydantic

#### 2.1.2. `models/traffic.py` — DTO модели
- **Статус:** ✅ Работает
- **Структуры:**
  - `LaneDetectionDTO` — данные с одной полосы (lane_id, car_count, avg_speed, max_capacity)
  - `CameraTelemetryDTO` — телеметрия камеры внутри batch (с метаданными для Camera-First)
  - `BatchTelemetryDTO` — batch от всех камер перекрёстка
  - `SingleResponseDTO` / `BatchResponseDTO` — ответы

#### 2.1.3. `core/road_config.py` — Конфигурация дорожной сети
- **Статус:** ✅ Работает (основная топология строится из телеметрии — Camera-First)
- **Дейфолт-набор (DEFAULT_NETWORK):** 4 перекрёстка (intersection_1, intersection_2, intersection_3X, intersection_3Z)
  - intersection_1: T-образный
  - intersection_2, intersection_3X, intersection_3Z: X-образные
- **Фазы:** NS (север-юг) и EW (восток-запад), генерируются динамически из подходов
- **Связи:** 5 линков между перекрёстками (коридор)
- **Расширяемость:** при наличии `road_network.json` рядом с модулем он переопределяет встроенный набор

#### 2.1.4. `services/orchestrator.py` — Главный оркестратор
- **Статус:** ✅ Работает
- **Функции:**
  - `handle_batch_telemetry()` — **основной метод**: batch-обработка
1. Регистрация камер (Camera-First Design, **батч: O(C²) на батч** — одна перестройка топологии, см. раздел 0.2)
  2. Обновление lane_pool от всех камер
  3. Получение команд зелёной волны
  4. Принятие решения о фазе через PhaseManager
  5. Расчёт длительности зелёного (8-25с, адаптивно)
  6. Запись статистики + формирование ответов + broadcast в WebSocket
- **Логика переключения фаз:**
  - Если машин нет на активной фазе → переключение
  - Если превышен max_duration → переключение
  - Green Wave override приоритетнее

#### 2.1.5. `services/traffic_brain.py` — Per-lane адаптивный мозг
- **Статус:** ❌ Удалён (рефакторинг)
- Per-lane режим убран: единый алгоритм принятия решений — batch через `PhaseManager`. `traffic_brain.py` удалён как dead code.

#### 2.1.6. `services/graph_manager.py` — Граф дорожной сети
- **Статус:** ✅ Работает
- **Технология:** NetworkX (DiGraph)
- **Функции:**
  - Динамическое построение графа из телеметрии камер (Camera-First)
  - `register_cameras()` — батч-регистрация с **одной** перестройкой графа (O(C²), см. 0.2); `register_camera()` — публичный API (метаданные + перестроение)
  - `_build_edges_from_cameras()` — чистит граф перед перестройкой (нет накопления устаревших рёбер)
  - `get_approach_toward()` — подход на перекрёстке к заданному (intersection, approach) по ребру графа (для emergency-каскада, см. 0.3)
  - `update_lane_state()` — обновление состояния полосы
  - `get_congestion_for_phase()` — средняя загруженность фазы
  - `get_upstream_intersections()` / `get_downstream_intersections()` — кэшированная топология
  - `calculate_cascade()` — каскадный анализ (metering):
    - congestion > 70% → upstream урезает зелёный на **своём вливающем** подходе (`REDUCE_GREEN`, порог/коэф. — константы `CASCADE_*`)
    - congestion < 30% → продлевает зелёный (`GREEN_WAVE`, зелёная волна)
    - команды потребляет оркестратор (`_cascade_adjustment`, см. 0b.4)

#### 2.1.7. `services/cloud_orchestrator.py` — Cloud-уровень
- **Статус:** ✅ Работает
- **Функции:**
  - Фоновый тикер раз в 1 секунду
  - Каскадный анализ графа
  - Интеграция с Green Wave Coordinator
  - Broadcast агрегированного состояния в WebSocket

#### 2.1.8. `services/green_wave.py` — Координатор зелёной волны
- **Статус:** ✅ Работает (базовая версия)
- **Параметры:**
  - Целевая скорость: 50 км/ч (13.9 м/с)
  - Цикл светофора: 60с
  - Окно зелёного: 20с
- **Функции:**
  - `_find_corridors()` — поиск линейных коридоров
  - `_calculate_corridor_sync()` — расчёт offset'ов между перекрёстками
  - `_get_current_commands()` — проверка активности окна зелёной волны
- **Ограничение:** Работает только для линейных коридоров, не учитывает реальные расстояния

#### 2.1.9. `services/phase_manager.py` — Менеджер фаз
- **Статус:** ✅ Работает
- **Функции:**
  - Единый источник истины о фазах всех перекрёстков
  - `get_or_create()`, `switch_phase()`, `get_active_phase()`
  - `min_duration` / `max_duration` для каждой фазы

#### 2.1.10. `UI/admin_panel.py` — Административная панель
- **Статус:** ✅ Работает (Flet-based)
- **Функции:**
  - WebSocket подключение к бэкенду
  - Отображение перекрёстков и полос в реальном времени
  - **Три вкладки:** «📋 Список» / «🗺 Карта» / «📈 Статистика»
  - **Карта сети** (`map.py`): цвет+размер по загрузке, стрелки каскада/зелёной волны, emergency-кольцо, **клик по узлу → детали** (+ мини-график за 5 мин), легенда
  - **Аналитика** (см. 0c.3): график загрузки сети во времени, таблица подходов, экспорт CSV, оценка «адаптив vs фикс. таймер»
  - Фильтр по перекрёсткам
  - Индикация зелёной волны и каскадных команд
- **Технология:** Flet 1.0.4 (Flutter-based Python UI); `flet.canvas` для графиков/карты

### 2.2. UNITY СИМУЛЯЦИЯ — `Scene/Diplom_scene/`

#### 2.2.1. `EdgeVisionCamera.cs` — Камера компьютерного зрения
- **Статус:** ✅ Работает
- **Функции:**
  - Захват кадра (RenderTexture 1280×720)
  - YOLO inference через Unity Barracuda
  - ROI (Region of Interest) полигон для детекции
  - NMS (Non-Maximum Suppression) — оптимизированная
  - Визуализация bounding boxes через UI Canvas
  - Редактирование ROI в рантайме

#### 2.2.2. `IntersectionVisionManager.cs` — Менеджер зрения перекрёстка
- **Статус:** ✅ Работает
- **Функции:**
  - Централизованный inference loop для всех камер перекрёстка
  - Batch telemetry отправка (1 POST на перекрёсток)
  - Парсинг batch-ответа и передача команд в IntersectionManager
  - Автоопределение ID перекрёстка из имени GameObject
  - Поддержка X и Z осей камер

#### 2.2.3. `IntersectionManager.cs` (бывш. LightController) — Контроллер светофоров
- **Статус:** ✅ Работает
- **Функции:**
  - Автономный цикл фаз (Z_Green → Yellow → X_Green → Yellow)
  - Внешнее управление от AI (FastAPI) с автоматическим отключением автономного цикла
  - Продление зелёного (renew) без переключения через жёлтый
  - Таймеры обратного отсчёта для каждой оси
  - Поддержка X-axis (approach_0,1) и Z-axis (approach_2,3)

#### 2.2.4. `TrafficGenerator.cs` — Генератор трафика
- **Статус:** ✅ Работает
- **Функции:**
  - Спавн машин на въездах в систему
  - Случайный выбор маршрута
  - Ограничение максимального количества машин (50)
  - Проверка занятости спаунпоинта

#### 2.2.5. `WaypointNavigator.cs` — Навигатор машин
- **Статус:** ✅ Работает
- **Функции:**
  - Движение по графу waypoint'ов
  - Реакция на светофоры (StopTrigger)
  - Правило правой руки (помеха справа)
  - Анти-дедлок механизм (подкрадывание)
  - Детекция машин впереди (SphereCast)
  - Плавное ускорение/торможение

#### 2.2.6. `IntersectionRightOfWay.cs` — Правило правой руки
- **Статус:** ✅ Работает (улучшено v0.8.1)
- **Функции:**
  - Детекция помехи справа (угловой сектор)
  - Таймер ожидания с защитой от дедлока (3s timeout)
  - Режим подкрадывания (creep) при дедлоке
  - Визуализация секторов и состояний в редакторе
  - **НОВОЕ (v0.8.0):** Приоритет машин на перекрёстке над приближающимися
  - **НОВОЕ (v0.8.0):** Приоритет машин, едущих прямо, над поворачивающими
  - **НОВОЕ (v0.8.0):** Метод `MarkCarOnIntersection()` для отслеживания машин на перекрёстке
  - **НОВОЕ (v0.8.1):** Система очередей машин на одной оси

#### 2.2.7. `IntersectionRightOfWay.cs` (дополнительно)
- **НОВОЕ (v0.8.1):** Методы `IsTargetRoadOccupied(string turnDirection)` и `GetTargetRoadCarCount(string direction)`

---

## 3. СООТВЕТСТВИЕ СИСТЕМЕ ДУБАЙ/АБУ-ДАБИ

### 3.1. Что описано в статьях

**Статья 1 (DuneJournal):**
- В Абу-Даби запущена система "умных светофоров" Ramp Metering
- Контролируют поток машин, въезжающих на основные магистрали с второстепенных дорог
- Используют AI для анализа трафика в реальном времени
- Цель: снижение заторов на въездах

**Статья 2 (ZigWheels):**
- Абу-Даби тестирует "умные светофоры" с AI
- Адаптивное управление сигналами на основе реального трафика
- Интеграция с центральной системой управления
- Пилотный проект на ключевых перекрёстках

### 3.2. Маппинг функций проекта на систему Дубай

| Функция Дубай/Абу-Даби | Статус в проекте | Комментарий |
|------------------------|-----------------|-------------|
| **Ramp Metering** (контроль въезда на магистраль) | ❌ Нет | Проект фокусируется на перекрёстках, не на рампах. Можно добавить как отдельный модуль |
| **AI-анализ трафика в реальном времени** | ✅ Есть | YOLO детекция + адаптивные фазы |
| **Адаптивное управление сигналами** | ✅ Есть | PhaseManager + Orchestrator |
| **Центральная система управления** | ✅ Есть | CloudOrchestrator + WebSocket |
| **Green Wave координация** | ✅ Есть (базовая) | GreenWaveCoordinator с линейными коридорами |
| **Каскадное управление** | ✅ Есть | CloudOrchestrator.calculate_cascade() |
| **Batch telemetry** | ✅ Есть | 1 POST на перекрёсток вместо N |
| **Приоритет спецтранспорта** | ✅ Есть (базовая) | Emergency-режим с миганием зелёного, детекция через Physics.OverlapSphere |
| **Прогнозная аналитика** | ❌ Нет | Только реактивное управление |
| **Интеграция с картами** | ⚠️ Частично | Camera-First Design: граф строится динамически из телеметрии |
| **REST API для внешних систем** | ✅ Есть | FastAPI эндпоинты |
| **Визуализация/Дашборд** | ✅ Есть | Flet admin panel |
| **Digital Twin симуляция** | ✅ Есть | Unity 3D сцена |

### 3.3. Ключевые отличия и пробелы

1. **Ramp Metering vs Intersection Control:** Проект ориентирован на перекрёстки, а система Абу-Даби — на ramp metering (контроль въезда на магистраль). Это разные, но дополняющие концепции.

2. **Масштаб:** В проекте 4 перекрёстка (DEFAULT_NETWORK), в реальной системе — сотни.

3. **Прогнозирование:** В проекте нет ML-моделей для прогноза трафика, только реактивная адаптация.

4. **Приоритет спецтранспорта:** Реализован (Emergency-режим с миганием зелёного)

5. **Реальные данные:** Проект использует симулированные данные из Unity, не реальные камеры.

---

## 4. АРХИТЕКТУРНЫЕ ОСОБЕННОСТИ

### 4.1. Сильные стороны
- **Чистая 3-слойная архитектура** (Edge → Fog → Cloud)
- **Batch-обработка** — 1 POST на перекрёсток вместо N
- **PhaseManager** — единый источник истины (устранение дублирования состояния)
- **Кэширование топологии** — O(1) для upstream/downstream запросов
- **Адаптивная длительность зелёного** — на основе congestion ratio
- **Green Wave** — базовая координация коридоров
- **Каскадное управление** — upstream/downstream propagation
- **Digital Twin** — полная симуляция в Unity с YOLO детекцией

### 4.2. Слабые стороны / Проблемы
1. ~~Дублирование состояния фаз~~ — устранено: `PhaseManager` — единый источник истины
2. ~~Per-lane режим~~ — убран: `traffic_brain.py` удалён, основной поток — batch-обработка
3. **Статичная конфигурация (частично решена):**
   - `road_config.py` поддерживает внешнюю конфигурацию `road_network.json`
   - Основная топология строится динамически из телеметрии (Camera-First)

4. **Green Wave упрощена:**
   - Только линейные коридоры
   - Не учитывает реальную скорость потока
   - Offset рассчитывается от статичных позиций

5. **Приоритет спецтранспорта (backend готов, остался trigger в Unity):**
   - Backend: emergency-детектор (`core/emergency.py`) → `cloud.report_emergency()` → каскад «зелёный коридор» на upstream-перекрёстки с **точным подходом** по графу (пофиксено 13.10, см. 0.3)
   - Unity: детекция классов 2,3,5,7 YOLO/Physics.OverlapSphere — TODO

6. **Тесты есть только внешние (вне репозитория):**
   - Smoke-проверки бэкенда (endpoints, дедуп статистики, road_config) — `/tmp/opencode/smoke_test.py` (18/18 PASS)
   - Emergency-каскад — `/tmp/opencode/emergency_test.py` (10/10 PASS)
   - Нет unit-/integration-тестов **внутри репозитория** (`backend/tests/`) — приоритет №1 из раздела 0.6

7. **Flet UI (частично закрыто, см. 0c):**
    - ~~Нет графиков аналитики~~ → добавлены график загрузки сети во времени и мини-графики по узлам
    - ~~Нет сравнения с фиксированным таймером~~ → добавлена оценка (идеализированная модель, помечена как оценка)
    - Добавлены: вкладка «Карта» (клик→детали), таблица подходов, экспорт CSV
    - Осталось: per-intersection time-series как отдельные графики, замер реальных задержек (не модель)

---

## 5. СТРУКТУРА ПРОЕКТА (ПОЛНАЯ)

```
intelligent-traffic-control-system/
├── backend/
│   ├── __init__.py
│   ├── main.py                          # FastAPI сервер (точка входа)
│   ├── core/
│   │   ├── road_config.py               # Конфигурация дорожной сети
│   │   ├── logger.py                    # Централизованное логирование
│   │   ├── lane_utils.py                # Утилиты для lane_id
│   │   └── emergency.py                 # Детекция спецтранспорта
│   ├── models/
│   │   └── traffic.py                   # Pydantic DTO модели
│   ├── services/
│   │   ├── orchestrator.py              # Fog-оркестратор
│   │   ├── graph_manager.py             # NetworkX граф дорожной сети
│   │   ├── cloud_orchestrator.py        # Cloud-уровень (тикер 1с)
│   │   ├── green_wave.py                # Координатор зелёной волны
│   │   ├── phase_manager.py             # Единый менеджер фаз
│   │   └── statistics.py                # Сбор статистики (время ожидания, нагруженность)
│   └── UI/
│       ├── admin_panel.py               # Flet админ-панель
│       └── ui/
│           ├── map.py                   # TrafficMap - визуализация графа
│           └── panels.py                # TrafficUIFactory - панели
├── Scene/
│   └── Diplom_scene/
│       └── Assets/
│           └── Scripts/
│               ├── EdgeVisionCamera.cs          # YOLO камера
│               ├── IntersectionVisionManager.cs # Менеджер зрения
│               ├── IntersectionManager.cs       # Контроллер светофоров
│               ├── TrafficGenerator.cs          # Генератор трафика
│               ├── WaypointNavigator.cs         # Навигатор машин
│               ├── WaypointNode.cs              # Узел waypoint
│               ├── IntersectionRightOfWay.cs    # Правило правой руки
│               ├── TraddicLightViewer.cs        # Визуализация светофора
│               ├── TrafficConstraintsManager.cs # Ограничения трафика
│               ├── Logger.cs                    # Unity логгер
│               └── CarCleanupHandler.cs         # Очистка машин
├── analysis/
│   └── economics_model.py                # Экономическая модель (конкурс «100 идей», см. docs/ECONOMICS.md)
├── docs/
│   └── ECONOMICS.md                      # Расчёт NPV/IRR, варианты пилота
├── PROJECT_STATE.md                     # Данный файл
├── README.md                            # Описание проекта
├── pyproject.toml                       # Python зависимости
├── .python-version                      # Версия Python
├── .env.example                         # Шаблон переменных окружения
└── .gitignore
```

---

## 6. ИНСТРУКЦИИ ДЛЯ ДАЛЬНЕЙШИХ АГЕНТОВ

### 6.1. Приоритетные задачи (по важности)

#### 🔴 КРИТИЧЕСКИЕ (баги / архитектурные проблемы)

1. ~~Устранить дублирование состояния фаз~~ — **выполнено:** `traffic_brain.py` удалён, `PhaseManager` — единый источник истины о фазах
2. ~~Рефакторинг per-lane vs batch~~ — **выполнено:** per-lane режим убран, основной поток — `handle_batch_telemetry()` через `PhaseManager`

#### 🟡 ВАЖНЫЕ (функциональные пробелы)

3. ~~Приоритет спецтранспорта (backend)~~ — **выполнено (13.10):** EmergencyDetector → cloud-каскад «зелёный коридор» с точным upstream-подходом (раздел 0.3).
   - Осталось (Unity): детекция спецтранспорта классами 2,3,5,7 YOLO (`EdgeVisionCamera.cs` / `IntersectionVisionManager.cs`) и передача `is_emergency` в batch
   - Backend приём: `backend/core/emergency.py` → `orchestrator.handle_batch_telemetry()` → `cloud.report_emergency()`

4. **Ramp Metering модуль**
   - Новый файл: `backend/services/ramp_metering.py`
   - Задача: Реализовать контроль въезда на магистраль по аналогии с системой Абу-Даби
   - Концепция: Светофор на въезде, регулирующий поток на основную дорогу

5. **Прогнозная аналитика**
   - Новый файл: `backend/services/predictor.py`
   - Задача: ML-модель для прогноза загрузки на 5-15 минут вперёд
   - Данные: История lane_pool, время суток, день недели

6. **Rename `TraddicLightViewer.cs` → `TrafficLightViewer.cs`** (Unity, правка на Windows)
   - Файл: `Scene/Diplom_scene/Assets/Scripts/TraddicLightViewer.cs`
   - Опечатка в имени класса/файла; поменять имя класса, имя файла и Age-ссылку в сцене

#### 🟢 УЛУЧШЕНИЯ (качество / тестирование)

7. **Unit-тесты в репозитории**
   - Новые файлы: `backend/tests/`
   - Задача: Перенести и формализовать smoke (`/tmp/opencode/smoke_test.py`) и emergency (`/tmp/opencode/emergency_test.py`) в pytest; добавить тесты PhaseManager, GreenWaveCoordinator, TrafficOrchestrator
   - Инструмент: pytest (dev-зависимость установлена)

8. **Динамическая конфигурация**
   - Файл: `backend/core/road_config.py`
   - Задача: Загрузка конфигурации из JSON/YAML файла вместо жёстко заданных данных
   - Формат: GeoJSON для совместимости с картами (задел уже есть: `road_network.json` переопределяет встроенный набор)

9. **Green Wave улучшение**
   - Файл: `backend/services/green_wave.py`
   - Задача: Поддержка нелинейных коридоров, учёт реальной скорости потока, динамический cycle_time

10. **Сравнение с фиксированным таймером**
    - Файл: `backend/UI/admin_panel.py`
    - Задача: Добавить графики сравнения эффективности (среднее время ожидания, пропускная способность)
    - Ссылка: README.md Этап 5

### 6.2. Как запустить проект

```bash
# 0. Установка зависимостей (Python >= 3.13; проверено на 3.14.7)
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]" httpx  # работает — pyproject починен (см. 0.5)

# 1. Запуск бэкенда
cd intelligent-traffic-control-system
python -m backend.main

# 2. Запуск админ-панели (отдельный терминал)
python -m backend.UI.admin_panel

# 3. Unity сцена
# Открыть Scene/Diplom_scene в Unity Editor и нажать Play
```

### 6.3. Ключевые эндпоинты API

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/api/v1/telemetry/batch` | Batch-телеметрия (все камеры перекрёстка) |
| GET | `/api/v1/state` | Полное состояние системы |
| GET | `/api/v1/congestion-map` | Карта загруженности полос |
| GET | `/api/v1/statistics` | Статистика трафика |
| GET | `/api/v1/statistics/{lane_id}` | История загрузки полосы (timeseries + trend) |
| WS | `/ws/monitor` | WebSocket для real-time мониторинга |

### 6.4. Формат batch-запроса

```json
{
  "intersection_id": "intersection_1",
  "cameras": [
    {
      "camera_id": "intersection_1_approach_0",
      "lanes": [
        {
          "lane_id": "intersection_1_approach_0",
          "car_count": 3,
          "avg_speed": 12.5,
          "max_capacity": 10
        }
      ]
    }
  ]
}
```

### 6.5. Формат batch-ответа

```json
{
  "type": "batch_response",
  "responses": [
    {
      "camera_id": "intersection_1_approach_0",
      "target_phase": "GREEN",
      "green_duration": 15.0
    }
  ]
}
```

---

## 7. ТЕХНИЧЕСКИЙ ДОЛГ

| Проблема | Где | Серьёзность |
|----------|-----|-------------|
| ~~Дублирование состояния фаз~~ (решено) | PhaseManager — единый источник | HIGH |
| ~~Per-lane код~~ (удалён) | traffic_brain.py удалён | MEDIUM |
| ~~Построчный register_camera (O(C³))~~ (решено 13.10) | graph_manager.register_cameras — O(C²) | MEDIUM |
| ~~Накопление устаревших рёбер графа~~ (решено 13.10) | graph.clear() в _build_edges_from_cameras | MEDIUM |
| ~~Дубль снапшотов загрузки~~ (решено 13.10) | только fog-оркестратор пишет snapshot | MEDIUM |
| ~~Сломанный /statistics/{lane_id}~~ (решено 13.10) | get_intersection_timeseries + parse_lane_id | HIGH |
| ~~Каскад emergency по первому подходу~~ (решено 13.10) | get_approach_toward по графу | HIGH |
| Нет unit-тестов в репозитории | backend/tests/ — TODO (см. 6.1 #7) | MEDIUM |
| Нет обработки ошибок в batch | orchestrator.py handle_batch_telemetry | MEDIUM |
| Жёстко заданные константы | green_wave.py (CYCLE_TIME, GREEN_WINDOW) | LOW |
| Магические числа в фазах | orchestrator.py (8.0, 25.0, 10.0, 15.0) | LOW |
| Нет graceful shutdown | cloud_orchestrator.py | LOW |
| Flet UI не обновляется при ошибках | admin_panel.py | LOW |
| Нет валидации конфигурации | road_config.py | MEDIUM |

---

## 8. ЗАКЛЮЧЕНИЕ

Проект представляет собой **работающий прототип** адаптивной системы управления светофорами с 3-слойной архитектурой, вдохновлённой системой Дубай. 

**Что уже реализовано:**
- ✅ Полный цикл: YOLO детекция → batch telemetry → адаптивное решение → управление светофорами
- ✅ 3-слойная архитектура (Edge → Fog → Cloud)
- ✅ Green Wave координация
- ✅ Каскадное управление
- ✅ Digital Twin симуляция в Unity
- ✅ WebSocket мониторинг + админ-панель

**Что нужно для полного соответствия системе Дубай/Абу-Даби:**
- ❌ Ramp Metering модуль
- ⚠️ Приоритет спецтранспорта (улучшить детекцию классов 2,3,5,7)
- ❌ Прогнозная аналитика (ML)
- ❌ Масштабирование на реальную карту города

**Рекомендация:** Сфокусироваться на критических архитектурных проблемах (дублирование состояния, унификация per-lane/batch), затем улучшить детекцию спецтранспорта (классы 2,3,5,7 YOLO), и только потом — ramp metering и прогнозную аналитику.

---

## 9. ЛОГИРОВАНИЕ (v0.8.0)

### 9.1. Централизованный логгер

**Файл:** `backend/core/logger.py`

**Концепция:** Управляемое логирование через переменную окружения `LOG_LEVEL`.

**Уровни логирования:**
- `DEBUG` — все сообщения (много шума, для разработки)
- `INFO` — основные события (рекомендуется для продакшена)
- `WARNING` — только предупреждения и ошибки
- `ERROR` — только ошибки
- `OFF` — логирование отключено

**Использование:**
```python
from backend.core.logger import debug, info, warning, error

# DEBUG - для детальной отладки (видно только при LOG_LEVEL=DEBUG)
debug("Orchestrator", f"Batch from {inter_id}: {len(batch.cameras)} cameras")

# INFO - для важных событий (видно при LOG_LEVEL=INFO и ниже)
info("CloudOrchestrator", f"🚨 EMERGENCY: {intersection_id}/{approach} phase={phase}")

# WARNING - для предупреждений
warning("GraphManager", f"No cameras registered for {intersection_id}")

# ERROR - для ошибок
error("CloudOrchestrator", f"Tick error: {e}")
```

**Настройка:**
```bash
# По умолчанию INFO (только важные сообщения)
python -m backend.main

# Для отладки - включить DEBUG
LOG_LEVEL=DEBUG python -m backend.main

# Отключить логирование
LOG_LEVEL=OFF python -m backend.main
```

### 9.2. Camera-First Design (v0.7.0)

**Концепция:** Система строит карту дорог на основе данных камер, а не из конфиг-файла.

**Реализовано:**
- Камеры отправляют `direction` (N/S/E/W) и `world_position` в batch telemetry
- Backend автоматически определяет связи между камерами (противоположные направления + расстояние < 200м)
- Граф строится динамически из телеметрии
- Endpoint `GET /api/v1/congestion-map` для Unity-машин

**Файлы:**
- `backend/models/traffic.py` - новые поля в CameraTelemetryDTO
- `backend/services/graph_manager.py` - `register_camera()`, `_build_edges_from_cameras()`
- `Scene/Diplom_scene/Assets/Scripts/EdgeVisionCamera.cs` - `GetWorldDirection()`
- `Scene/Diplom_scene/Assets/Scripts/IntersectionVisionManager.cs` - метаданные в batch

### 9.3. Fallback-механика (v0.7.0)

**Концепция:** При потере связи с бэкендом светофоры автоматически переключаются на статический режим.

**Реализовано:**
- Счётчик неудачных запросов (порог: 5)
- При 5 неудачных запросах: автономный цикл включается автоматически
- При успешном ответе: счётчик сбрасывается

**Файлы:**
- `Scene/Diplom_scene/Assets/Scripts/IntersectionManager.cs` - `OnBackendResponseSuccess/Failed()`, `FallbackCheckRoutine()`
- `Scene/Diplom_scene/Assets/Scripts/IntersectionVisionManager.cs` - вызов fallback-методов

### 9.4. Удалён граф из admin_panel

- Вкладка "Граф" удалена (осталась только "Список")

### 9.5. Unity Logger (v0.8.0)

**Файл:** `Scene/Diplom_scene/Assets/Scripts/Logger.cs`

**Концепция:** Централизованное логирование в Unity через PlayerPrefs.

**Уровни логирования:**
- `DEBUG` — все сообщения (много шума, для разработки)
- `INFO` — основные события (рекомендуется для продакшена)
- `WARNING` — только предупреждения
- `ERROR` — только ошибки
- `OFF` — логирование отключено

**Использование:**
```csharp
// DEBUG - для детальной отладки (видно только при LOG_LEVEL=DEBUG)
Logger.LogDebug("IntersectionVisionManager", $"Camera {i}: {cameraResults[i]} cars");

// INFO - для важных событий (видно при LOG_LEVEL=INFO и ниже)
Logger.LogInfo("IntersectionVisionManager", $"🚨 Emergency vehicle on camera {i}");

// WARNING - для предупреждений
Logger.LogWarning("IntersectionVisionManager", $"YOLO model not assigned!");

// ERROR - для ошибок
Logger.LogError("IntersectionVisionManager", $"Batch request failed: {request.error}");
```

**Настройка в Unity:**
- Через PlayerPrefs: `PlayerPrefs.SetString("LOG_LEVEL", "DEBUG")`
- Или в коде: `Logger.SetLogLevel(Logger.LogLevel.DEBUG)`

---

## 10. ЭКОНОМИЧЕСКАЯ МОДЕЛЬ (конкурс «100 идей для Беларуси»)

Файлы: `analysis/economics_model.py` (скрипт расчёта), `docs/ECONOMICS.md` (объяснение и расчёты).

Ключевые результаты (подробно — в `docs/ECONOMICS.md`):
- **Запрашиваемый грант:** 78 775 BYN (≈ 25 900 USD) — вариант с нулевой ставкой дисконтирования (8-летний горизонт для города).
- **Городская перспектива (пилот 1):** NPV = +67 609 BYN, 5 лет, ставка 9.5%.
- **Вендорская перспектива (тираж 100 шт):** NPV = +297 903 BYN, IRR = 69.2% (не 219% — ошибка/опечатка прежней версии модели).
- **Точки безубыточности (относительно движения):** городская модель — безубыточен от 18.2% эффекта; вендорская — уже безубыточен базово.
- Скрипт читается и исполняется как модуль: `python -m analysis.economics_model`.

---

## 11. UNITY: состояние сцены и известные проблемы (ревизия 14.10.2026)

### 11.1. Симптом и причина (было: фазы не переключались, лампы не горели)

**Симптом:** бэкенд запущен и корректно отвечает `GREEN`/`RED`, но в Unity светофоры стоят красными и не загораются, фаза не отображается.

**Диагностика:**
1. Бэкенд проверен отдельно — фазовая логика исправна (EW → `GREEN` для `approach_0/1`, `RED` для `2/3`; проверено с машинами, без машин, и без метаданных камер). Путь ответа `/api/v1/telemetry/batch` в ревизии кода **не менялся**.
2. В сценах Unity компонент `IntersectionManager` в `m_Script` ссылался на **несуществующий GUID** `f69e1b6e9ddddbe428847eccc62cd9d1`, тогда как у `Assets/Scripts/IntersectionManager.cs` GUID = `30562c420c317d0469b962cf732fb09c`. Такая нестыковка была **уже до ревизии** (в коммите `6a3d7e9`), т.е. пре-существующий дефект.

**Почему это ломало именно так:**
- Unity показывал у компонента «Missing (Mono Script)» → `IntersectionManager.Start()` не вызывался → управление лампами и автономный цикл не работали.
- `IntersectionVisionManager.intersectionController` указывал на этот «мёртвый» компонент, а обработка ответа обёрнута в `if (… && intersectionController != null)` → команды фаз не применялись. Телеметрия уходила, ответ приходил, применять его было не к чему.

**Исправление (коммит `ee1d997`):** во всех сценах GUID `f69e1b6e…` заменён на актуальный `30562c…`:
- `SampleScene.unity` (2), `scene_easy_intersection.unity` (4), `scene_backup.unity` (3), `_Recovery/0*.unity`.
- Поля компонента (`xAxisLights`, `zAxisLights`, `useAutonomousCycle`, `zGreenDuration`, `yellowDuration`, `xGreenDuration`, `enableDebugLogs`) совпадают с `IntersectionManager` — замена безопасна; ссылка `intersectionController` переподключается по `fileID`.

**Проверка после правки:** в Hierarchy у объектов `IntersectionManager*` пропадает значок ⚠; сцена ссылается на валидный скрипт.

### 11.2. Переименование и баг эмиссии светофора (исправлено)

- Файл `Assets/Scripts/TraddicLightViewer.cs` содержал класс `TrafficLightViewer` — **несовпадение имени файла и класса** (Unity не привязывает такой MonoBehaviour). Файл переименован в `TrafficLightViewer.cs` вместе с `.cs.meta` (GUID `af406a1114f4e9a43b2cd7e7221096e8` сохранён, ссылки в сценах остались валидны).
- Баг отображения: `currentLight` инициализировался как `Red`, а `SwitchToColor()` выходил раньше (`if (newColor == currentLight) return;`) → самый первый красный **никогда не включал эмиссию** (красный не светился, пока не переключишься в другой цвет и обратно). Добавлен `ApplyColor()` и принудительный вызов стартового цвета в `Start()`.

### 11.3. Про «Missing Script» у RoadSegment / OncomingTrafficDetector — это НЕ баг

При анализе сцены на чистом клоне `RoadSegment` (×54) и `OncomingTrafficDetector` (×1) выглядят как «Missing (Mono Script)». Это **ожидаемо и не является дефектом**:

- `RoadSegment` — это **локальный префаб «просто дорога»**, который автор сцены намеренно **не коммитит** в репозиторий (как и связанный с ним скрипт). Аналогично, вероятно, и `OncomingTrafficDetector`.
- Поэтому на машине автора (где префабы есть) ссылки резолвятся и всё работает, а на чистом клоне `Assets/` их не содержит → Unity рисует «Missing».
- Компоненты `IntersectionManager`/`TrafficLightViewer` к этим префабам отношения не имеют; на управление светофорами они не влияют.

**Вывод:** отдельно восстанавливать эти скрипты не нужно. Если же сцену предполагается открывать на «чистой» машине, стоит либо положить префабы дорог в репозиторий (например, в `Assets/Prefabs/`), либо удалить ссылки на них из сцены. Решение за автором.

### 11.4. Чек-лист настройки сцены (для дальнейших правок)

1. На GameObject перекрёстка должны быть **оба** компонента: `IntersectionManager` (управление лампами) и `IntersectionVisionManager` (телеметрия).
2. `IntersectionManager`:
   - `xAxisLights` — список светофоров оси X (`approach_0,1`);
   - `zAxisLights` — светофоры оси Z (`approach_2,3`);
   - `useAutonomousCycle` — по умолчанию в сцене выключен (`0`), управление от бэкенда.
3. `IntersectionVisionManager`:
   - `xAxisCameras` / `zAxisCameras` — заполнены (`EdgeVisionCamera`), иначе `Start()` пишет ошибку и выходит;
   - `intersectionController` — ссылка на `IntersectionManager`;
   - `sharedYoloModel` — назначена YOLO-модель;
   - `batchTelemetryUrl` = `http://127.0.0.1:8050/api/v1/telemetry/batch` (порт должен совпадать с `BACKEND_PORT`/`.env`).
4. Каждый светофор (`TrafficLightViewer`) — заполнены `redLight`/`yellowLight`/`greenLight` (MeshRenderer сфер), включён emission-материал.
5. Порядок запуска: сначала бэкенд (`python -m backend.main`), затем Play в Unity. Проверить в консоли Unity строки `🚦 Started: ID=…` и отсутствие «Missing Script».

### 11.5. Как быстро находить битые ссылки на скрипты в сценах

GUID скрипта лежит в `<Script>.cs.meta`. Если в `.unity` строка `m_Script` ссылается на GUID, которого нет среди `.cs.meta`, — компонент «Missing». Быстрая проверка (из корня репо):

```bash
# показать все GUID скриптов
grep -h guid Scene/Diplom_scene/Assets/**/*.cs.meta
# показать все ссылки сцен на скрипты
grep -rh "m_Script:" Scene/Diplom_scene/Assets/**/*.unity | sort -u
```
