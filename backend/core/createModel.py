from ultralytics import YOLO
from roboflow import Roboflow
import os

rf = Roboflow(api_key="8ruTrXQopT37OBoi3cEU")
project = rf.workspace("kugas-workspace").project("low_poly-cars")
dataset = project.version(1).download("yolov8")

# Автоматически находим путь к скачанному data.yaml
yaml_path = os.path.join(dataset.location, "data.yaml")

# 1. Загружаем базовую модель
model = YOLO('yolov8m.pt')

# 2. Дообучаем модель (теперь путь подставится сам!)
# Изменяем параметры тренировки, чтобы не перегружать оперативку
model.train(
    data=yaml_path,
    epochs=15,
    imgsz=640,
    batch=4,          # Уменьшаем размер батча (по умолчанию 16). Меньше батч — намного меньше жрет ОЗУ!
    workers=2,        # Жестко ограничиваем потоки загрузки данных до 2 (вместо авто-выбора всех 32 ядер)
    cache=False       # ОТКЛЮЧАЕМ кеширование картинок в оперативную память
)

# 3. Экспортируем обновленную модель в ONNX для Unity
model.export(format='onnx', imgsz=640)
print("[SUCCESS] Модель сконвертирована! Ищи файл 'yolov8m.onnx' в папке.")