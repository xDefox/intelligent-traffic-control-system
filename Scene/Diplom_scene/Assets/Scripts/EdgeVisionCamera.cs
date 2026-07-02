using UnityEngine;
using Unity.InferenceEngine; // Актуальный неймспейс
using System.Collections;
using System.Collections.Generic;
using UnityEngine.Networking;

public class EdgeVisionCamera : MonoBehaviour
{
    [Header("Настройки ИИ (Inference Engine)")]
    [Tooltip("Перетащи сюда файл yolov8m.onnx")]
    public ModelAsset yoloModelAsset;
    [Tooltip("Интервал анализа (0.2f = 5 раз в секунду)")]
    public float detectionInterval = 0.2f;
    [Tooltip("Максимальное количество машин, которое физически влезает в эту зону")]
    public int maxZoneCapacity = 10;

    [Header("Сетевой шлюз (FastAPI)")]
    private string serverUrl = "http://127.0.0.1:8050/api/v1/update-congestion";

    private Camera targetCamera;
    private RenderTexture rt;
    private Worker engine; // ИСПРАВЛЕНО: используем Worker вместо старого IWorker
    private Tensor<float> inputTensor;

    // Нормализованные координаты полигона ROI (от 0.0 до 1.0)
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

        // Загружаем рантайм-модель
        Model runtimeModel = ModelLoader.Load(yoloModelAsset);

        // ИСПРАВЛЕНО: Создаем воркер напрямую через актуальный статический метод класса Worker
        // Создаем воркер напрямую через конструктор класса Worker
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

            // Переводим текстуру в тензор
            TextureConverter.ToTensor(rt, inputTensor);

            // Запуск инференса
            // В актуальном API вместо Execute используется Schedule
            engine.Schedule(inputTensor);

            // Получаем выходной тензор
            Tensor<float> outputTensor = engine.PeekOutput() as Tensor<float>;

            int detectedCars = ParseYoloOutputs(outputTensor);
            float congestionIndex = Mathf.Clamp01((float)detectedCars / maxZoneCapacity);

            StartCoroutine(SendAnalyticsToGateway(detectedCars, congestionIndex));
        }
    }

    int ParseYoloOutputs(Tensor<float> output)
    {
        // Временная заглушка, используем рандом Unity
        return UnityEngine.Random.Range(1, 5);
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