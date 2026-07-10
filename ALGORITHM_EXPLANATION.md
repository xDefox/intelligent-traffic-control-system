# Алгоритм работы системы светофоров

## Общая схема

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  Camera 0   │      │  Camera 1   │      │  Camera 2   │
│ (lane_0)    │      │ (lane_1)    │      │ (lane_2)    │
└──────┬──────┘      └──────┬──────┘      └──────┬──────┘
       │                    │                    │
       │  car_count=5      │  car_count=0      │  car_count=8
       │  max_cap=10       │  max_cap=10       │  max_cap=10
       │                    │                    │
       └────────────────────┼────────────────────┘
                            │
                    ┌───────▼────────┐
                    │   Backend      │
                    │                 │
                    │  Lane 0 brain:  │  → GREEN, 17.5 сек
                    │  Lane 1 brain:  │  → RED
                    │  Lane 2 brain:  │  → GREEN, 25.0 сек
                    └───────┬────────┘
                            │
            ┌───────────────┼───────────────┐
            │               │               │
    ┌───────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐
    │ TrafficLight │ │ TrafficLight│ │ TrafficLight│
    │     0        │ │     1       │ │     2       │
    │  GREEN 17.5с │ │    RED      │ │ GREEN 25.0с │
    └──────────────┘ └─────────────┘ └─────────────┘
```

## Backend Algorithm (traffic_brain.py)

### Входные данные:
```python
update = IntersectionUpdateDTO(
    intersection_id="intersection_1",
    camera_id="intersection_1_approach_0",  # Уникальный ID камеры
    lanes=[
        LaneDetectionDTO(
            lane_id="intersection_1_approach_0",
            car_count=5,           # Машин обнаружено
            avg_speed=0.0,
            max_capacity=10        # Вместимость полосы
        )
    ]
)
```

### Обработка:

```python
def process_lane_telemetry(self, update: IntersectionUpdateDTO) -> tuple:
    # 1. Получаем данные
    car_count = 5
    max_capacity = 10
    
    # 2. Проверяем минимальное время (защита от частого переключения)
    elapsed = time.time() - self._green_start_time
    if self._current_command == "GREEN" and elapsed < 5.0:
        return "GREEN", 0.0  # Продолжаем зелёный
    
    # 3. Принимаем решение
    if car_count > 0:
        # ЕСТЬ МАШИНЫ → GREEN
        self._current_command = "GREEN"
        
        # 4. ВЫЧИСЛЯЕМ ДЛИТЕЛЬНОСТЬ
        congestion_ratio = car_count / max_capacity  # 5/10 = 0.5 (50%)
        green_duration = 5.0 + (congestion_ratio * 25.0)  # 5 + 12.5 = 17.5 сек
        green_duration = min(30.0, max(5.0, green_duration))  # Ограничения 5-30 сек
        
        return "GREEN", 17.5
    else:
        # НЕТ МАШИН → RED
        self._current_command = "RED"
        return "RED", 0.0
```

### Выход:
```python
return {
    "target_phase": "GREEN",      # Команда
    "green_duration": 17.5,        # Длительность в секундах
    "cascade_applied": False
}
```

## Unity Execution (TrafficLightController.cs)

### Получение команды:
```csharp
public void ReceiveCommandFromPython(string command, float duration = 0f)
{
    // Останавливаем предыдущую команду
    if (currentCommandCoroutine != null)
        StopCoroutine(currentCommandCoroutine);
    
    // Запускаем новую
    currentCommandCoroutine = StartCoroutine(ExecuteCommand(command, duration));
}
```

### Выполнение:
```csharp
private IEnumerator ExecuteCommand(string command, float duration)
{
    switch (command)
    {
        case "GREEN":
            // 1. Жёлтый (2 сек)
            SetLightColor(Color.yellow);
            yield return new WaitForSeconds(2f);
            
            // 2. Зелёный (динамическая длительность)
            float greenTime = duration > 0 ? duration : 5f;  // 17.5 сек
            SetLightColor(Color.green);
            yield return new WaitForSeconds(greenTime);
            
            // 3. Автоматически в RED
            SetLightColor(Color.red);
            break;
            
        case "RED":
            SetLightColor(Color.red);
            break;
    }
}
```

## Примеры работы

### Сценарий 1: Лёгкий поток
```
Camera 0: car_count=1, max_capacity=10
Backend:  congestion = 1/10 = 10%
          duration = 5 + (0.1 * 25) = 7.5 сек
Response: {"target_phase": "GREEN", "green_duration": 7.5}

Unity:    RED → YELLOW(2с) → GREEN(7.5с) → RED
```

### Сценарий 2: Средний поток
```
Camera 0: car_count=5, max_capacity=10
Backend:  congestion = 5/10 = 50%
          duration = 5 + (0.5 * 25) = 17.5 сек
Response: {"target_phase": "GREEN", "green_duration": 17.5}

Unity:    RED → YELLOW(2с) → GREEN(17.5с) → RED
```

### Сценарий 3: Пробка
```
Camera 0: car_count=10, max_capacity=10
Backend:  congestion = 10/10 = 100%
          duration = 5 + (1.0 * 25) = 30 сек (max)
Response: {"target_phase": "GREEN", "green_duration": 30.0}

Unity:    RED → YELLOW(2с) → GREEN(30с) → RED
```

### Сценарий 4: Пусто
```
Camera 0: car_count=0, max_capacity=10
Backend:  car_count == 0 → RED
Response: {"target_phase": "RED", "green_duration": 0.0}

Unity:    RED (остаётся красным)
```

## Временная диаграмма

```
Time    │ 0s   2s   4s   6s   8s   10s  12s  14s  16s  18s  20s
────────┼─────────────────────────────────────────────────────────
Cam 0   │ [5 машин обнаружено]
        │
Backend │ ┌──────────────┐
        │ │ Обработка    │ → GREEN, 17.5с
        │ └──────────────┘
        │
Light 0 │    ┌─YELLOW─┐┌──────GREEN 17.5с──────┐┌──RED──┐
        │    │   2с   ││                      ││       │
────────┼────┴────────┴──────────────────────┴┴───────┴───────
Cam 1   │ [0 машин]
        │
Backend │ ┌──────────────┐
        │ │ Обработка    │ → RED
        │ └──────────────┘
        │
Light 1 │ ──────── RED (без изменений) ───────────────
────────┼──────────────────────────────────────────────────
```

## Ключевые особенности

### 1. **Независимость**
Каждый светофор работает сам по себе:
- Lane 0 может быть GREEN, пока Lane 1 is RED
- Нет фаз NS/EW, нет синхронизации

### 2. **Динамическая длительность**
Длительность зелёного вычисляется на основе:
- Количества машин (car_count)
- Вместимости полосы (max_capacity)
- Формула: `5 + (car_count/max_capacity * 25)` секунд

### 3. **Защита от частого переключения**
- Минимальное время зелёного: 5 секунд
- Если только что включили GREEN, ждём минимум 5 сек перед следующим решением

### 4. **Автоматический возврат в RED**
- После зелёного светофор автоматически становится красным
- Ждёт следующей команды от backend

### 5. **Асинхронность**
- Каждая камера отправляет данные независимо (каждые 0.2 сек)
- Backend обрабатывает каждую independently
- Светофоры переключаются независимо

## Отладка

### Backend логи (что должно быть):
```
🧠 Создан независимый контроллер для intersection_1_approach_0
  🟢 [intersection_1_approach_0] Машины: 5/10 (50%) → GREEN на 17.5с
  🔴 [intersection_1_approach_0] Пусто → RED
```

### Unity логи (что должно быть):
```
[IntersectionManager] Переключено на внешнее управление ИИ (FastAPI).
[TrafficLightController] TrafficLight_0 переключен на внешнее управление
```

### Network requests:
```
POST /api/v1/telemetry
{
  "intersection_id": "intersection_1",
  "camera_id": "intersection_1_approach_0",
  "lanes": [{"lane_id": "intersection_1_approach_0", "car_count": 5, ...}]
}

Response:
{
  "status": "processed",
  "target_phase": "GREEN",
  "green_duration": 17.5,
  "cascade_applied": false
}
```

## Сравнение: Было vs Стало

| Параметр | Было (Legacy) | Стало (Dubai-style) |
|-----------|---------------|---------------------|
| Управление | По фазам (NS/EW) | По светофорам (GREEN/RED) |
| Зависимость | Все светофоры синхронизированы | Каждый независим |
| Длительность | Фиксированная (12с/8с) | Динамическая (5-30с) |
| Данные | Все камеры вместе | Каждая камера отдельно |
| Мозг | Один на перекрёсток | Один на светофор |
| Решение | "Какая фаза сейчас" | "Что делать этому светофору" |
| Адаптивность | Низкая | Высокая |

## Почему это работает как в Дубае

В Дубае (и других умных городах) светофоры работают независимо:
1. **Адаптивное управление**: Каждый светофор реагирует только на свой поток
2. **Динамическое время**: Зелёный горит ровно столько, сколько нужно
3. **Нет ожидания**: Если на одной оси нет машин, она не блокирует другую
4. **Эффективность**: Минимум простоя, максимум пропускной способности

Наша реализация повторяет эту логику:
- Каждый светофор = независимый агент
- Backend = мозг агента
- Данные с камеры = органы чувств
- Динамическая длительность = адаптивность