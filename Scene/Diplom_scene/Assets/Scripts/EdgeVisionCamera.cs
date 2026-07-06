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
    public float confidenceThreshold = 0.4f;

    [Header("Сетевой шлюз (FastAPI)")]
    private string serverUrl = "http://127.0.0.1:8050/api/v1/update-congestion";

    private Camera targetCamera;
    private RenderTexture rt;
    private Worker engine;
    private Tensor<float> inputTensor;

    private struct BoundingBox
    {
        public Rect rect;
        public float confidence;
        public int classId;
    }

    private List<BoundingBox> detectedBoxes = new List<BoundingBox>();
    private HashSet<int> vehicleClassIds = new HashSet<int> { 2, 3, 5, 7 };

    // ТЕПЕРЬ ПОЛИГОН КАНВА СДЕЛАН ПУБЛИЧНЫМ — настраивай точки в инспекторе для каждой камеры!
    [Header("Зона детекции (ROI) для этой камеры")]
    [Tooltip("Задай 4 точки полигона в нормализованных координатах (от 0 до 1)")]
    public Vector2[] roiPolygon = new Vector2[]
    {
        new Vector2(0.35f, 0.14f),
        new Vector2(0.41f, 0.14f),
        new Vector2(0.67f, 0.80f),
        new Vector2(0.15f, 0.80f)
    };

    // 1. Добавляем компонент LineRenderer на сам объект камеры для отрисовки ROI
    private LineRenderer roiLineRenderer;

    void Start()
    {
        targetCamera = GetComponent<Camera>();
        rt = new RenderTexture(640, 640, 24);
        inputTensor = new Tensor<float>(new TensorShape(1, 3, 640, 640));

        // Настраиваем LineRenderer для отрисовки 2D-полигона в пространстве камеры
        roiLineRenderer = gameObject.AddComponent<LineRenderer>();
        roiLineRenderer.useWorldSpace = false; // РИСУЕМ В ЛОКАЛЬНЫХ КООРДИНАТАХ КАМЕРЫ
        roiLineRenderer.positionCount = roiPolygon.Length;
        roiLineRenderer.loop = true;
        roiLineRenderer.startWidth = 0.02f; // Тонкие линии
        roiLineRenderer.endWidth = 0.02f;

        // Простейший unlit материал, чтобы линия светилась (зеленым/желтым)
        roiLineRenderer.material = new Material(Shader.Find("Sprites/Default"));
        roiLineRenderer.startColor = Color.yellow;
        roiLineRenderer.endColor = Color.yellow;

        // Переводим нормализованные 2D координаты ROI (0..1) в локальные координаты перед камерой
        for (int i = 0; i < roiPolygon.Length; i++)
        {
            // Проецируем точку на плоскость прямо перед камерой (например, на расстоянии 1 метра)
            // В viewport-координатах: X и Y (0..1), Z - расстояние от линзы
            Vector3 viewportPoint = new Vector3(roiPolygon[i].x, roiPolygon[i].y, 1.0f);
            Vector3 localPoint = targetCamera.ViewportToWorldPoint(viewportPoint);
            // Переводим в локальное пространство объекта камеры
            localPoint = targetCamera.transform.InverseTransformPoint(localPoint);

            roiLineRenderer.SetPosition(i, localPoint);
        }

        if (yoloModelAsset != null)
        {
            Model runtimeModel = ModelLoader.Load(yoloModelAsset);
            engine = new Worker(runtimeModel, BackendType.GPUCompute);
            StartCoroutine(InferenceLoopCo());
        }
    }

    // МЕТОД OnGUI() МЫ ПОЛНОСТЬЮ УДАЛЯЕМ

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

            Tensor<float> outputTensor = engine.PeekOutput() as Tensor<float>;
            ParseYoloOutputs(outputTensor);

            int detectedCars = detectedBoxes.Count;
            float congestionIndex = Mathf.Clamp01((float)detectedCars / maxZoneCapacity);

            // Имя объекта (например, Camera_North) уйдет на бэкенд как camera_id
            StartCoroutine(SendAnalyticsToGateway(gameObject.name, detectedCars, congestionIndex));
        }
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

                Vector2 centerPointNormalized = new Vector2(xCenter / 640f, yCenter / 640f);

                if (IsPointInPolygon(centerPointNormalized, roiPolygon))
                {
                    float screenX = (xCenter - width / 2f) / 640f * Screen.width;
                    float screenY = (1f - (yCenter + height / 2f) / 640f) * Screen.height;
                    float screenW = width / 640f * Screen.width;
                    float screenH = height / 640f * Screen.height;

                    detectedBoxes.Add(new BoundingBox
                    {
                        rect = new Rect(screenX, screenY, screenW, screenH),
                        confidence = maxScore,
                        classId = bestClassId
                    });
                }
            }
        }
        cpuOutput?.Dispose();
    }

    void DrawScreenRect(Rect rect, Color color, float thickness)
    {
        Texture2D lineTex = new Texture2D(1, 1);
        lineTex.SetPixel(0, 0, color); lineTex.Apply();
        GUI.DrawTexture(new Rect(rect.x, rect.y, rect.width, thickness), lineTex);
        GUI.DrawTexture(new Rect(rect.x, rect.y + rect.height - thickness, rect.width, thickness), lineTex);
        GUI.DrawTexture(new Rect(rect.x, rect.y, thickness, rect.height), lineTex);
        GUI.DrawTexture(new Rect(rect.x + rect.width - thickness, rect.y, thickness, rect.height), lineTex);
    }

    void DrawLine(Vector2 start, Vector2 end, Color color, float thickness)
    {
        Vector2 d = end - start;
        float a = Mathf.Atan2(d.y, d.x) * Mathf.Rad2Deg;
        GUIUtility.RotateAroundPivot(a, start);
        Texture2D tex = new Texture2D(1, 1);
        tex.SetPixel(0, 0, color); tex.Apply();
        GUI.DrawTexture(new Rect(start.x, start.y, d.magnitude, thickness), tex);
        GUIUtility.RotateAroundPivot(-a, start);
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

    void OnDestroy()
    {
        engine?.Dispose();
        inputTensor?.Dispose();
        if (rt != null) rt.Release();
    }
}