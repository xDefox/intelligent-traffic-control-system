using UnityEngine;
using Unity.InferenceEngine;
using System.Collections;
using System.Collections.Generic;
using UnityEngine.Networking;
using UnityEngine.UI; // Обязательно для работы с Image и Outline

public class EdgeVisionCamera : MonoBehaviour
{
    [Header("Настройки ИИ (Inference Engine)")]
    public ModelAsset yoloModelAsset;
    public float detectionInterval = 0.2f;
    public int maxZoneCapacity = 10;

    [Range(0f, 1f)]
    public float confidenceThreshold = 0.4f;

    [Header("Визуализация рамок (UI Canvas)")]
    [Tooltip("Перетащи сюда Canvas, который создан внутри этой камеры")]
    public Canvas targetCanvas;
    public int maxVisibleBoxes = 15;

    private List<RectTransform> boxPool = new List<RectTransform>();
    private RectTransform canvasRectTransform;

    [Header("Сетевой шлюз (FastAPI)")]
    private string serverUrl = "http://127.0.0.1:8050/api/v1/update-congestion";

    private Camera targetCamera;
    private RenderTexture rt;
    private Worker engine;
    private Tensor<float> inputTensor;

    private struct BoundingBox
    {
        // Нормализованные координаты (0..1), где (0,0) - левый нижний угол (стандарт Unity)
        public float xCenter, yCenter, width, height;
        public float confidence;
    }

    private List<BoundingBox> detectedBoxes = new List<BoundingBox>();
    private HashSet<int> vehicleClassIds = new HashSet<int> { 2, 3, 5, 7 };

    [Header("Зона детекции (ROI) для этой камеры")]
    [Tooltip("Задай 4 точки полигона в нормализованных координатах (от 0 до 1)")]
    public Vector2[] roiPolygon = new Vector2[]
    {
        new Vector2(0.35f, 0.14f),
        new Vector2(0.41f, 0.14f),
        new Vector2(0.67f, 0.80f),
        new Vector2(0.15f, 0.80f)
    };

    private LineRenderer roiLineRenderer;

    void Start()
    {
        targetCamera = GetComponent<Camera>();
        rt = new RenderTexture(640, 640, 24);
        inputTensor = new Tensor<float>(new TensorShape(1, 3, 640, 640));

        // Отрисовка желтого ROI — ДЕЛАЕМ ЛИНИЮ ТОНЬШЕ (0.005f вместо 0.02f)
        roiLineRenderer = gameObject.AddComponent<LineRenderer>();
        roiLineRenderer.useWorldSpace = false;
        roiLineRenderer.positionCount = roiPolygon.Length;
        roiLineRenderer.loop = true;
        roiLineRenderer.startWidth = 0.005f;
        roiLineRenderer.endWidth = 0.005f;
        roiLineRenderer.material = new Material(Shader.Find("Sprites/Default"));
        roiLineRenderer.startColor = Color.yellow;
        roiLineRenderer.endColor = Color.yellow;

        for (int i = 0; i < roiPolygon.Length; i++)
        {
            Vector3 viewportPoint = new Vector3(roiPolygon[i].x, roiPolygon[i].y, 1.0f);
            Vector3 localPoint = targetCamera.ViewportToWorldPoint(viewportPoint);
            localPoint = targetCamera.transform.InverseTransformPoint(localPoint);
            roiLineRenderer.SetPosition(i, localPoint);
        }

        // НАСТРОЙКА КАНВАСА И СОЗДАНИЕ КРАСИВЫХ РАМОК
        if (targetCanvas != null)
        {
            targetCanvas.renderMode = RenderMode.ScreenSpaceCamera;
            targetCanvas.worldCamera = targetCamera;
            targetCanvas.planeDistance = 0.4f; // Чуть ближе желтой линии, чтобы не перекрывались

            canvasRectTransform = targetCanvas.GetComponent<RectTransform>();

            for (int i = 0; i < maxVisibleBoxes; i++)
            {
                // Главный контейнер рамки
                GameObject boxObj = new GameObject($"UI_Box_{i}", typeof(RectTransform), typeof(Image));
                boxObj.transform.SetParent(targetCanvas.transform, false);

                RectTransform rtBox = boxObj.GetComponent<RectTransform>();
                rtBox.anchorMin = new Vector2(0.5f, 0.5f);
                rtBox.anchorMax = new Vector2(0.5f, 0.5f);
                rtBox.pivot = new Vector2(0.5f, 0.5f);

                // Делаем центр ПОЛНОСТЬЮ прозрачным
                Image mainImg = boxObj.GetComponent<Image>();
                mainImg.color = Color.clear;

                // Создаем 4 линии (Top, Bottom, Left, Right) внутри рамки
                CreateUiLine(boxObj.transform, "Top", new Vector2(0, 1), new Vector2(1, 1), new Vector2(0, -2));
                CreateUiLine(boxObj.transform, "Bottom", new Vector2(0, 0), new Vector2(1, 0), new Vector2(0, 2));
                CreateUiLine(boxObj.transform, "Left", new Vector2(0, 0), new Vector2(0, 1), new Vector2(2, 0));
                CreateUiLine(boxObj.transform, "Right", new Vector2(1, 0), new Vector2(1, 1), new Vector2(-2, 0));

                boxObj.SetActive(false);
                boxPool.Add(rtBox);
            }
        }
        else
        {
            Debug.LogError($"[Ошибка] На объекте {gameObject.name} не указан Target Canvas!");
        }

        if (yoloModelAsset != null)
        {
            Model runtimeModel = ModelLoader.Load(yoloModelAsset);
            engine = new Worker(runtimeModel, BackendType.GPUCompute);
            StartCoroutine(InferenceLoopCo());
        }
    }

    // Вспомогательный метод для динамического создания тонких UI-линий рамки
    void CreateUiLine(Transform parent, string name, Vector2 anchorMin, Vector2 anchorMax, Vector2 sizeOffset)
    {
        GameObject line = new GameObject(name, typeof(RectTransform), typeof(Image));
        line.transform.SetParent(parent, false);

        RectTransform rt = line.GetComponent<RectTransform>();
        rt.anchorMin = anchorMin;
        rt.anchorMax = anchorMax;

        // Задаем толщину рамки (2 пикселя)
        if (sizeOffset.x == 2 || sizeOffset.x == -2) // Вертикальные линии
        {
            rt.pivot = new Vector2(sizeOffset.x > 0 ? 0f : 1f, 0.5f);
            rt.sizeDelta = new Vector2(2f, 0f);
        }
        else // Горизонтальные линии
        {
            rt.pivot = new Vector2(0.5f, sizeOffset.y > 0 ? 0f : 1f);
            rt.sizeDelta = new Vector2(0f, 2f);
        }

        Image img = line.GetComponent<Image>();
        img.color = Color.green; // Цвет рамки машинки
    }

    void ParseYoloOutputs(Tensor<float> output)
    {
        detectedBoxes.Clear();
        if (output == null) return;

        output.CompleteAllPendingOperations();
        Tensor<float> cpuOutput = output.ReadbackAndClone() as Tensor<float>;
        int numAnchors = cpuOutput.shape[2];

        for (int i = 0; i < numAnchors; i++)
        {
            float maxScore = 0;

            foreach (int classId in vehicleClassIds)
            {
                float score = cpuOutput[0, 4 + classId, i];
                if (score > maxScore) maxScore = score;
            }

            if (maxScore > confidenceThreshold)
            {
                float xCenter = cpuOutput[0, 0, i];
                float yCenter = cpuOutput[0, 1, i];
                float width = cpuOutput[0, 2, i];
                float height = cpuOutput[0, 3, i];

                if (xCenter <= 1.0f && width <= 1.0f)
                {
                    xCenter *= 640f; yCenter *= 640f; width *= 640f; height *= 640f;
                }

                // Переводим в нормализованный вид (0..1)
                float normX = xCenter / 640f;
                float normY = yCenter / 640f;
                float normW = width / 640f;
                float normH = height / 640f;

                // В YOLO 0 - это верх экрана. Инвертируем Y для координатной сетки Unity UI
                Vector2 centerPointNormalizedUnity = new Vector2(normX, 1f - normY);

                if (IsPointInPolygon(centerPointNormalizedUnity, roiPolygon))
                {
                    detectedBoxes.Add(new BoundingBox
                    {
                        xCenter = normX,
                        yCenter = 1f - normY,
                        width = normW,
                        height = normH,
                        confidence = maxScore
                    });
                }
            }
        }
        cpuOutput?.Dispose();
    }

    void UpdateBoxVisuals()
    {
        if (canvasRectTransform == null) return;

        Vector2 canvasSize = canvasRectTransform.rect.size;

        for (int i = 0; i < boxPool.Count; i++)
        {
            if (i < detectedBoxes.Count)
            {
                BoundingBox box = detectedBoxes[i];
                RectTransform rtBox = boxPool[i];

                // Переводим из нормализованных координат (0..1) в локальное пространство Canvas (относительно центра)
                float localX = (box.xCenter - 0.5f) * canvasSize.x;
                float localY = (box.yCenter - 0.5f) * canvasSize.y;
                float pixelW = box.width * canvasSize.x;
                float pixelH = box.height * canvasSize.y;

                rtBox.anchoredPosition = new Vector2(localX, localY);
                rtBox.sizeDelta = new Vector2(pixelW, pixelH);

                // Принудительно включаем только те, под которые есть машины
                if (!rtBox.gameObject.activeSelf) rtBox.gameObject.SetActive(true);
            }
            else
            {
                // Все остальные избыточные боксы ГАРАНТИРОВАННО выключаем
                if (boxPool[i].gameObject.activeSelf) boxPool[i].gameObject.SetActive(false);
            }
        }
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

    IEnumerator SendAnalyticsToGateway(string camId, int cars, float congestion)
    {
        string jsonPayload = $"{{\"camera_id\":\"{camId}\",\"car_count\":{cars},\"congestion_index\":{congestion.ToString("F2", System.Globalization.CultureInfo.InvariantCulture)}}}";

        using (UnityWebRequest www = new UnityWebRequest(serverUrl, "POST"))
        {
            byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes(jsonPayload);
            www.uploadHandler = new UploadHandlerRaw(bodyRaw);
            www.downloadHandler = new DownloadHandlerBuffer();
            www.SetRequestHeader("Content-Type", "application/json");
            yield return www.SendWebRequest();
        }
    }

    IEnumerator InferenceLoopCo()
    {
        while (true)
        {
            // Ждем заданный интервал перед следующей детекцией
            yield return new WaitForSeconds(detectionInterval);
            // Ждем конца кадра, чтобы RenderTexture успел корректно захватить изображение
            yield return new WaitForEndOfFrame();

            if (targetCamera == null || rt == null || engine == null) continue;

            // Направляем рендер камеры в нашу текстуру 640x640
            targetCamera.targetTexture = rt;
            targetCamera.Render();
            targetCamera.targetTexture = null;

            // Передаем текстуру в ИИ-движок и запускаем инференс
            TextureConverter.ToTensor(rt, inputTensor);
            engine.Schedule(inputTensor);

            // Считываем результаты
            Tensor<float> outputTensor = engine.PeekOutput() as Tensor<float>;
            ParseYoloOutputs(outputTensor);

            // Считаем загруженность зоны
            int detectedCars = detectedBoxes.Count;
            float congestionIndex = Mathf.Clamp01((float)detectedCars / maxZoneCapacity);

            // Обновляем наши зеленые UI-прямоугольники на Canvas
            UpdateBoxVisuals();

            // Отправляем данные аналитики на бэкенд FastAPI
            StartCoroutine(SendAnalyticsToGateway(gameObject.name, detectedCars, congestionIndex));
        }
    }

    void OnDestroy()
    {
        engine?.Dispose();
        inputTensor?.Dispose();
        if (rt != null) rt.Release();
    }

#if UNITY_EDITOR
    // Этот метод вызывается автоматически при изменении значений в инспекторе
    private void OnValidate()
    {
        // Проверяем, запущена ли игра, инициализированы ли камера и LineRenderer
        if (Application.isPlaying && targetCamera != null && roiLineRenderer != null && roiPolygon != null)
        {
            // Обновляем количество точек, если ты решил добавить или удалить вершины
            roiLineRenderer.positionCount = roiPolygon.Length;

            // Пересчитываем координаты точек на лету
            for (int i = 0; i < roiPolygon.Length; i++)
            {
                Vector3 viewportPoint = new Vector3(roiPolygon[i].x, roiPolygon[i].y, 1.0f);
                Vector3 worldPoint = targetCamera.ViewportToWorldPoint(viewportPoint);
                Vector3 localPoint = targetCamera.transform.InverseTransformPoint(worldPoint);

                roiLineRenderer.SetPosition(i, localPoint);
            }
        }
    }
#endif
}