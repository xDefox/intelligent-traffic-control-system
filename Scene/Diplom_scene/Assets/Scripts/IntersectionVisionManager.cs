using System.Collections;
using System.Collections.Generic;
using Unity.InferenceEngine;
using UnityEngine;
using UnityEngine.Networking;

public class IntersectionVisionManager : MonoBehaviour
{
    [Header("Уникальный ID перекрестка (строка для соответствия бэкенду)")]
    public string intersectionId = "intersection_1";

    [Header("Настройки ИИ (Один на весь перекресток)")]
    public ModelAsset sharedYoloModel;
    public float globalDetectionInterval = 0.2f;

    [Header("Привязка камер по направлениям")]
    public EdgeVisionCamera XCamera;
    public EdgeVisionCamera ZCamera;
    public EdgeVisionCamera xCamera;
    public EdgeVisionCamera zCamera;

    [Header("Сетевой шлюз (FastAPI)")]
    public string telemetryUrl = "http://127.0.0.1:8050/api/v1/telemetry";

    private Worker sharedEngine;
    private Tensor<float> sharedInputTensor;

    // Вложенные DTO-структуры, которые на 100% повторяют Pydantic-схему бэка
    [System.Serializable]
    private class LaneDetectionDTO
    {
        public string lane_id;
        public int car_count;
        public float avg_speed;
    }

    [System.Serializable]
    private class IntersectionUpdateDTO
    {
        public string intersection_id;
        public string camera_id;
        public List<LaneDetectionDTO> lanes;
    }

    void Start()
    {
        if (sharedYoloModel == null)
        {
            Debug.LogError($"[{gameObject.name}] Не задана YOLO модель в IntersectionVisionManager!");
            return;
        }

        // Инициализируем ИИ ОДИН раз
        Model runtimeModel = ModelLoader.Load(sharedYoloModel);
        sharedEngine = new Worker(runtimeModel, BackendType.GPUCompute);
        sharedInputTensor = new Tensor<float>(new TensorShape(1, 3, 1280, 1280));

        StartCoroutine(CentralizedInferenceLoop());
    }

    IEnumerator CentralizedInferenceLoop()
    {
        while (true)
        {
            // Создаем динамический список для полос
            List<LaneDetectionDTO> lanesList = new List<LaneDetectionDTO>();

            // Опрашиваем камеры и сразу пакуем результаты в новый DTO формат
            // Имена "lane_north", "lane_west" и т.д. заставят наш traffic_brain правильно распределять фазы
            if (XCamera != null)
            {
                lanesList.Add(new LaneDetectionDTO
                {
                    lane_id = "lane_north",
                    car_count = ProcessSingleCamera(XCamera),
                    avg_speed = 0f // Пока заглушка, если скорость не трекается
                });
            }
            if (ZCamera != null)
            {
                lanesList.Add(new LaneDetectionDTO
                {
                    lane_id = "lane_south",
                    car_count = ProcessSingleCamera(ZCamera),
                    avg_speed = 0f
                });
            }
            if (xCamera != null)
            {
                lanesList.Add(new LaneDetectionDTO
                {
                    lane_id = "lane_east",
                    car_count = ProcessSingleCamera(xCamera),
                    avg_speed = 0f
                });
            }
            if (zCamera != null)
            {
                lanesList.Add(new LaneDetectionDTO
                {
                    lane_id = "lane_west",
                    car_count = ProcessSingleCamera(zCamera),
                    avg_speed = 0f
                });
            }

            // Отправляем агрегированные данные новым пакетом
            if (lanesList.Count > 0)
            {
                StartCoroutine(SendCombinedTelemetry(lanesList));
            }

            yield return new WaitForSeconds(globalDetectionInterval);
        }
    }

    int ProcessSingleCamera(EdgeVisionCamera cam)
    {
        if (cam == null) return 0;

        RenderTexture cameraRt = cam.CaptureFrame();
        if (cameraRt == null) return 0;

        TextureConverter.ToTensor(cameraRt, sharedInputTensor);

        sharedEngine.Schedule(sharedInputTensor);
        Tensor<float> outputTensor = sharedEngine.PeekOutput() as Tensor<float>;

        int carsCount = cam.UpdateDetectionsAndGetCount(outputTensor);
        return carsCount;
    }

    IEnumerator SendCombinedTelemetry(List<LaneDetectionDTO> lanes)
    {
        // Формируем финальный пакет
        IntersectionUpdateDTO payload = new IntersectionUpdateDTO
        {
            intersection_id = intersectionId,
            camera_id = "central_manager", // Так как этот скрипт агрегирует данные централизованно
            lanes = lanes
        };

        string json = JsonUtility.ToJson(payload);

        using (UnityWebRequest request = new UnityWebRequest(telemetryUrl, "POST"))
        {
            byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes(json);
            request.uploadHandler = new UploadHandlerRaw(bodyRaw);
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");

            yield return request.SendWebRequest();

            if (request.result != UnityWebRequest.Result.Success)
            {
                Debug.LogWarning($"[API Error] Не удалось отправить телеметрию: {request.error}");
            }
            else
            {
                // Лог ответа от FastAPI — теперь он вернет корректную фазу
                Debug.Log($"[API Success] Ответ сервера: {request.downloadHandler.text}");
            }
        }
    }

    void OnDestroy()
    {
        sharedEngine?.Dispose();
        sharedInputTensor?.Dispose();
    }
}