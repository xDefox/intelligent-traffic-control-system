import cv2
from ultralytics import YOLO
import numpy as np

# 1. Загружаем модель
model = YOLO('yolov8m.pt')

# 2. Загружаем тестовое изображение
image_path = 'traffic_technical.png'
img = cv2.imread(image_path)

if img is None:
    print(f"Ошибка: Не удалось загрузить {image_path}")
    exit()

# 3. Координаты зоны (ROI) под тестовую картинку
roi_polygon = np.array([
    [750, 340],
    [900, 400],
    [170, 950],
    [-440, 1000]
], np.int32)

# 4. Запускаем детекцию
results = model(img, classes=[2, 3, 5, 7], conf=0.3)

car_count_in_roi = 0
total_detected = 0

for result in results:
    for box in result.boxes:
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        total_detected += 1

        # Единая логика: считаем ГЕОМЕТРИЧЕСКИЙ ЦЕНТР
        cx = int((x1 + x2) / 2)
        cy = int((y1 + y2) / 2)

        # Проверяем попадание центра в зону
        inside = cv2.pointPolygonTest(roi_polygon, (cx, cy), False) >= 0

        if inside:
            car_count_in_roi += 1
            # В ЗОНЕ: Жирная зеленая рамка и точка
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 3)
            cv2.circle(img, (cx, cy), 5, (0, 255, 0), -1)

            cls = int(box.cls[0])
            class_name = model.names[cls]
            cv2.putText(img, f"{class_name} IN ROI", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        else:
            # ВНЕ ЗОНЫ: Тонкая красная рамка и точка
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 1)
            cv2.circle(img, (cx, cy), 5, (0, 0, 255), -1)

# Рисуем саму зону
cv2.polylines(img, [roi_polygon], True, (255, 255, 0), 4)
cv2.putText(img, f"DETECTED IN ROI: {car_count_in_roi}", (50, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 0), 4)
cv2.putText(img, f"Total Detected: {total_detected}", (50, 110), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

print(f"Обработка завершена. Найдено в ROI: {car_count_in_roi}")

cv2.imshow("Smart Crossroads: Local Test Mode", img)
cv2.waitKey(0)
cv2.destroyAllWindows()