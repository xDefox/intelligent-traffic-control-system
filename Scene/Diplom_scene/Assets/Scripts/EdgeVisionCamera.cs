using System.Collections.Generic;
using Unity.InferenceEngine;
using UnityEngine;
using UnityEngine.UI;

public class EdgeVisionCamera : MonoBehaviour
{
    [Header("Настройки детекции полосы")]
    public int maxZoneCapacity = 10;
    [Range(0f, 1f)] public float confidenceThreshold = 0.4f;
    [Range(0f, 1f)] public float iouThreshold = 0.4f;

    [Header("Визуализация рамок (UI Canvas)")]
    public Canvas targetCanvas;
    public int maxVisibleBoxes = 15;

    private List<RectTransform> boxPool = new List<RectTransform>();
    private Camera targetCamera;
    private RenderTexture rt;

    public struct BoundingBox
    {
        public float xCenter, yCenter, width, height;
        public float confidence;
    }

    private List<BoundingBox> detectedBoxes = new List<BoundingBox>();
    private HashSet<int> vehicleClassIds = new HashSet<int> { 0, 1 }; // 2, 3, 5, 7 для yolo8

    [Header("Зона детекции (ROI) для этой камеры")]
    public Vector2[] roiPolygon = new Vector2[]
    {
        new Vector2(0.35f, 0.14f),
        new Vector2(0.41f, 0.14f),
        new Vector2(0.67f, 0.80f),
        new Vector2(0.15f, 0.80f)
    };

    [Header("Интерактив в игре")]
    [Tooltip("Включите, чтобы перетаскивать точки ROI мышкой прямо в окне Game")]
    public bool editRoiInGame = false;
    private int selectedPointIndex = -1;

    private LineRenderer roiLineRenderer;

    void Start()
    {
        targetCamera = GetComponent<Camera>();
        rt = new RenderTexture(1280, 720, 24);

        // Настройка линий ROI
        roiLineRenderer = gameObject.AddComponent<LineRenderer>();
        roiLineRenderer.useWorldSpace = false;
        roiLineRenderer.positionCount = roiPolygon.Length;
        roiLineRenderer.loop = true;
        roiLineRenderer.startWidth = 0.005f;
        roiLineRenderer.endWidth = 0.005f;
        roiLineRenderer.material = new Material(Shader.Find("Sprites/Default"));
        roiLineRenderer.startColor = Color.yellow;
        roiLineRenderer.endColor = Color.yellow;

        // Первичный просчет линий
        UpdateRoiLines();

        // Инициализация пула UI-рамок
        if (targetCanvas != null)
        {
            targetCanvas.renderMode = RenderMode.ScreenSpaceCamera;
            targetCanvas.worldCamera = targetCamera;
            targetCanvas.planeDistance = 0.4f;

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
    }

    private float roiUpdateTimer = 0f;
    private const float ROI_UPDATE_INTERVAL = 0.1f; // Update ROI lines 10 times per second instead of every frame
    
    void Update()
    {
        // 1. Если активирован режим редактирования — ловим мышь
        if (editRoiInGame)
        {
            try
            {
                HandleInGameRoiEditing();
            }
            catch (System.InvalidOperationException)
            {
                // Игнорируем ошибки старого Input при использовании Input System Package
            }
        }

        // 2. Update ROI lines less frequently - only every 0.1s
        roiUpdateTimer += Time.deltaTime;
        if (roiUpdateTimer >= ROI_UPDATE_INTERVAL)
        {
            roiUpdateTimer = 0f;
            UpdateRoiLines();
        }
    }

    private void HandleInGameRoiEditing()
    {
        if (targetCamera == null) return;

        // Переводим позицию курсора в нормализованные координаты Viewport (от 0 до 1)
        Vector2 mouseViewportPos = targetCamera.ScreenToViewportPoint(Input.mousePosition); 

        // ЛКМ зажата: ищем ближайшую точку для захвата
        if (Input.GetMouseButtonDown(0))
        {
            selectedPointIndex = -1;
            float grabRadius = 0.04f; // Радиус захвата точки (4% от размера экрана)

            for (int i = 0; i < roiPolygon.Length; i++)
            {
                float distance = Vector2.Distance(mouseViewportPos, roiPolygon[i]);
                if (distance < grabRadius)
                {
                    grabRadius = distance;
                    selectedPointIndex = i;
                }
            }
        }

        // Процесс перетаскивания
        if (Input.GetMouseButton(0) && selectedPointIndex != -1)
        {
            // Обновляем координату точки, зажимая её в границах экрана
            roiPolygon[selectedPointIndex] = new Vector2(
                Mathf.Clamp01(mouseViewportPos.x),
                Mathf.Clamp01(mouseViewportPos.y)
            );
        }

        // Отпустили ЛКМ — сбрасываем захват
        if (Input.GetMouseButtonUp(0))
        {
            selectedPointIndex = -1;
        }
    }

    public void UpdateRoiLines()
    {
        if (roiLineRenderer == null || targetCamera == null) return;

        if (roiLineRenderer.positionCount != roiPolygon.Length)
            roiLineRenderer.positionCount = roiPolygon.Length;

        for (int i = 0; i < roiPolygon.Length; i++)
        {
            Vector3 viewportPoint = new Vector3(roiPolygon[i].x, roiPolygon[i].y, 1.0f);
            Vector3 worldPoint = targetCamera.ViewportToWorldPoint(viewportPoint);
            Vector3 localPoint = targetCamera.transform.InverseTransformPoint(worldPoint);
            roiLineRenderer.SetPosition(i, localPoint);
        }
    }

    void CreateUiLine(Transform parent, string name, Vector2 anchorMin, Vector2 anchorMax, Vector2 sizeOffset)
    {
        GameObject line = new GameObject(name, typeof(RectTransform), typeof(Image));
        line.transform.SetParent(parent, false);

        RectTransform rectTrans = line.GetComponent<RectTransform>();
        rectTrans.anchorMin = anchorMin;
        rectTrans.anchorMax = anchorMax;

        if (sizeOffset.x == 2 || sizeOffset.x == -2)
        {
            rectTrans.pivot = new Vector2(sizeOffset.x > 0 ? 0f : 1f, 0.5f);
            rectTrans.sizeDelta = new Vector2(2f, 0f);
        }
        else
        {
            rectTrans.pivot = new Vector2(0.5f, sizeOffset.y > 0 ? 0f : 1f);
            rectTrans.sizeDelta = new Vector2(0f, 2f);
        }

        Image img = line.GetComponent<Image>();
        img.color = Color.green;
    }

    public RenderTexture CaptureFrame()
    {
        if (targetCamera == null || rt == null) return null;

        targetCamera.targetTexture = rt;
        targetCamera.Render();
        targetCamera.targetTexture = null;

        return rt;
    }

    public void ReleaseFrame()
    {
        // RenderTexture не уничтожаем — он переиспользуется.
        // Просто сбрасываем targetTexture, если вдруг что-то осталось.
        if (targetCamera != null)
        {
            targetCamera.targetTexture = null;
        }
    }

    public int UpdateDetectionsAndGetCount(Tensor<float> output)
    {
        if (output == null) return 0;

        output.CompleteAllPendingOperations();

        // Оптимизация: фильтруем анкоры во время копирования с GPU,
        // чтобы не создавать List<BoundingBox> для 8400 кандидатов, если детекций мало.
        using (Tensor<float> cpuOutput = output.ReadbackAndClone() as Tensor<float>)
        {
            int numAnchors = (cpuOutput.shape[2] > cpuOutput.shape[1]) ? cpuOutput.shape[2] : cpuOutput.shape[1];
            bool isTransposed = cpuOutput.shape[1] == numAnchors;

            // Избегаем аллокаций в цикле — сразу кладём прошедшие фильтр
            // Используем capacity hint для снижения переаллокаций
            int estimatedCandidates = Mathf.Min(200, numAnchors / 10);
            List<BoundingBox> candidates = new List<BoundingBox>(estimatedCandidates);

            // Предзагружаем константы, чтобы не считать их в цикле
            float inv1280 = 1f / 1280f;

            for (int i = 0; i < numAnchors; i++)
            {
                // Ранний выход: быстрая проверка confidence
                float maxScore = cpuOutput[isTransposed ? 0 : 0,
                                              isTransposed ? i : 4,
                                              isTransposed ? 4 : i];

                // Если первый класс не прошёл — проверяем остальные
                if (maxScore <= confidenceThreshold)
                {
                    foreach (int classId in vehicleClassIds)
                    {
                        if (classId == 0) continue; // уже проверили
                        float score = isTransposed ? cpuOutput[0, i, 4 + classId] : cpuOutput[0, 4 + classId, i];
                        if (score > maxScore) maxScore = score;
                    }
                }

                if (maxScore <= confidenceThreshold)
                    continue;

                // Извлекаем координаты
                float rawX = isTransposed ? cpuOutput[0, i, 0] : cpuOutput[0, 0, i];
                float rawY = isTransposed ? cpuOutput[0, i, 1] : cpuOutput[0, 1, i];
                float rawW = isTransposed ? cpuOutput[0, i, 2] : cpuOutput[0, 2, i];
                float rawH = isTransposed ? cpuOutput[0, i, 3] : cpuOutput[0, 3, i];

                bool modelOutputsPixels = (rawX > 1.5f);

                float normX = modelOutputsPixels ? rawX * inv1280 : rawX;
                float normY = modelOutputsPixels ? rawY * inv1280 : rawY;
                float normW = modelOutputsPixels ? rawW * inv1280 : rawW;
                float normH = modelOutputsPixels ? rawH * inv1280 : rawH;

                normX = Mathf.Clamp01(normX);
                normY = Mathf.Clamp01(normY);
                normW = Mathf.Clamp01(normW);
                normH = Mathf.Clamp01(normH);

                float unityY = 1f - normY;
                Vector2 centerPointNormalizedUnity = new Vector2(normX, unityY);

                // ROI check — быстрый отсев до создания объекта
                if (IsPointInPolygon(centerPointNormalizedUnity, roiPolygon))
                {
                    candidates.Add(new BoundingBox { xCenter = normX, yCenter = unityY, width = normW, height = normH, confidence = maxScore });
                }
            }

            detectedBoxes = ApplyNmsOptimized(candidates, iouThreshold);
        }

        UpdateBoxVisuals();
        return detectedBoxes.Count;
    }

    /// <summary>
    /// Оптимизированная NMS: предварительная сортировка + ранний выход
    /// при малом количестве детекций.
    /// </summary>
    private List<BoundingBox> ApplyNmsOptimized(List<BoundingBox> boxes, float iouThresh)
    {
        if (boxes.Count == 0) return new List<BoundingBox>();
        if (boxes.Count == 1) return new List<BoundingBox> { boxes[0] };

        // Сортируем по confidence (убывание)
        boxes.Sort((a, b) => b.confidence.CompareTo(a.confidence));

        List<BoundingBox> result = new List<BoundingBox>(Mathf.Min(boxes.Count, 20));
        bool[] suppressed = new bool[boxes.Count];

        for (int i = 0; i < boxes.Count; i++)
        {
            if (suppressed[i]) continue;
            result.Add(boxes[i]);

            for (int j = i + 1; j < boxes.Count; j++)
            {
                if (suppressed[j]) continue;
                if (ComputeIoU(boxes[i], boxes[j]) > iouThresh)
                {
                    suppressed[j] = true;
                }
            }
        }

        return result;
    }

    /// <summary>
    /// Оптимизированное вычисление IoU: без промежуточных переменных.
    /// </summary>
    private float ComputeIoU(BoundingBox a, BoundingBox b)
    {
        float left = Mathf.Max(a.xCenter - a.width * 0.5f, b.xCenter - b.width * 0.5f);
        float right = Mathf.Min(a.xCenter + a.width * 0.5f, b.xCenter + b.width * 0.5f);

        if (left >= right) return 0f;

        float top = Mathf.Min(a.yCenter + a.height * 0.5f, b.yCenter + b.height * 0.5f);
        float bottom = Mathf.Max(a.yCenter - a.height * 0.5f, b.yCenter - b.height * 0.5f);

        if (bottom >= top) return 0f;

        float interArea = (right - left) * (top - bottom);
        float areaA = a.width * a.height;
        float areaB = b.width * b.height;

        return interArea / (areaA + areaB - interArea);
    }

    private bool IsPointInPolygon(Vector2 point, Vector2[] polygon)
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

    private void UpdateBoxVisuals()
    {
        // Only update visuals if count changed
        int activeCount = Mathf.Min(detectedBoxes.Count, boxPool.Count);
        
        for (int i = 0; i < boxPool.Count; i++)
        {
            if (i < activeCount)
            {
                if (!boxPool[i].gameObject.activeSelf)
                {
                    boxPool[i].gameObject.SetActive(true);
                }
                
                BoundingBox box = detectedBoxes[i];
                // Only update if significantly changed
                Vector2 newAnchorMin = new Vector2(box.xCenter - box.width / 2f, box.yCenter - box.height / 2f);
                Vector2 newAnchorMax = new Vector2(box.xCenter + box.width / 2f, box.yCenter + box.height / 2f);
                
                if (Vector2.Distance(boxPool[i].anchorMin, newAnchorMin) > 0.001f ||
                    Vector2.Distance(boxPool[i].anchorMax, newAnchorMax) > 0.001f)
                {
                    boxPool[i].anchorMin = newAnchorMin;
                    boxPool[i].anchorMax = newAnchorMax;
                    boxPool[i].offsetMin = Vector2.zero;
                    boxPool[i].offsetMax = Vector2.zero;
                }
            }
            else if (boxPool[i].gameObject.activeSelf)
            {
                boxPool[i].gameObject.SetActive(false);
            }
        }
    }

    void OnDestroy()
    {
        if (rt != null) rt.Release();
    }
}