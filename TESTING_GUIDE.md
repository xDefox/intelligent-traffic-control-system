# Руководство по тестированию системы

## 🎯 Что было исправлено

### 1. **Независимость перекрёстков** ✅
- Каждый перекрёсток теперь работает независимо
- У каждого своё состояние фазы
- Перекрёстки не синхронизируются друг с другом

### 2. **Автоматическая конфигурация** ✅
- ID перекрёстка автоматически определяется из имени GameObject
- Поддерживаются: `IntersectionManager`, `IntersectionManager2`, `intersection_1`, etc.
- Не нужно вручную задавать intersectionId

### 3. **Индексация камер** ✅
- X-камеры → approach_0, approach_1
- Z-камеры → approach_2, approach_3
- Работает с любым количеством камер (1X+1Z, 2X+2Z, 2X+1Z, etc.)

### 4. **Переключение фаз** ✅
- При получении GREEN для оси, противоположная выключается
- Нет ситуации когда обе оси зелёные одновременно
- Правильная логика продления зелёного

## 🧪 Процедура тестирования

### Шаг 1: Запуск Python сервера

```bash
cd D:\Education\Diplom
.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8050 --reload
```

**Ожидаемый вывод:**
```
[CityTrafficGraph] Построен: ... узлов, ... связей
[CloudOrchestrator] Запущен (тикер раз в 1с)
INFO:     Application startup complete.
```

### Шаг 2: Запуск Unity

1. Откройте `Scene/Diplom_scene/Scenes/SampleScene.unity`
2. Нажмите Play
3. **Не трогайте ничего 10-15 секунд** - дайте системе стабилизироваться

### Шаг 3: Проверка Unity Console

**Ожидаемые логи (для каждого перекрёстка):**
```
[IntersectionManager] 🚦 IntersectionVisionManager запущен: ID=intersection_1, Камер X=1, Z=1, Всего=2
[IntersectionManager] ✅ Запуск inference loop: 2 камер, ID=intersection_1
[IntersectionManager] Переключено на внешнее управление ИИ (FastAPI).
```

**Если видите ошибку:**
```
❌ ОШИБКА: Не все камеры назначены!
```
→ Добавьте недостающие камеры в Inspector

### Шаг 4: Проверка Python Console

**Ожидаемые логи:**
```
📥 [intersection_1] Получен batch: 2 камер
  🔎 [intersection_1] intersection_1_approach_0 (approach approach_0) → фаза EW, машин: 0
  🔎 [intersection_1] intersection_1_approach_2 (approach approach_2) → фаза NS, машин: 0
  📊 [intersection_1] Машины по фазам: {'NS': 0, 'EW': 0}
  🚦 [intersection_1] Начальная фаза: NS {'NS': 0, 'EW': 0}

📥 [intersection_2] Получен batch: 3 камер
  🔎 [intersection_2] intersection_2_approach_0 (approach approach_0) → фаза EW, машин: 1
  🔎 [intersection_2] intersection_2_approach_1 (approach approach_1) → фаза EW, машин: 0
  🔎 [intersection_2] intersection_2_approach_2 (approach approach_2) → фаза NS, машин: 0
  📊 [intersection_2] Машины по фазам: {'NS': 0, 'EW': 1}
  🚦 [intersection_2] Начальная фаза: EW {'NS': 0, 'EW': 1}
```

### Шаг 5: Наблюдение за переключениями

**Через 30-60 секунд** должны появиться логи:
```
📥 [intersection_1] Получен batch: 2 камер
  🔎 [intersection_1] intersection_1_approach_0 (approach approach_0) → фаза EW, машин: 3
  🔎 [intersection_1] intersection_1_approach_2 (approach approach_2) → фаза NS, машин: 5
  📊 [intersection_1] Машины по фазам: {'NS': 5, 'EW': 3}
  🔍 [intersection_1] Текущая: NS (машин: 5), Противоположная: EW (машин: 3), Прошло: 8.5с, Мин: 8.0с
  📤 [intersection_1] Ответ: NS на 15.0с для 1 зелёных
```

Или переключение:
```
🔄 [intersection_1] NS→EW (на EW есть 3 машин)
📤 [intersection_1] Ответ: EW на 12.0с для 1 зелёных
```

### Шаг 6: Проверка в Unity

**В Unity Console должны быть:**
```
[IntersectionManager] 📥 Команда для intersection_1_approach_2: GREEN (ось Z, duration=15с)
[IntersectionManager] Z-axis зелёный на 15.0с
```

**В сцене Unity:**
- Светофоры Z-оси должны гореть зелёным
- Светофоры X-оси должны гореть красным
- Машины с направления -Z должны проезжать

## ✅ Критерии успешного тестирования

### Обязательные
- [ ] Оба перекрёстка отправляют batch-телеметрию
- [ ] Каждый перекрёсток имеет правильные approach_0, approach_2 (или больше)
- [ ] Фазы переключаются independently (не синхронно)
- [ ] Z-камера детектирует машины
- [ ] Когда на Z есть машины, включается зелёный для Z-оси
- [ ] Когда на X есть машины, включается зелёный для X-оси
- [ ] Обе оси не горят зелёным одновременно

### Желательные
- [ ] Переключения происходят после минимальной длительности (8с)
- [ ] При отсутствии машин на обеих осях происходит переключение
- [ ] При превышении 30с фаза принудительно переключается
- [ ] Логи показывают понятные причины переключений

## 🐛 Если что-то не работает

### Проблема: Оба перекрёстка синхронно меняют фазы
**Причина:** Используется глобальное состояние (должно быть исправлено)
**Решение:** Проверьте что используется `self._intersection_phase_states` в orchestrator.py

### Проблема: Z-ось не получает зелёный
**Причина 1:** Нет Z-камер в Unity Inspector
**Решение:** Добавьте камеры в `zAxisCameras` список

**Причина 2:** Z-камера не детектирует машины
**Решение:** 
- Проверьте что YOLO модель загружена
- Проверьте позицию камеры (должна смотреть на дорогу)
- Включите `enableDebugLogs = true` для просмотра детекций

**Причина 3:** Неправильная индексация
**Решение:** В логах должна быть строка `Камера 1 (Z-ось) → intersection_1_approach_2`

### Проблема: Обе оси зелёные одновременно
**Причина:** Баг в LightController.cs (должен быть исправлен)
**Решение:** Проверьте что используется исправленная версия `SetGreenWithRenewal()`

## 📊 Мониторинг

### Админ-панель
```bash
.venv\Scripts\python -m flet run backend/UI/admin_panel.py
```

### API статуса
```bash
curl http://127.0.0.1:8050/api/v1/state
```

## 🎓 Референсы

Статьи про умные светофоры:
- https://dunejournal.com/article/v-abu-dabi-zapustili-novuyu-sistemu-umnyh-svetoforov-ramp-metering
- https://www.zigwheels.ae/car-news/abu-dhabi-testing-smart-traffic-signals

Наши алгоритмы реализуют:
- ✅ Адаптивное управление фазами
- ✅ Детекцию потока машин
- ✅ Каскадное управление между перекрёстками
- ⏳ Ramp Metering (требует доработки)
- ⏳ Приоритет для общественного транспорта (требует доработки)