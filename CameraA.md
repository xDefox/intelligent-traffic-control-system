
## Новая концепция: "Что видит камера = что есть на карте"

### Основные принципы

1. **Камера = источник истины** — она физически стоит на перекрёстке и смотрит в конкретном направлении
2. **Авто-определение направлений** — из rotation камеры в Unity
3. **Авто-определение связей** — если камера А смотрит в направлении камеры Б, они соединены дорогой
4. **Минимум конфига** — только ID перекрёстков и их позиции для визуализации

---

## Архитектура



### 2. Как это работает в Unity

#### Шаг 1: Камера регистрируется при старте

```csharp
// IntersectionVisionManager.cs
void Start()
{
    intersectionId = ExtractIntersectionIdFromName(gameObject.name);
    
    // Автоматически определяем направление камеры из её rotation
    foreach (var cam in xAxisCameras)
    {
        string direction = cam.GetWorldDirection();  // "E" или "W"
        RegisterCamera(cam, direction);
    }
    
    foreach (var cam in zAxisCameras)
    {
        string direction = cam.GetWorldDirection();  // "N" или "S"
        RegisterCamera(cam, direction);
    }
}
```

#### Шаг 2: Камера отправляет телеметрию с метаданными

```csharp
IEnumerator SendBatchTelemetry()
{
    var batch = new BatchTelemetryDTO
    {
        intersection_id = intersectionId,
        cameras = new List<CameraTelemetryDTO>()
    };
    
    for (int i = 0; i < allCameras.Count; i++)
    {
        var cam = allCameras[i];
        string direction = cam.GetWorldDirection();  // "E", "W", "N", "S"
        Vector3 worldPos = cam.GetWorldPosition();
        Vector3 worldRot = cam.GetWorldRotation();   // euler angles
        
        batch.cameras.Add(new CameraTelemetryDTO
        {
            camera_id = $"{intersectionId}_{direction}",  // INT_001_E
            direction = direction,                          // "E"
            world_position = {x: worldPos.x, y: worldPos.y, z: worldPos.z},
            world_rotation = {x: worldRot.x, y: worldRot.y, z: worldRot.z},
            lanes = new List<LaneDetectionDTO>
            {
                new LaneDetectionDTO
                {
                    lane_id = $"{intersectionId}_{direction}",
                    car_count = cameraResults[i],
                    avg_speed = 0f,
                    max_capacity = cam.maxZoneCapacity
                }
            }
        });
    }
    
    // Отправляем batch
    yield return StartCoroutine(SendToBackend(batch));
}
```

#### Шаг 3: Бэкенд автоматически строит граф


---

### 3. Определение направления из Unity

```csharp
// EdgeVisionCamera.cs
public string GetWorldDirection()
{
    // Получаем forward вектор камеры в мировых координатах
    Vector3 forward = transform.forward;
    
    // Определяем доминирующую ось
    if (Mathf.Abs(forward.x) > Mathf.Abs(forward.z))
    {
        // X-axis (восток-запад)
        return forward.x > 0 ? "E" : "W";
    }
    else
    {
        // Z-axis (север-юг)
        return forward.z > 0 ? "N" : "S";
    }
}

public Vector3 GetWorldPosition()
{
    return transform.position;
}

public Vector3 GetWorldRotation()
{
    return transform.eulerAngles;
}
```

**Результат:**
- Камера стоит на перекрёстке, смотрит на восток → `direction="E"`
- Камера стоит на перекрёстке, смотрит на запад → `direction="W"`
- Система сама понимает, что INT_001_E соединена с INT_002_W

---

### 4. Система проверки следующей дороги (ваш вопрос)

**Автоматическое определение следующей дороги:**

```python
# backend/services/next_road_predictor.py

class NextRoadPredictor:
    """
    Предсказывает, на какую дорогу поедет машина,
    анализируя траекторию и текущую позицию.
    """
    
    def __init__(self, graph: CityTrafficGraph):
        self.graph = graph
    
    def predict_next_road(self, 
                          intersection_id: str, 
                          current_approach: str,
                          car_trajectory: list[Vector3]) -> str:
        """
        Определить следующую дорогу по траектории машины.
        
        Args:
            intersection_id: "INT_001"
            current_approach: "E" (машина въезжает с востока)
            car_trajectory: [(x1,z1), (x2,z2), ...] — последние 10 позиций
        
        Returns:
            "INT_002_N" — следующая дорога
        """
        # Получаем все выходы из текущего подхода
        outgoing_edges = self.graph.out_edges(
            (intersection_id, current_approach),
            data=True
        )
        
        if not outgoing_edges:
            return None
        
        # Анализируем траекторию
        if len(car_trajectory) < 3:
            # Недостаточно данных — возвращаем самую загруженную дорогу
            return self._get_most_congested_outgoing(intersection_id, current_approach)
        
        # Вычисляем направление движения
        start_pos = car_trajectory[0]
        end_pos = car_trajectory[-1]
        movement_vector = end_pos - start_pos
        
        # Сравниваем с возможными выходами
        best_match = None
        best_score = -1
        
        for src, dst, edge_data in outgoing_edges:
            dst_inter, dst_approach = dst
            expected_direction = self._get_expected_direction(current_approach, dst_approach)
            
            # Насколько траектория совпадает с ожидаемым направлением
            score = self._cosine_similarity(movement_vector, expected_direction)
            
            if score > best_score:
                best_score = score
                best_match = dst
        
        return best_match if best_score > 0.7 else self._get_most_congested_outgoing(...)
    
    def _get_expected_direction(self, from_approach: str, to_approach: str) -> Vector3:
        """
        Ожидаемое направление движения от from_approach к to_approach.
        
        Примеры:
        - E → N: поворот налево (движение на север)
        - E → S: поворот направо (движение на юг)
        - E → W: прямо (движение на запад)
        """
        directions = {
            "N": Vector3(0, 0, 1),
            "S": Vector3(0, 0, -1),
            "E": Vector3(1, 0, 0),
            "W": Vector3(-1, 0, 0)
        }
        return directions.get(to_approach, Vector3.zero)
```

**В Unity:**

```csharp
// WaypointNavigator.cs
public List<Vector3> trajectoryHistory = new List<Vector3>();

void Update()
{
    // Записываем траекторию каждые 0.5 сек
    if (Time.time - lastTrajectoryRecord > 0.5f)
    {
        trajectoryHistory.Add(transform.position);
        if (trajectoryHistory.Count > 10)
            trajectoryHistory.RemoveAt(0);
    }
}

// При приближении к перекрёстку:
void OnTriggerEnter(Collider other)
{
    if (other.CompareTag("Intersection"))
    {
        string intersectionId = other.GetComponent<IntersectionManager>().intersectionId;
        string currentApproach = GetCurrentApproach();  // "E"
        
        // Отправляем траекторию на бэкенд
        StartCoroutine(AskNextRoad(intersectionId, currentApproach, trajectoryHistory));
    }
}
```

## Полный пример workflow

### 1. Запуск системы

```
Unity → Backend: "Привет, я INT_001, у меня 4 камеры:
  - cam_1: position=(105, 1, 0), rotation=(0, 90, 0) → direction=E
  - cam_2: position=(95, 1, 0), rotation=(0, -90, 0) → direction=W
  - cam_3: position=(100, 1, 5), rotation=(0, 0, 0) → direction=N
  - cam_4: position=(100, 1, -5), rotation=(0, 180, 0) → direction=S"

Backend: "Понял, INT_001 имеет 4 подхода: E, W, N, S"
```

### 2. Построение графа

```
Unity → Backend: "Привет, я INT_002, у меня 4 камеры:
  - cam_1: position=(55, 1, 0), rotation=(0, -90, 0) → direction=W
  - ..."

Backend: "Анализирую:
  - INT_001_E смотрит на +X (восток)
  - INT_002_W смотрит на -X (запад)
  - Они противоположны!
  - Расстояние: 50м < 200м
  - → Создаю связь: INT_001_E → INT_002_W
  - → Создаю связь: INT_002_W → INT_001_E"
```

### 3. Работа в реальном времени

```
Unity (INT_001_E): "3 машины, скорость 12.5 км/ч"
Backend: "Обновляю lane_INT_001_E: congestion=0.6"
Backend: "Каскадный анализ: INT_002_W будет загружена через 30 сек → продлеваю зелёный"
Backend → Unity (INT_002): "Зелёная волна: +5 сек на W"
---

## Миграция с текущей системы

### Шаг 1: Добавить поля в DTO

```python
# backend/models/traffic.py

class CameraTelemetryDTO(BaseModel):
    camera_id: str
    direction: str  # "N", "S", "E", "W"
    world_position: dict[str, float]  # {x, y, z}
    world_rotation: dict[str, float]  # {x, y, z} (euler angles)
    lanes: List[LaneDetectionDTO]
```

### Шаг 2: Обновить Unity

```csharp
// IntersectionVisionManager.cs
// Добавить в CameraTelemetryDTO:
public string direction;
public Vector3 world_position;
public Vector3 world_rotation;
```

### Шаг 3: Автоматическое определение направлений

```csharp
// EdgeVisionCamera.cs
public string GetWorldDirection()
{
    Vector3 forward = transform.forward;
    
    // Угол между forward и осями мира
    float angleX = Vector3.Angle(forward, Vector3.right);   // 0° = восток
    float angleZ = Vector3.Angle(forward, Vector3.forward); // 0° = север
    
    if (angleX < 45) return "E";
    if (angleX > 135) return "W";
    if (angleZ < 45) return "N";
    if (angleZ > 135) return "S";
    
    return "UNKNOWN";  // Камера под углом (можно добавить NE, SW, etc.)
}
```

### Шаг 4: Автоматическое построение графа

```python
# backend/services/graph_manager.py

def register_camera_telemetry(self, camera_data: dict):
    """
    Регистрирует камеру и автоматически обновляет граф.
    """
    camera_id = camera_data["camera_id"]
    intersection_id = camera_data["intersection_id"]
    direction = camera_data["direction"]
    
    # Сохраняем метаданные камеры
    self.camera_registry[camera_id] = {
        "intersection_id": intersection_id,
        "direction": direction,
        "position": camera_data["world_position"],
        "rotation": camera_data["world_rotation"]
    }
    
    # Перестраиваем граф (можно оптимизировать — перестраивать только при добавлении/удалении камер)
    self._build_from_telemetry()
```
## Итоговая архитектура

```
┌─────────────────────────────────────────────┐
│          UNITY (Edge Layer)                  │
│                                              │
│  Intersection_1 (GameObject)                 │
│  ├── Camera_E (смотрит на +X) → direction=E │
│  ├── Camera_W (смотрит на -X) → direction=W │
│  ├── Camera_N (смотрит на +Z) → direction=N │
│  └── Camera_S (смотрит на -Z) → direction=S │
│                                              │
│  [Автоматически отправляет в batch:]         │
│  - camera_id: INT_001_E                      │
│  - direction: E                              │
│  - world_position: (105, 1, 0)               │
│  - world_rotation: (0, 90, 0)                │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│       BACKEND (Fog Layer)                    │
│                                              │
│  1. Получает batch с метаданными камер       │
│  2. Автоматически определяет направления     │
│  3. Строит граф: INT_001_E ↔ INT_002_W       │
│  4. Определяет фазы: EW (E+W), NS (N+S)     │
│  5. Принимает решения на основе данных       │
└─────────────────────────────────────────────┘
```



Если хотите показать, что INT_3 и INT_4 — это ветвление от одного места:

```
INT_1, INT_2, INT_3A, INT_3B
```

Но это избыточно для 3-4 перекрёстков.

---

## Что делать с графом?

### Автоматическое определение топологии

Система сама поймёт, что у вас **4 перекрёстка**, а не 3:

```python
# Backend получает telemetry от Unity:

# INT_1: 4 камеры (E, W, N, S)
# INT_2: 4 камеры (E, W, N, S)
# INT_3: 4 камеры (E, W, N, S)
# INT_4: 4 камеры (E, W, N, S)

# Система автоматически строит граф:
# - INT_1_E ↔ INT_2_W (расстояние 50м)
# - INT_2_E ↔ INT_3_W (расстояние 50м)
# - INT_2_N ↔ INT_4_S (расстояние 50м)

# Получается граф:
#     INT_3
#      ↑
# INT_1 → INT_2 → INT_4
```


# План реализации камеро-центричной архитектуры

## Выбрано: `INT_1, INT_2, INT_3, INT_4` (простая последовательность)

## Что будет изменено:

### 1. **road_config.py** — упрощение до минимума
- Убрать строковые связи
- Убрать типы перекрёстков
- Оставить только ID и позиции (для UI)
- Добавить поддержку YAML

### 2. **backend/models/traffic.py** — новые поля в DTO
- Добавить `direction` (N/S/E/W)
- Добавить `world_position` ({x, y, z})
- Добавить `world_rotation` ({x, y, z})

### 3. **Unity: EdgeVisionCamera.cs** — авто-определение направлений
- Метод `GetWorldDirection()` — определяет направление из rotation
- Метод `GetWorldPosition()` — возвращает мировые координаты
- Отправка метаданных в batch telemetry

### 4. **Unity: IntersectionVisionManager.cs** — обновление batch
- Добавить direction, world_position, world_rotation в CameraTelemetryDTO
- Автоматическое определение lane_id из направления

### 5. **graph_manager.py** — автоматическое построение графа
- Реестр камер с метаданными
- Автоматическое определение связей по расстоянию и противоположным направлениям
- Автоматическое определение типов перекрёстков

### 6. **Миграция Unity сцены**
- Переименовать GameObjects: `intersection_3X` → `INT_3`, `intersection_3Z` → `INT_4`
- Обновить IntersectionVisionManager на новые ID

## Результат:

**Конфиг (road_network.yaml):**
```yaml
version: "1.0"
intersections:
  - id: "INT_1"
    position: {x: 100, z: 0}
  - id: "INT_2"
    position: {x: 50, z: 0}
  - id: "INT_3"
    position: {x: 0, z: 0}
  - id: "INT_4"
    position: {x: 50, z: 50}
```

**Граф строится автоматически из telemetry камер:**
```
    INT_3 (север)
      ↑
INT_1 → INT_2 → INT_4 (восток)
```

**Преимущества:**
- ✅ Не нужно прописывать направления в конфиге
- ✅ Не нужно прописывать связи — система сама их построит
- ✅ Минимум конфига — только ID и позиции для UI
- ✅ Автоматическая валидация

---

**Для реализации изменений нужно переключиться в ACT MODE.** После этого я выполню все 6 этапов последовательно (~2-3 часа работы).

## Анализ текущей архитектуры

### Что уже есть:
1. **road_config.py** - статическая конфигурация (3 перекрёстка, 4 линка)
2. **graph_manager.py** - NetworkX граф, строится из road_config, но динамически регистрирует подходы (approach) из телеметрии
3. **admin_panel.py** - вкладка "Граф" визуализирует топологию через `TrafficMap`
4. **WaypointNavigator.cs** - машины движутся по waypoint-графу, выбирая случайных соседей
5. **IntersectionVisionManager.cs** - отправляет batch-телеметрию с камер

### Проблемы:
- **road_config** - статичен, не масштабируется под реальную карту
- **Граф в admin_panel** - только визуализация, не используется для бизнес-логики
- **Нет ограничений транспорта** - машины не знают, где дороги переполнены

---

## Предлагаемый план: Camera-First Design

### Концепция:
Система сама строит карту дорог на основе данных камер (их позиции, направления, связи), а не из конфиг-файла.

### Этапы реализации:

#### 1. **Camera Registry Service** (backend)
- Регистрация камер с их позициями и направлениями
- Автоматическое построение топологии на основе геометрии
- API: `GET /api/v1/road-topology` - возвращает текущую топологию для Unity

#### 2. **Dynamic Road Network** (замена road_config)
- Убрать зависимость от `road_config.get_links()` в `graph_manager.py`
- Топология строится из зарегистрированных камер
- Связь камер определяется по:
  - Геометрической близости (raycast между камерами)
  - Или явной регистрации в Unity (камера знает, куда ведёт дорога)

#### 3. **Traffic Constraints API** (для ограничения транспорта)
- Новый эндпоинт: `GET /api/v1/congestion-map`
- Возвращает карту загруженности: `{ "lane_id": 0.8, ... }`
- Unity-машины запрашивают этот API и не выбирают переполненные пути

#### 4. **WaypointNode Enhancement** (Unity)
- Добавить компонент `RoadConstraintChecker` к WaypointNode
- При выборе соседа проверять congestion_map
- Если downstream-дорога >70% загружена - не выбирать этот путь

#### 5. **Удаление/архивирование графа в admin_panel**
- Вырезать вкладку "Граф" (как вы и хотели)
- Или перенести в отдельный view (для отладки)

---

### Вопросы для уточнения:

1. **Как камеры будут знать свои связи?**
   - Вариант A: Unity камера знает, куда ведёт дорога (next_camera_id)
   - Вариант B: Сервер определяет связи по геометрии позиций камер
   - Вариант C: Комбинация - камеры регистрируются с next_camera_id, сервер валидирует

2. **Где хранить позицию камеры?**
   - В Unity: позиция waypoint'а перед перекрёстком
   - В бэкенде: нужно добавить поле `position` в регистрацию камеры

3. **Нужно ли сохранять историю топологии?**
   - Или топология "живёт" только пока сервер работает?

4. **Как часто обновлять congestion_map в Unity?**
   - Каждые 1-2 секунды? Или по WebSocket?

Пожалуйста, уточните эти моменты, чтобы я мог предложить более детальный план.