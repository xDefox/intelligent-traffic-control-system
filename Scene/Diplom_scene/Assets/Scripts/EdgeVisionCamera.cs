using UnityEngine;
using Unity.InferenceEngine;
using System.Collections;
using System.Collections.Generic;
using UnityEngine.Networking;
using UnityEngine.UI;

public class EdgeVisionCamera : MonoBehaviour
{
    [Header("Настройки ИИ (Inference Engine)")]
    public ModelAsset yoloModelAsset;
    public float detectionInterval = 0.2f;
    public int maxZoneCapacity = 10;

    [Range(0f, 1f)]
    public float confidenceThreshold = 0.4f;

    [Range(0f, 1f)]
    [Tooltip("Порог перекрытия рамок. Чем меньше, тем жестче склеиваются дубликаты (оптимально 0.3 - 0.45)")]
    public float iouThreshold = 0.4f; // <-- ДОБАВИЛИ ПОРОГ ДЛЯ NMS

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
        public float xCenter, yCenter, width, height;
        public float confidence;
    }

    private List<BoundingBox> detectedBoxes = new List<BoundingBox>();
    private HashSet<int> vehicleClassIds = new HashSet<int> { 2, 3, 5, 7 };

    [Header("Зона детекции (ROI) для этой камеры")]
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

        if (targetCanvas != null)
        {
            targetCanvas.renderMode = RenderMode.ScreenSpaceCamera;
            targetCanvas.worldCamera = targetCamera;
            targetCanvas.planeDistance = 0.4f;

            canvasRectTransform = targetCanvas.GetComponent<RectTransform>();

            for (int i = 0; i < maxVisibleBoxes; i++)
            {
                GameObject boxObj = new GameObject($"UI_Box_{i}", typeof(RectTransform), typeof(Image));
                boxObj.transform.SetParent(targetCanvas.transform, false);

                RectTransform rtBox = boxObj.GetComponent<RectTransform>();
                rtBox.anchorMin = new Vector2(0.5f, 0.5f);
                rtBox.anchorMax = new Vector2(0.5f, 0.5f);
                rtBox.pivot = new Vector2(0.5f, 0.5f);

                Image mainImg = boxObj.GetComponent<Image>();
                mainImg.color = Color.clear;

                CreateUiLine(boxObj.transform, "Top", new Vector2(0, 1), new Vector2(1, 1), new Vector2(0, -2));
                CreateUiLine(boxObj.transform, "Bottom", new Vector2(0, 0), new Vector2(1, 0), new Vector2(0, 2));
                CreateUiLine(boxObj.transform, "Left", new Vector2(0, 0), new Vector2(0, 1), new Vector2(2, 0));
                CreateUiLine(boxObj.transform, "Right", new Vector2(1, 0), new Vector2(1, 1), new Vector2(-2, 0));

                boxObj.SetActive(false);
                boxPool.Add(rtBox);
            }
        }

        if (yoloModelAsset != null)
        {
            Model runtimeModel = ModelLoader.Load(yoloModelAsset);
            engine = new Worker(runtimeModel, BackendType.GPUCompute);
            StartCoroutine(InferenceLoopCo());
        }
    }

    void CreateUiLine(Transform parent, string name, Vector2 anchorMin, Vector2 anchorMax, Vector2 sizeOffset)
    {
        GameObject line = new GameObject(name, typeof(RectTransform), typeof(Image));
        line.transform.SetParent(parent, false);

        RectTransform rt = line.GetComponent<RectTransform>();
        rt.anchorMin = anchorMin;
        rt.anchorMax = anchorMax;

        if (sizeOffset.x == 2 || sizeOffset.x == -2)
        {
            rt.pivot = new Vector2(sizeOffset.x > 0 ? 0f : 1f, 0.5f);
            rt.sizeDelta = new Vector2(2f, 0f);
        }
        else
        {
            rt.pivot = new Vector2(0.5f, sizeOffset.y > 0 ? 0f : 1f);
            rt.sizeDelta = new Vector2(0f, 2f);
        }

        Image img = line.GetComponent<Image>();
        img.color = Color.green;
    }

    void ParseYoloOutputs(Tensor<float> output)
    {
        List<BoundingBox> candidates = new List<BoundingBox>();
        if (output == null) return;

        // 1. Принудительно дожидаемся окончания расчетов на GPU
        output.CompleteAllPendingOperations();

        // 2. Используем `using` для клона. Память гарантированно очистится, жор RAM прекратится!
        using (Tensor<float> cpuOutput = output.ReadbackAndClone() as Tensor<float>)
        {
            // Автоматически определяем, где у нас каналы (84), а где анкоры (8400)
            int numAnchors = (cpuOutput.shape[2] > cpuOutput.shape[1]) ? cpuOutput.shape[2] : cpuOutput.shape[1];
            bool isTransposed = cpuOutput.shape[1] == numAnchors;

            for (int i = 0; i < numAnchors; i++)
            {
                float maxScore = 0;
                foreach (int classId in vehicleClassIds)
                {
                    // Если модель транспонирована, меняем индексы местами автоматически
                    float score = isTransposed ? cpuOutput[0, i, 4 + classId] : cpuOutput[0, 4 + classId, i];
                    if (score > maxScore) maxScore = score;
                }

                if (maxScore > confidenceThreshold)
                {
                    // Извлекаем координаты с учетом структуры модели
                    float rawX = isTransposed ? cpuOutput[0, i, 0] : cpuOutput[0, 0, i];
                    float rawY = isTransposed ? cpuOutput[0, i, 1] : cpuOutput[0, 1, i];
                    float rawW = isTransposed ? cpuOutput[0, i, 2] : cpuOutput[0, 2, i];
                    float rawH = isTransposed ? cpuOutput[0, i, 3] : cpuOutput[0, 3, i];

                    // Переводим в нормализованные 0..1
                    float normX = (rawX > 1.0f) ? rawX / 640f : rawX;
                    float normY = (rawY > 1.0f) ? rawY / 640f : rawY;
                    float normW = (rawW > 1.0f) ? rawW / 640f : rawW;
                    float normH = (rawH > 1.0f) ? rawH / 640f : rawH;

                    normX = Mathf.Clamp01(normX);
                    normY = Mathf.Clamp01(normY);
                    normW = Mathf.Clamp01(normW);
                    normH = Mathf.Clamp01(normH);

                    float unityY = 1f - normY;
                    Vector2 centerPointNormalizedUnity = new Vector2(normX, unityY);

                    if (IsPointInPolygon(centerPointNormalizedUnity, roiPolygon))
                    {
                        candidates.Add(new BoundingBox
                        {
                            xCenter = normX,
                            yCenter = unityY,
                            width = normW,
                            height = normH,
                            confidence = maxScore
                        });
                    }
                }
            }
        } // Здесь cpuOutput уничтожается автоматически, предотвращая утечку 4 ГБ!

        // Применяем NMS фильтрацию к очищенным данным
        detectedBoxes = ApplyNMS(candidates, iouThreshold);
    }

    // РЕАЛИЗАЦИЯ АЛГОРИТМА NON-MAXIMUM SUPPRESSION (NMS)
    List<BoundingBox> ApplyNMS(List<BoundingBox> boxes, float iouThresh)
    {
        // Сортируем рамки по убыванию уверенности нейросети
        boxes.Sort((a, b) => b.confidence.CompareTo(a.confidence));

        List<BoundingBox> result = new List<BoundingBox>();
        bool[] suppressed = new bool[boxes.Count];

        for (int i = 0; i < boxes.Count; i++)
        {
            if (suppressed[i]) continue;

            BoundingBox baseBox = boxes[i];
            result.Add(baseBox);

            for (int j = i + 1; j < boxes.Count; j++)
            {
                if (suppressed[j]) continue;

                // Если рамка сильно перекрывает текущую базовую — удаляем её
                if (CalculateIoU(baseBox, boxes[j]) > iouThresh)
                {
                    suppressed[j] = true;
                }
            }
        }

        return result;
    }

    // Расчет коэффициента пересечения двух прямоугольников (Intersection over Union)
    float CalculateIoU(BoundingBox boxA, BoundingBox boxB)
    {
        float x1A = boxA.xCenter - boxA.width / 2f;
        float y1A = boxA.yCenter - boxA.height / 2f;
        float x2A = boxA.xCenter + boxA.width / 2f;
        float y2A = boxA.yCenter + boxA.height / 2f;

        float x1B = boxB.xCenter - boxB.width / 2f;
        float y1B = boxB.yCenter - boxB.height / 2f;
        float x2B = boxB.xCenter + boxB.width / 2f;
        float y2B = boxB.yCenter + boxB.height / 2f;

        float xOverlap = Mathf.Max(0, Mathf.Min(x2A, x2B) - Mathf.Max(x1A, x1B));
        float yOverlap = Mathf.Max(0, Mathf.Min(y2A, y2B) - Mathf.Max(y1A, y1B));

        float intersectionArea = xOverlap * yOverlap;

        float areaA = boxA.width * boxA.height;
        float areaB = boxB.width * boxB.height;

        float unionArea = areaA + areaB - intersectionArea;

        if (unionArea <= 0) return 0;
        return intersectionArea / unionArea;
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

                float localX = (box.xCenter - 0.5f) * canvasSize.x;
                float localY = (box.yCenter - 0.5f) * canvasSize.y;
                float pixelW = box.width * canvasSize.x;
                float pixelH = box.height * canvasSize.y;

                rtBox.anchoredPosition = new Vector2(localX, localY);
                rtBox.sizeDelta = new Vector2(pixelW, pixelH);

                if (!rtBox.gameObject.activeSelf) rtBox.gameObject.SetActive(true);
            }
            else
            {
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
            yield return new WaitForSeconds(detectionInterval);
            yield return new WaitForEndOfFrame();

            if (targetCamera == null || rt == null || engine == null) continue;

            targetCamera.targetTexture = rt;
            targetCamera.Render();
            targetCamera.targetTexture = null;

            TextureConverter.ToTensor(rt, inputTensor);
            engine.Schedule(inputTensor);

            Tensor<float> outputTensor = engine.PeekOutput() as Tensor<float>;
            ParseYoloOutputs(outputTensor);

            int detectedCars = detectedBoxes.Count;
            float congestionIndex = Mathf.Clamp01((float)detectedCars / maxZoneCapacity);

            UpdateBoxVisuals();

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
    private void OnValidate()
    {
        if (Application.isPlaying && targetCamera != null && roiLineRenderer != null && roiPolygon != null)
        {
            roiLineRenderer.positionCount = roiPolygon.Length;

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