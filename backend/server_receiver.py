import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

app = FastAPI(
    title="Smart Crossroads Gateway",
    description="Шлюз для сбора аналитики загруженности дорог с камер ИИ"
)


# Модель данных, строго соответствующая отправляемому JSON из Unity
class CongestionData(BaseModel):
    camera_id: str = Field(..., description="Уникальный идентификатор или имя камеры из Unity")
    car_count: int = Field(..., ge=0, description="Количество зафиксированных автомобилей в зоне")
    congestion_index: float = Field(..., ge=0.0, le=1.0, description="Индекс затора от 0.0 (пусто) до 1.0 (пробка)")


# Эндпоинт, на который Unity шлет POST-запросы
@app.post(
    "/api/v1/update-congestion",
    status_code=status.HTTP_200_OK,
    summary="Обновить данные о заторе с камеры"
)
async def update_congestion(data: CongestionData):
    try:
        # Здесь будет логика обработки данных
        # Например: сохранение в БД (SQLite/PostgreSQL) или отправка диспетчеру

        print(
            f"[Камера: {data.camera_id}] Машин в зоне: {data.car_count} | Индекс затора: {data.congestion_index * 100:.1f}%")

        # Возвращаем статус успешной обработки
        return {
            "status": "success",
            "message": f"Данные с камеры '{data.camera_id}' успешно обработаны."
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Внутренняя ошибка сервера при обработке данных: {str(e)}"
        )

@app.get("/")
async def root():
    return {"status": "gateway_online", "info": "Перейди на http://127.0.0.1:8050/docs для просмотра API"}

if __name__ == "__main__":
    # Запускаем сервер на порту 8050, который прописан в скрипте EdgeVisionCamera.cs
    uvicorn.run("main:app", host="127.0.0.1", port=8050, reload=True)