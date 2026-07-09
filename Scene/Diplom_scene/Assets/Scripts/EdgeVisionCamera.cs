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
    public bool editRoiInGame = true;
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

    void Update()
    {
        // 1. Если активирован режим редактирования — ловим мышь
        if (editRoiInGame)
        {
            HandleInGameRoiEditing();
        }

        // 2. Обновляем позиции LineRenderer каждый кадр (для плавного изменения на лету)
        UpdateRoiLines();
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

    public int UpdateDetectionsAndGetCount(Tensor<float> output)
    {
        if (output == null) return 0;

        List<BoundingBox> candidates = new List<BoundingBox>();
        output.CompleteAllPendingOperations();

        using (Tensor<float> cpuOutput = output.ReadbackAndClone() as Tensor<float>)
        {
            int numAnchors = (cpuOutput.shape[2] > cpuOutput.shape[1]) ? cpuOutput.shape[2] : cpuOutput.shape[1];
            bool isTransposed = cpuOutput.shape[1] == numAnchors;

            for (int i = 0; i < numAnchors; i++)
            {
                float maxScore = 0;
                foreach (int classId in vehicleClassIds)
                {
                    float score = isTransposed ? cpuOutput[0, i, 4 + classId] : cpuOutput[0, 4 + classId, i];
                    if (score > maxScore) maxScore = score;
                }

                if (maxScore > confidenceThreshold)
                {
                    float rawX = isTransposed ? cpuOutput[0, i, 0] : cpuOutput[0, 0, i];
                    float rawY = isTransposed ? cpuOutput[0, i, 1] : cpuOutput[0, 1, i];
                    float rawW = isTransposed ? cpuOutput[0, i, 2] : cpuOutput[0, 2, i];
                    float rawH = isTransposed ? cpuOutput[0, i, 3] : cpuOutput[0, 3, i];

                    bool modelOutputsPixels = (rawX > 1.5f);
                    float normX = modelOutputsPixels ? rawX / 1280f : rawX;
                    float normY = modelOutputsPixels ? rawY / 1280f : rawY;
                    float normW = modelOutputsPixels ? rawW / 1280f : rawW;
                    float normH = modelOutputsPixels ? rawH / 1280f : rawH;

                    normX = Mathf.Clamp01(normX);
                    normY = Mathf.Clamp01(normY);
                    normW = Mathf.Clamp01(normW);
                    normH = Mathf.Clamp01(normH);

                    float unityY = 1f - normY;
                    Vector2 centerPointNormalizedUnity = new Vector2(normX, unityY);

                    if (IsPointInPolygon(centerPointNormalizedUnity, roiPolygon))
                    {
                        candidates.Add(new BoundingBox { xCenter = normX, yCenter = unityY, width = normW, height = normH, confidence = maxScore });
                    }
                }
            }
        }

        detectedBoxes = ApplyNMS(candidates, iouThreshold);
        UpdateBoxVisuals();

        return detectedBoxes.Count;
    }

    private List<BoundingBox> ApplyNMS(List<BoundingBox> boxes, float iouThresh)
    {
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
                if (CalculateIoU(boxes[i], boxes[j]) > iouThresh)
                {
                    suppressed[j] = true;
                }
            }
        }
        return result;
    }

    private float CalculateIoU(BoundingBox boxA, BoundingBox boxB)
    {
        float leftA = boxA.xCenter - boxA.width / 2f;
        float rightA = boxA.xCenter + boxA.width / 2f;
        float topA = boxA.yCenter + boxA.height / 2f;
        float bottomA = boxA.yCenter - boxA.height / 2f;

        float leftB = boxB.xCenter - boxB.width / 2f;
        float rightB = boxB.xCenter + boxB.width / 2f;
        float topB = boxB.yCenter + boxB.height / 2f;
        float bottomB = boxB.yCenter - boxB.height / 2f;

        float interLeft = Mathf.Max(leftA, leftB);
        float interRight = Mathf.Min(rightA, rightB);
        float interTop = Mathf.Min(topA, topB);
        float interBottom = Mathf.Max(bottomA, bottomB);

        if (interLeft >= interRight || interBottom >= interTop) return 0f;

        float interArea = (interRight - interLeft) * (interTop - interBottom);
        float areaA = boxA.width * boxA.height;
        float areaB = boxB.width * boxB.height;

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
        for (int i = 0; i < boxPool.Count; i++)
        {
            if (i < detectedBoxes.Count)
            {
                boxPool[i].gameObject.SetActive(true);
                BoundingBox box = detectedBoxes[i];

                boxPool[i].anchorMin = new Vector2(box.xCenter - box.width / 2f, box.yCenter - box.height / 2f);
                boxPool[i].anchorMax = new Vector2(box.xCenter + box.width / 2f, box.yCenter + box.height / 2f);
                boxPool[i].offsetMin = Vector2.zero;
                boxPool[i].offsetMax = Vector2.zero;
            }
            else
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