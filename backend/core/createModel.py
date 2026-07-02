from ultralytics import YOLO

# Загружаем твою модель
model = YOLO('yolov8m.pt')

# Экспортируем в ONNX с фиксированным размером кадра (стандарт для YOLOv8)
model.export(format='onnx', imgsz=640)
print("[SUCCESS] Модель сконвертирована! Ищи файл 'yolov8m.onnx' в папке.")