# Project State Analysis: Smart Crossroads Belarus (Intelligent Traffic Control System)

> **Дата анализа:** 20.07.2026
> **Версия бэкенда:** 0.7.0 ("Camera-First Design + Fallback")
> **Репозиторий:** https://github.com/xDefox/intelligent-traffic-control-system.git

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
- **Порт:** 8050
- **Эндпоинты:**
  - `POST /api/v1/telemetry` — одиночная телеметрия с камеры
  - `POST /api/v1/telemetry/batch` — **batch-телеметрия** (все камеры перекрёстка в 1 POST)
  - `GET /api/v1/state` — полное состояние системы
  - `WS /ws/monitor` — WebSocket для UI
- **Зависимости:** FastAPI, Uvicorn, Pydantic

#### 2.1.2. `models/traffic.py` — DTO модели
- **Статус:** ✅ Работает
- **Структуры:**
  - `LaneDetectionDTO` — данные с одной полосы (lane_id, car_count, avg_speed, max_capacity)
  - `IntersectionUpdateDTO` — телеметрия от одной камеры
  - `CameraTelemetryDTO` — телеметрия камеры внутри batch
  - `BatchTelemetryDTO` — batch от всех камер перекрёстка
  - `SingleResponseDTO` / `BatchResponseDTO` — ответы

#### 2.1.3. `core/road_config.py` — Конфигурация дорожной сети
- **Статус:** ✅ Работает (но ограничен)
- **Перекрёстки:** 3 шт. (intersection_1, intersection_2, intersection_3)
  - intersection_1: T-образный
  - intersection_2, intersection_3: X-образные
- **Фазы:** NS (север-юг) и EW (восток-запад)
- **Связи:** 4 линка между перекрёстками (линейный коридор)
- **Ограничение:** Конфигурация статична, не масштабируется под реальную карту

#### 2.1.4. `services/orchestrator.py` — Главный оркестратор
- **Статус:** ✅ Работает
- **Функции:**
  - `handle_telemetry()` — обработка одиночной телеметрии (per-lane режим)
  - `handle_batch_telemetry()` — **основной метод**: batch-обработка
    1. Обновление lane_pool от всех камер
    2. Получение команд зелёной волны
    3. Принятие решения о фазе через PhaseManager
    4. Расчёт длительности зелёного (8-25с, адаптивно)
    5. Формирование ответов + broadcast в WebSocket
- **Логика переключения фаз:**
  - Если машин нет на активной фазе → переключение
  - Если превышен max_duration → переключение
  - Green Wave override приоритетнее

#### 2.1.5. `services/traffic_brain.py` — Адаптивный мозг светофора
- **Статус:** ✅ Работает (per-lane режим)
- **Функции:**
  - `process_lane_telemetry()` — решает GREEN/RED для конкретного подхода
  - Адаптивная длительность зелёного на основе congestion_ratio
  - `apply_cascade_command()` — урезание/продление зелёного по командам Cloud
- **Важно:** Фазу НЕ переключает — только проверяет активность своей фазы

#### 2.1.6. `services/graph_manager.py` — Граф дорожной сети
- **Статус:** ✅ Работает
- **Технология:** NetworkX (DiGraph)
- **Функции:**
  - Построение графа из ROADS
  - `update_lane_state()` — обновление состояния полосы
  - `get_congestion_for_phase()` — средняя загруженность фазы
  - `get_upstream_intersections()` / `get_downstream_intersections()` — кэшированная топология
  - `calculate_cascade()` — каскадный анализ:
    - congestion > 70% → upstream урезает зелёный
    - congestion < 30% → downstream продлевает зелёный (зелёная волна)

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
  - Граф дорожной сети с цветовой индикацией загрузки
  - Фильтр по перекрёсткам
  - Индикация зелёной волны и каскадных команд
- **Технология:** Flet (Flutter-based Python UI)

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

#### 2.2.3. `LightController.cs` (IntersectionManager) — Контроллер светофоров
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
| **Адаптивное управление сигналами** | ✅ Есть | PhaseManager + TrafficBrain + Orchestrator |
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

2. **Масштаб:** В проекте 3 перекрёстка, в реальной системе — сотни.

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
1. **Дублирование состояния фаз:**
   - `_intersection_phase_state` в `traffic_brain.py` (глобальный словарь)
   - `PhaseManager` в `orchestrator.py`
   - Код в `orchestrator.py` (строки 55-61) синхронизирует их, что костыль

2. **Per-lane режим не используется:**
   - `handle_telemetry()` (одиночный) редко вызывается
   - Основной поток — `handle_batch_telemetry()`, который не использует `AdaptiveTrafficBrain`
   - `traffic_brain.py` фактически мёртвый код для batch-режима

3. **Статичная конфигурация:**
   - `road_config.py` — жёстко заданные 3 перекрёстка
   - Нет динамического добавления/удаления перекрёстков
   - Нет загрузки из файла/БД

4. **Green Wave упрощена:**
   - Только линейные коридоры
   - Не учитывает реальную скорость потока
   - Offset рассчитывается от статичных позиций

5. **Приоритет спецтранспорта (базовая реализация):**
   - Emergency-режим реализован (мгновенное зелёное на фазе)
   - Детекция через Physics.OverlapSphere работает
   - Классы 2,3,5,7 YOLO можно добавить для улучшения детекции

6. **Отсутствие тестов:**
   - Нет unit-тестов
   - Нет integration-тестов
   - Нет нагрузочного тестирования

7. **Flet UI ограничен:**
    - Нет графиков аналитики
    - Нет сравнения с фиксированным таймером
    - История данных добавлена (через statistics.py)

---

## 5. СТРУКТУРА ПРОЕКТА (ПОЛНАЯ)

```
D:/Education/Diplom/
├── backend/
│   ├── __init__.py
│   ├── main.py                          # FastAPI сервер (точка входа)
│   ├── core/
│   │   └── road_config.py               # Конфигурация дорожной сети
│   ├── models/
│   │   └── traffic.py                   # Pydantic DTO модели
│   ├── services/
│   │   ├── orchestrator.py              # Fog-оркестратор
│   │   ├── traffic_brain.py             # Per-lane адаптивный мозг
│   │   ├── graph_manager.py             # NetworkX граф дорожной сети
│   │   ├── cloud_orchestrator.py        # Cloud-уровень (тикер 1с)
│   │   ├── green_wave.py                # Координатор зелёной волны
│   │   ├── phase_manager.py             # Единый менеджер фаз
│   │   └── statistics.py                # Сбор статистики (время ожидания, нагруженность)
│   └── UI/
│       └── admin_panel.py               # Flet админ-панель
├── Scene/
│   └── Diplom_scene/
│       └── Assets/
│           └── Scripts/
│               ├── EdgeVisionCamera.cs          # YOLO камера
│               ├── IntersectionVisionManager.cs # Менеджер зрения
│               ├── LightController.cs           # Контроллер светофоров
│               ├── TrafficGenerator.cs          # Генератор трафика
│               ├── WaypointNavigator.cs         # Навигатор машин
│               ├── WaypointNode.cs              # Узел waypoint
│               ├── IntersectionRightOfWay.cs    # Правило правой руки
│               ├── TraddicLightViewer.cs        # Визуализация светофора
│               └── CarCleanupHandler.cs         # Очистка машин
├── PROJECT_STATE.md                     # Данный файл
├── README.md                            # Описание проекта
├── pyproject.toml                       # Python зависимости
├── .python-version                      # Версия Python
└── .gitignore
```

---

## 6. ИНСТРУКЦИИ ДЛЯ ДАЛЬНЕЙШИХ АГЕНТОВ

### 6.1. Приоритетные задачи (по важности)

#### 🔴 КРИТИЧЕСКИЕ (баги / архитектурные проблемы)

1. **Устранить дублирование состояния фаз**
   - Файлы: `backend/services/traffic_brain.py` (строки 10-21), `backend/services/orchestrator.py` (строки 55-61)
   - Задача: Удалить `_intersection_phase_state` из `traffic_brain.py`, перевести `AdaptiveTrafficBrain` на использование `PhaseManager`
   - Риск: Рассогласование фаз между per-lane и batch режимами

2. **Рефакторинг per-lane vs batch**
   - Файл: `backend/services/orchestrator.py`
   - Задача: `handle_telemetry()` (per-lane) использует `AdaptiveTrafficBrain`, а `handle_batch_telemetry()` — нет. Нужно унифицировать или явно разделить.
   - Риск: Два разных алгоритма принятия решений

#### 🟡 ВАЖНЫЕ (функциональные пробелы)

3. **Приоритет спецтранспорта**
   - Файлы: `Scene/Diplom_scene/Assets/Scripts/EdgeVisionCamera.cs` (строка 28), `backend/services/orchestrator.py`
   - Задача: Реализовать детекцию спецтранспорта (классы 2,3,5,7 YOLO) и "зелёный коридор"
   - Ссылка: README.md Этап 3

4. **Ramp Metering модуль**
   - Новый файл: `backend/services/ramp_metering.py`
   - Задача: Реализовать контроль въезда на магистраль по аналогии с системой Абу-Даби
   - Концепция: Светофор на въезде, регулирующий поток на основную дорогу

5. **Прогнозная аналитика**
   - Новый файл: `backend/services/predictor.py`
   - Задача: ML-модель для прогноза загрузки на 5-15 минут вперёд
   - Данные: История lane_pool, время суток, день недели

#### 🟢 УЛУЧШЕНИЯ (качество / тестирование)

6. **Unit-тесты**
   - Новые файлы: `backend/tests/`
   - Задача: Написать тесты для PhaseManager, GreenWaveCoordinator, TrafficOrchestrator
   - Инструмент: pytest

7. **Динамическая конфигурация**
   - Файл: `backend/core/road_config.py`
   - Задача: Загрузка конфигурации из JSON/YAML файла вместо жёстко заданных данных
   - Формат: GeoJSON для совместимости с картами

8. **Green Wave улучшение**
   - Файл: `backend/services/green_wave.py`
   - Задача: Поддержка нелинейных коридоров, учёт реальной скорости потока, динамический cycle_time

9. **Сравнение с фиксированным таймером**
   - Файл: `backend/UI/admin_panel.py`
   - Задача: Добавить графики сравнения эффективности (среднее время ожидания, пропускная способность)
   - Ссылка: README.md Этап 5

### 6.2. Как запустить проект

```bash
# 1. Запуск бэкенда
cd D:/Education/Diplom
python -m backend.main

# 2. Запуск админ-панели (отдельный терминал)
python -m backend.UI.admin_panel

# 3. Unity сцена
# Открыть Scene/Diplom_scene в Unity Editor и нажать Play
```

### 6.3. Ключевые эндпоинты API

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/api/v1/telemetry` | Одиночная телеметрия с камеры |
| POST | `/api/v1/telemetry/batch` | Batch-телеметрия (все камеры перекрёстка) |
| GET | `/api/v1/state` | Полное состояние системы |
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
| Дублирование состояния фаз | traffic_brain.py / orchestrator.py | HIGH |
| Per-lane код не используется | traffic_brain.py | MEDIUM |
| Нет обработки ошибок в batch | orchestrator.py handle_batch_telemetry | MEDIUM |
| Жёстко заданные константы | green_wave.py (CYCLE_TIME, GREEN_WINDOW) | LOW |
| Нет graceful shutdown | cloud_orchestrator.py | LOW |
| Flet UI не обновляется при ошибках | admin_panel.py | LOW |
| Нет валидации конфигурации | road_config.py | MEDIUM |
| Магические числа в фазах | orchestrator.py (8.0, 25.0, 10.0, 15.0) | LOW |

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
- `Scene/Diplom_scene/Assets/Scripts/LightController.cs` - `OnBackendResponseSuccess/Failed()`, `FallbackCheckRoutine()`
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
