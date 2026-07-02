using UnityEngine;
using Unity.InferenceEngine;
using System.Collections;
using System.Collections.Generic;
using UnityEngine.Networking;

public class EdgeVisionCamera : MonoBehaviour
{
    [Header("Настройки ИИ (Inference Engine)")]
    public ModelAsset yoloModelAsset;
    public float detectionInterval = 0.2f;
    public int maxZoneCapacity = 10;

    [Range(0f, 1f)]
    public float confidenceThreshold = 0.5f; // Порог точности для отображения рамки

    [Header("Сетевой шлюз (FastAPI)")]
    private string serverUrl = "http://127.0.0.1:8050/api/v1/update-congestion";

    private Camera targetCamera;
    private RenderTexture rt;
    private Worker engine;
    private Tensor<float> inputTensor;

    // Структура для хранения найденной машины для отрисовки
    private struct BoundingBox
    {
        public Rect rect;
        public float confidence;
        public int classId;
    }

    private List<BoundingBox> detectedBoxes = new List<BoundingBox>();

    // Индексы классов в стандартном датасете COCO для транспорта
    private HashSet<int> vehicleClassIds = new HashSet<int> { 2, 3, 5, 7 }; // 2 = car, 3 = motorbike, 5 = bus, 7 = truck

    // Координаты полигона ROI (от 0.0 до 1.0)
    private Vector2[] roiPolygon = new Vector2[]
    {
        new Vector2(0.35f, 0.14f),
        new Vector2(0.41f, 0.14f),
        new Vector2(0.67f, 0.80f),
        new Vector2(0.15f, 0.80f)
    };

    void Start()
    {
        targetCamera = GetComponent<Camera>();
        rt = new RenderTexture(640, 640, 24);
        inputTensor = new Tensor<float>(new TensorShape(1, 3, 640, 640));

        Model runtimeModel = ModelLoader.Load(yoloModelAsset);
        engine = new Worker(runtimeModel, BackendType.GPUCompute);

        StartCoroutine(InferenceLoopCo());
    }

    IEnumerator InferenceLoopCo()
    {
        while (true)
        {
            yield return new WaitForSeconds(detectionInterval);
            yield return new WaitForEndOfFrame();

            targetCamera.targetTexture = rt;
            targetCamera.Render();
            targetCamera.targetTexture = null;

            TextureConverter.ToTensor(rt, inputTensor);
            engine.Schedule(inputTensor);

            // Получаем выходной тензор
            Tensor<float> outputTensor = engine.PeekOutput() as Tensor<float>;

            // Парсим реальные рамки
            ParseYoloOutputs(outputTensor);

            int detectedCars = detectedBoxes.Count;
            float congestionIndex = Mathf.Clamp01((float)detectedCars / maxZoneCapacity);

            StartCoroutine(SendAnalyticsToGateway(detectedCars, congestionIndex));
        }
    }

    void ParseYoloOutputs(Tensor<float> output)
    {
        detectedBoxes.Clear();
        if (output == null) return;

        output.CompleteAllPendingOperations();
        Tensor<float> cpuOutput = output.ReadbackAndClone() as Tensor<float>;

        int numAnchors = cpuOutput.shape[2]; // 8400

        for (int i = 0; i < numAnchors; i++)
        {
            float maxScore = 0;
            int bestClassId = -1;

            foreach (int classId in vehicleClassIds)
            {
                float score = cpuOutput[0, 4 + classId, i];
                if (score > maxScore)
                {
                    maxScore = score;
                    bestClassId = classId;
                }
            }

            // ВРЕМЕННО: Опустим порог до 0.3 для тестов, чтобы поймать хоть что-то
            if (maxScore > 0.3f)
            {
                float xCenter = cpuOutput[0, 0, i];
                float yCenter = cpuOutput[0, 1, i];
                float width = cpuOutput[0, 2, i];
                float height = cpuOutput[0, 3, i];

                // Дебаг лог: проверяем, какие вообще координаты приходят из модели
                // Debug.Log($"Raw YOLO: x={xCenter}, y={yCenter}, w={width}, h={height}, score={maxScore}");

                // Переводим в экранные координаты
                float screenX = (xCenter - width / 2f) / 640f * Screen.width;
                float screenY = (1f - (yCenter + height / 2f) / 640f) * Screen.height;
                float screenW = width / 640f * Screen.width;
                float screenH = height / 640f * Screen.height;

                BoundingBox box = new BoundingBox
                {
                    rect = new Rect(screenX, screenY, screenW, screenH),
                    confidence = maxScore,
                    classId = bestClassId
                };

                // ВРЕМЕННО комментируем фильтр ROI, чтобы увидеть ВСЕ детекции на экране!
                detectedBoxes.Add(box);
            }
        }

        if (detectedBoxes.Count > 0)
        {
            Debug.Log($"[ИИ Дебаг] Найдено объектов на экране: {detectedBoxes.Count}");
        }

        cpuOutput?.Dispose();
    }

    void OnGUI()
    {
        // 1. РИСУЕМ ЗОНУ ROI (Полигон), чтобы ты видел ее на экране Game
        Vector2[] screenPolygon = new Vector2[roiPolygon.Length];
        for (int i = 0; i < roiPolygon.Length; i++)
        {
            screenPolygon[i] = new Vector2(roiPolygon[i].x * Screen.width, (1f - roiPolygon[i].y) * Screen.height);
        }
        for (int i = 0; i < screenPolygon.Length; i++)
        {
            Vector2 p1 = screenPolygon[i];
            Vector2 p2 = screenPolygon[(i + 1) % screenPolygon.Length];
            // Рисуем линии нашей зоны (Желтые)
            DrawLine(p1, p2, Color.yellow, 3f);
        }

        // 2. РИСУЕМ РАМКИ МАШИН
        if (detectedBoxes.Count == 0) return;

        foreach (var box in detectedBoxes)
        {
            // Рисуем рамку (Зеленая)
            DrawScreenRect(box.rect, Color.green, 2f);

            string label = $"Vehicle: {box.confidence * 100:.0f}%";
            GUI.backgroundColor = Color.black;
            GUI.Label(new Rect(box.rect.x, box.rect.y - 20, 150, 20), label);
        }
    }

    // Вспомогательный метод для рисования линий полигона ROI
    // Вспомогательный метод для рисования линий полигона ROI через Pivot
    void DrawLine(Vector2 start, Vector2 end, Color color, float thickness)
    {
        Vector2 d = end - start;
        float a = Mathf.Atan2(d.y, d.x) * Mathf.Rad2Deg;

        // ИСПРАВЛЕНО: Используем RotateAroundPivot вместо несуществующего RotateAroundTransform
        GUIUtility.RotateAroundPivot(a, start);

        Texture2D tex = new Texture2D(1, 1);
        tex.SetPixel(0, 0, color);
        tex.Apply();

        GUI.DrawTexture(new Rect(start.x, start.y, d.magnitude, thickness), tex);

        // Сбрасываем поворот обратно, чтобы не сломать отрисовку остальных элементов
        GUIUtility.RotateAroundPivot(-a, start);
    }

    void DrawScreenRect(Rect rect, Color color, float thickness)
    {
        Texture2D lineTex = new Texture2D(1, 1);
        lineTex.SetPixel(0, 0, color);
        lineTex.Apply();

        // Верхняя линия
        GUI.DrawTexture(new Rect(rect.x, rect.y, rect.width, thickness), lineTex);
        // Нижняя линия
        GUI.DrawTexture(new Rect(rect.x, rect.y + rect.height - thickness, rect.width, thickness), lineTex);
        // Левая линия
        GUI.DrawTexture(new Rect(rect.x, rect.y, thickness, rect.height), lineTex);
        // Правая линия
        GUI.DrawTexture(new Rect(rect.x + rect.width - thickness, rect.y, thickness, rect.height), lineTex);
    }

    bool IsPointInPolygon(Vector2 point, Vector2[] polygon)
    {
        bool isInside = false;
        for (int i = 0, j = polygon.Length - 1; i < polygon.Length; j = i++)
        {
            if (((polygon[i].y > point.y) != (polygon[j].y > point.y)) &&
                (point.x < (polygon[j].x - polygon[i].x) * (point.y - polygon[i].y) / (polygon[j].y - polygon[i].y) + polygon[i].x))
            {
                isInside = !isInside;
            }
        }
        return isInside;
    }

    IEnumerator SendAnalyticsToGateway(int cars, float congestion)
    {
        string jsonPayload = $"{{\"camera_id\":\"{gameObject.name}\",\"car_count\":{cars},\"congestion_index\":{congestion.ToString("F2", System.Globalization.CultureInfo.InvariantCulture)}}}";

        using (UnityWebRequest www = new UnityWebRequest(serverUrl, "POST"))
        {
            byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes(jsonPayload);
            www.uploadHandler = new UploadHandlerRaw(bodyRaw);
            www.downloadHandler = new DownloadHandlerBuffer();
            www.SetRequestHeader("Content-Type", "application/json");
            yield return www.SendWebRequest();
        }
    }

    void OnDestroy()
    {
        engine?.Dispose();
        inputTensor?.Dispose();
        if (rt != null) rt.Release();
    }
}