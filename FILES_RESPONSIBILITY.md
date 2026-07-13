# Ответственность файлов (простая схема)

## Backend (Python)

### 1. `backend/main.py`
**Задача**: API сервер
- Принимает POST запросы с данными камер (`/api/v1/telemetry`)
- Возвращает решение (GREEN/RED + длительность)
- WebSocket для UI мониторинга

### 2. `backend/services/traffic_brain.py`
**Задача**: Мозг для КАЖДОГО светофора
- Создаёт отдельный `AdaptiveTrafficBrain` для каждого `lane_id`
- Принимает решение: GREEN или RED
- Вычисляет длительность: `5 + (car_count/max_capacity * 25)` секунд
- Пример: 5 машин из 10 = 17.5 секунд зелёного

### 3. `backend/services/orchestrator.py`
**Задача**: Координатор
- Получает данные от Unity
- Создаёт/обновляет мозги для каждого светофора
- Возвращает решение + длительность

### 4. `backend/models/traffic.py`
**Задача**: Структуры данных
- DTO для запросов и ответов

---

## Unity (C#)

### 1. `IntersectionVisionManager.cs`
**Задача**: Сбор данных с камер и отправка в backend
- **Принимает**: Ссылки на камеры (xAxisCameras, zAxisCameras)
- **Делает**: 
  - Каждые 0.2с запускает YOLO на каждой камере
  - Отправляет ОТДЕЛЬНЫЙ запрос для каждой камеры
  - Получает ответ и передаёт в IntersectionManager
- **НЕ УПРАВЛЯЕТ** светофорами напрямую!

```csharp
// Отправляет:
POST /api/v1/telemetry
{
  "intersection_id": "intersection_1",
  "camera_id": "intersection_1_approach_0",
  "lanes": [{"lane_id": "intersection_1_approach_0", "car_count": 5}]
}

// Получает:
{"target_phase": "GREEN", "green_duration": 17.5}

// Передаёт в IntersectionManager:
intersectionController.ReceiveCommandForLane(
    "intersection_1_approach_0", 
    "GREEN", 
    17.5f
)
```

### 2. `LightController.cs` (IntersectionManager)
**Задача**: Управление всеми светофорами на перекрёстке
- **Принимает**: Ссылки на все светофоры (xAxisLights, zAxisLights)
- **Принимает команды** от IntersectionVisionManager
- **Находит нужный светофор** по laneId
- **Управляет** через TrafficLightViewer.SwitchToColor()

```csharp
// Получает команду:
ReceiveCommandForLane("intersection_1_approach_0", "GREEN", 17.5f)

// Находит светофор:
TrafficLightViewer light = GetLightForLane("intersection_1_approach_0")
// → возвращает xAxisLights[0]

// Управляет:
light.SwitchToColor(YELLOW)  // жёлтый 2с
light.SwitchToColor(GREEN)   // зелёный 17.5с
light.SwitchToColor(RED)     // красный
```

### 3. `TrafficLightViewer.cs` (уже существующий)
**Задача**: Визуализация ОДНОГО светофора
- Управляет цветами сфер (RED/YELLOW/GREEN)
- Метод `SwitchToColor(color)` меняет цвет

**НЕ НУЖНО** создавать новый скрипт для управления светофором!

---

## Иерархия GameObject

```
Intersection_1 (Empty)
│
├── IntersectionManager (LightController.cs)
│   ├── X Axis Lights: [TrafficLight_0, TrafficLight_1]
│   └── Z Axis Lights: [TrafficLight_2, TrafficLight_3]
│
├── IntersectionVisionManager
│   ├── Intersection Controller: [IntersectionManager]
│   ├── X Axis Cameras: [Camera_0, Camera_1]
│   └── Z Axis Cameras: [Camera_2, Camera_3]
│
├── TrafficLight_0
│   ├── TrafficLightViewer (управляет цветами)
│   └── Light (включает/выключает)
│
├── TrafficLight_1
│   ├── TrafficLightViewer
│   └── Light
│
├── TrafficLight_2
│   ├── TrafficLightViewer
│   └── Light
│
└── TrafficLight_3
    ├── TrafficLightViewer
    └── Light
```

---

## Поток данных (просто!)

```
Camera_0 → IntersectionVisionManager → Backend
         ← IntersectionVisionManager ← Backend
         → IntersectionManager.ReceiveCommandForLane()
         → TrafficLight_0.TrafficLightViewer.SwitchToColor()
         → 🌟 СВЕТОФОР МЕНЯЕТ ЦВЕТ
```

---

## Кто за что отвечает (кратко)

| Файл | Роль | Управляет светофорами? |
|------|------|----------------------|
| IntersectionVisionManager | Сбор данных с камер, отправка в backend | ❌ НЕТ |
| IntersectionManager | Получение команд, управление светофорами | ✅ ДА |
| TrafficLightViewer | Визуализация цветов | ✅ ДА (выполняет) |
| TrafficLightController | ❌ УДАЛЕН (был лишним) | - |

---

## Настройка Unity

### На Intersection_1:
1. **IntersectionManager** (LightController.cs)
   - X Axis Lights: TrafficLight_0, TrafficLight_1
   - Z Axis Lights: TrafficLight_2, TrafficLight_3
   - Use Autonomous Cycle: **false**

2. **IntersectionVisionManager**
   - Intersection Controller: [IntersectionManager]
   - X Axis Cameras: Camera_0, Camera_1
   - Z Axis Cameras: Camera_2, Camera_3

### На TrafficLight_0, 1, 2, 3:
- **TrafficLightViewer** (уже есть)
- **Light** (уже есть)
- ❌ TrafficLightController НЕ НУЖЕН!

---

## Как работает алгоритм

1. **Camera_0** видит 5 машин
2. **IntersectionVisionManager** отправляет в backend: `car_count=5`
3. **Backend** вычисляет: `5 + (5/10 * 25) = 17.5` секунд
4. **Backend** возвращает: `{"target_phase": "GREEN", "green_duration": 17.5}`
5. **IntersectionVisionManager** вызывает: 
   ```csharp
   intersectionManager.ReceiveCommandForLane(
       "intersection_1_approach_0", 
       "GREEN", 
       17.5f
   )
   ```
6. **IntersectionManager** находит TrafficLight_0 и:
   ```csharp
   light.SwitchToColor(YELLOW)  // 2 секунды
   light.SwitchToColor(GREEN)   // 17.5 секунд
   light.SwitchToColor(RED)     // автоматически
   ```
7. **TrafficLightViewer** меняет цвета сфер ✓

---

## Итог

**Только 3 файла на Unity:**
1. `IntersectionVisionManager` - камеры → backend
2. `IntersectionManager` - backend → светофоры
3. `TrafficLightViewer` - визуализация цветов

**TrafficLightController удалён** - он был лишним!