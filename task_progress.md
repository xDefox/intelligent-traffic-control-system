# Как запустить и проверить

## 1. Запуск сервера (Python)

В PyCharm Terminal (или cmd):
```bash
cd D:\Education\Diplom
.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8050 --reload
```
Должен увидеть:
```
[CityTrafficGraph] Построен: ... узлов, ... связей, 8 полос
[CloudOrchestrator] Запущен (тикер раз в 1с)
```

## 2. Запуск админ-панели (Flet UI)

Отдельный терминал:
```bash
cd D:\Education\Diplom
.venv\Scripts\python -m flet run backend/UI/admin_panel.py
```
Или просто:
```bash
.venv\Scripts\python backend/UI/admin_panel.py
```

## 3. Запуск Unity сцены

Открыть `Scene/Diplom_scene/Scenes/SampleScene.unity` и нажать Play.

## 4. Что проверить

### ✅ Сервер
- В логах должны быть сообщения:
  - `[CityTrafficGraph] Построен: 8 узлов...`
  - `🧠 Создан Fog-мозг для intersection_1`
  - `🧠 [intersection_1] Фаза: NS/EW (прошло: Xс, ...)`

### ✅ Unity → Сервер
- В консоли Unity не должно быть ошибок `[JSON Parse Error]`
- В логах Python должны появляться сообщения вида:
  ```
  🧠 [intersection_1] Фаза: NS (прошло: 3с, NS: 50%, EW: 20%)
  ```

### ✅ Сервер → Unity (светофоры)
- Светофоры в Unity должны переключаться между NS и EW (зелёные полосы)
- В консоли Unity лог: `[IntersectionManager] Переключено на внешнее управление ИИ`

### ✅ Flet UI
- Окно должно показывать перекрёстки и lane_id в новом формате (`intersection_1_approach_0`)
- Фильтр по перекрёсткам должен работать

### ✅ Каскад (упреждение)
- Заполни машинами подход, который соединён с другим перекрёстком
- Если congestion > 70%, в логах появится:
  ```
  ☁️ [Cloud] Команда: intersection_1 -> REDUCE_GREEN
  ⏱️  intersection_1 -> каскад: зелёный урезан до Xс
  ```

## 5. Дальнейшие шаги (после проверки)

1. **Настроить max_capacity** в `EdgeVisionCamera.cs` — в инспекторе Unity для каждой камеры выставить `maxZoneCapacity` равным реальному числу машин, которое помещается в кадре (сейчас 4-5)

2. **Добавить третий перекрёсток** — копия префаба в Unity + строка в `road_config.py`

3. **Сделать, чтобы Unity отображала фазу NS/EW** — сейчас `LightController.ReceiveCommandFromPython()` получает "NS" или "EW", а не "Z_GREEN"/"X_GREEN". Нужно обновить маппинг фаз в Unity

4. **CV-модуль** — дообучить модель YOLO на low_poly машинках (уже есть датасет)