import torch
from fastapi import FastAPI, File, UploadFile
import uvicorn
import cv2
import numpy as np
from ultralytics import YOLO

app = FastAPI(title="Smart Crossroads - Production Ready")

# Проверяем видеокарту
device = 'cuda' if torch.cuda.is_available() else 'cpu'
print(f"[SYSTEM] ИИ запущен на: {device.upper()}")

# Загружаем модель
model = YOLO('yolov8m.pt').to(device)

# Флаг занятости процессора (чтобы кадры не накладывались друг на друга)
is_processing = False

# Твои скорректированные координаты зоны из Unity
roi_polygon = np.array([
    [450, 100],  # Верхняя левая
    [530, 100],
    [860, 580],# правый низ
    [200, 580]  # левый низ
], np.int32)

# Создаем ОДНО фиксированное окно для стрима при старте сервера
cv2.namedWindow("Unity Live CCTV Stream", cv2.WINDOW_NORMAL)


@app.post("/api/v1/upload-frame")
async def upload_frame(image: UploadFile = File(...)):
    global is_processing

    # ЗАЩИТА: Если ИИ ещё считает прошлый кадр — этот мы просто дропаем,
    # чтобы не вешать ноут и не копить лаги
    if is_processing:
        return {"status": "skipped", "reason": "server_busy"}

    is_processing = True

    try:
        contents = await image.read()
        nparr = np.frombuffer(contents, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            is_processing = False
            return {"status": "error", "message": "bad image"}

        # СТРОГИЙ ФИЛЬТР: classes=[2] оставляет ТОЛЬКО машины (car).
        # Никаких самолетов и грузовиков на горизонте больше не будет.
        results = model(img, classes=[2, 3], conf=0.3, verbose=False)

        car_count = 0
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])

                # Точно такой же расчет центра, как в детекторе!
                cx = int((x1 + x2) / 2)
                cy = int((y1 + y2) / 2)

                inside = cv2.pointPolygonTest(roi_polygon, (cx, cy), False) >= 0
                if inside:
                    car_count += 1
                    cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.circle(img, (cx, cy), 5, (0, 255, 0), -1)
                else:
                    cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 1)
                    cv2.circle(img, (cx, cy), 5, (0, 0, 255), -1)

        # Отрисовка интерфейса поверх кадра
        cv2.polylines(img, [roi_polygon], True, (255, 255, 0), 2)
        cv2.putText(img, f"In Queue: {car_count}", (30, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2)

        # Безопасное обновление окна
        cv2.imshow("Unity Live CCTV Stream", img)
        cv2.waitKey(1)  # Короткий такт для перерисовки графики Windows

    finally:
        # Освобождаем сервер для следующего кадра
        is_processing = False

    return {"status": "success", "detected_cars": car_count}


if __name__ == "__main__":
    # Запускаем на порту 8050
    uvicorn.run(app, host="127.0.0.1", port=8050)