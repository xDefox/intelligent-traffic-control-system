# Исправление архитектуры светофоров (Dubai-style)

## Проблема
Изначальная архитектура имела централизованное управление:
- `IntersectionVisionManager` собирал данные со ВСЕХ камер и отправлял ОДИН запрос
- Backend создавал ОДИН мозг на перекрёсток
- Все светофоры работали по фазам (NS/EW), ожидая своей очереди
- Светофоры не могли работать независимо
- **Нет адаптивной длительности** - все фазы горели фиксированное время

## Решение (Dubai-style архитектура)
Каждый светофор имеет **независимый контроллер**, который:
1. Получает данные только от СВОЕЙ камеры
2. Принимает решение только для СЕБЯ
3. Не ждёт других светофоров или фаз
4. Работает автономно на основе загруженности
5. **Вычисляет динамическую длительность** зелёного на основе количества машин

## Изменения в коде

### 1. Unity (C#) - `IntersectionVisionManager.cs`
**Было:**
- Собирал данные со всех камер в один список
- Отправлял один комбинированный запрос
- Получал одну фазу для всего перекрёстка

**Стало:**
- Каждая камера отправляет данные ОТДЕЛЬНО
- В каждом запросе только одна полоса (lane)
- Получает команду + длительность для конкретного светофора
- Отправляет команду в `IntersectionManager.ReceiveCommandForLane(laneId, command, duration)`

### 2. Unity (C#) - `TrafficLightController.cs` (НОВЫЙ ФАЙЛ)
**Создан новый компонент** для управления отдельным светофором:
```csharp
public void ReceiveCommandFromPython(string command, float duration = 0f)
```

Логика:
- Принимает команду (GREEN/RED) и длительность
- Переходит через жёлтый: RED → YELLOW → GREEN
- Горит зелёным указанное время (динамическое)
- Автоматически возвращается в RED
- **Нет статического цикла** - полностью управляется backend'ом

### 3. Unity (C#) - `LightController.cs` (IntersectionManager)
**Было:**
```csharp
public void ReceiveCommandFromPython(string command) // Управление фазами
```

**Стало:**
```csharp
public void ReceiveCommandForLane(string laneId, string command, float greenDuration = 0f)
```

Новый метод:
- Принимает `laneId` для определения какого светофора управлять
- Получает `TrafficLightController` для конкретного светофора
- Передаёт команду и длительность в контроллер
- Не затрагивает другие светофоры на перекрёстке

### 4. Backend (Python) - `orchestrator.py`
**Было:**
- Один `AdaptiveTrafficBrain` на перекрёсток
- Управление фазами (NS/EW)

**Стало:**
- Отдельный `AdaptiveTrafficBrain` для КАЖДОГО светофора (lane_id)
- Ключ в словаре: `camera_id` (например "intersection_1_approach_0")
- Вызывает `process_lane_telemetry()` который возвращает (команда, длительность)
- Возвращает `green_duration` в ответе API

### 5. Backend (Python) - `traffic_brain.py`
**Добавлен новый режим с адаптивной длительностью:**
```python
def process_lane_telemetry(self, update: IntersectionUpdateDTO) -> tuple:
    """
    Вернуть (команда, длительность_зелёного)
    
    Логика:
    - car_count > 0 → GREEN
    - Длительность = 5 + (загруженность * 25) секунд
    - Загруженность = car_count / max_capacity
    - Итог: 5-30 секунд зелёного
    """
```

Пример:
- 1 машина из 10 (10%) → 5 + 2.5 = 7.5 сек
- 5 машин из 10 (50%) → 5 + 12.5 = 17.5 сек
- 10 машин из 10 (100%) → 5 + 25 = 30 сек

### 6. Backend (Python) - `models/traffic.py`
Добавлен `BackendResponseDTO`:
```python
class BackendResponseDTO(BaseModel):
    target_phase: str      # "GREEN" или "RED"
    green_duration: float  # Длительность зелёного (0 = авто)
    confidence: float      # Уверенность решения
```

### 7. Backend (Python) - `main.py`
Обновлён endpoint `/api/v1/telemetry`:
```python
return {
    "status": "processed",
    "target_phase": result["target_phase"],
    "green_duration": result.get("green_duration", 0.0),
    "cascade_applied": result["cascade_applied"],
}
```

## Как это работает

### Поток данных:
```
Camera 0 → Backend (lane_0, car_count=5) 
         → Backend вычисляет: GREEN на 17.5 сек
         → TrafficLight 0: YELLOW → GREEN (17.5с) → RED

Camera 1 → Backend (lane_1, car_count=0) 
         → Backend вычисляет: RED
         → TrafficLight 1: RED
```

### Алгоритм принятия решений:
```
1. Backend получает car_count от камеры
2. Если car_count > 0:
   - Вычисляет загруженность = car_count / max_capacity
   - Вычисляет длительность = 5 + (загруженность * 25) секунд
   - Отправляет: GREEN + длительность
3. Если car_count == 0:
   - Отправляет: RED
4. Unity получает команду:
   - RED → YELLOW → GREEN (на указанное время) → RED
```

### Пример работы:
1. Подъехало 8 машин к светофору 0 (max_capacity=10)
2. Camera 0 отправляет `car_count=8`
3. Backend:
   - Загруженность = 8/10 = 80%
   - Длительность = 5 + (0.8 * 25) = 25 секунд
   - Решение: `GREEN` на 25 сек
4. Backend возвращает `{"target_phase": "GREEN", "green_duration": 25.0}`
5. Unity получает команду:
   - Светофор 0: RED → YELLOW (2с) → GREEN (25с) → RED
   - **Остальные светофоры не затрагиваются!**
6. Через 25 сек светофор автоматически становится красным
7. Backend продолжает получать данные и принимать новые решения

## Преимущества Dubai-style архитектуры

1. **Независимость**: Каждый светофор работает самостоятельно
2. **Адаптивность**: Длительность зелёного зависит от загруженности (5-30 сек)
3. **Масштабируемость**: Легко добавить новые светофоры
4. **Отказоустойчивость**: Если один светофор сломался, остальные работают
5. **Эффективность**: Нет простоя, каждый светофор работает только когда нужно
6. **Реалистичность**: Как в Дубае, где светофоры адаптируются к потоку individually

## Запуск

### Unity Setup:
1. На каждом `TrafficLightViewer` должен быть компонент `TrafficLightController` (создан)
2. `IntersectionVisionManager` должен иметь ссылки на камеры в `xAxisCameras` и `zAxisCameras`
3. `IntersectionVisionManager` должен иметь ссылку на `IntersectionManager` в `intersectionController`
4. `IntersectionManager` должен иметь ссылки на светофоры в `xAxisLights` и `zAxisLights`

### Backend:
```bash
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8050 --reload
```

## Мониторинг

### Backend логи:
```
🧠 Создан независимый контроллер для intersection_1_approach_0
  🟢 [intersection_1_approach_0] Машины: 8/10 (80%) → GREEN на 25.0с
  🔴 [intersection_1_approach_0] Пусто → RED
```

### Unity логи:
```
[IntersectionManager] Переключено на внешнее управление ИИ (FastAPI).
[TrafficLightController] TrafficLight_0 переключен на внешнее управление
```

### API Response:
```json
{
  "status": "processed",
  "target_phase": "GREEN",
  "green_duration": 25.0,
  "cascade_applied": false
}
```

## Алгоритм расчёта длительности

```python
# Загруженность полосы (0.0 - 1.0)
congestion_ratio = car_count / max_capacity

# Длительность зелёного (5-30 секунд)
green_duration = 5.0 + (congestion_ratio * 25.0)
green_duration = clamp(green_duration, 5.0, 30.0)

# Примеры:
# 0 машин → RED (не используется)
# 1 машина (10%) → 7.5 сек
# 5 машин (50%) → 17.5 сек
# 10 машин (100%) → 30 сек
```

## Отладка

### Проверка что камеры отправляют данные:
```bash
# Backend логи должны показывать:
🧠 Создан независимый контроллер для intersection_1_approach_0
  🟢 [intersection_1_approach_0] Машины: 5/10 (50%) → GREEN на 17.5с
```

### Проверка что светофоры получают команды:
```bash
# Unity Console должен показывать:
[IntersectionManager] Переключено на внешнее управление ИИ (FastAPI).
[TrafficLightController] TrafficLight_0 переключен на внешнее управление
```

### Проверка длительности:
```bash
# В ответе API должно быть:
{"target_phase": "GREEN", "green_duration": 17.5}
```

## Миграция с legacy архитектуры

Если у вас уже настроен legacy режим (фазы NS/EW):
1. Backend автоматически определит режим по флагу `is_per_lane`
2. Legacy режим сохраняется для совместимости
3. Для перехода на Dubai-style:
   - На каждом светофоре добавьте компонент `TrafficLightController`
   - Уберите конфигурацию фаз из `road_config.py`
   - Или установите `is_per_lane=True` при создании `AdaptiveTrafficBrain`

## Ключевые особенности реализации

1. **Нет статических таймеров** - всё управляется backend'ом
2. **Динамическая длительность** - 5-30 сек на основе загруженности
3. **Независимые светофоры** - каждый работает сам по себе
4. **Минимальный зелёный** - 5 секунд всегда
5. **Максимальный зелёный** - 30 секунд
6. **Автоматический возврат в RED** - после зелёного светофор сам становится красным
7. **Каскадное управление** - Cloud может влиять на длительность через команды