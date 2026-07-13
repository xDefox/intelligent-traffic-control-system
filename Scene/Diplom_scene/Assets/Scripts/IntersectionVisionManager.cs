using System.Collections;
using System.Collections.Generic;
using Unity.InferenceEngine;
using UnityEngine;
using UnityEngine.Networking;
using System;

public class IntersectionVisionManager : MonoBehaviour
{
    [Header("Связь с контроллером перекрёстка")]
    [SerializeField] private IntersectionManager intersectionController;

    [Header("Уникальный ID перекрестка")]
    public string intersectionId = "intersection_1";

    [Header("Настройки ИИ")]
    public ModelAsset sharedYoloModel;
    public float globalDetectionInterval = 0.5f;
    public bool enableDebugLogs = false;

    [Header("Камеры, контролирующие Ось X")]
    public List<EdgeVisionCamera> xAxisCameras = new List<EdgeVisionCamera>();

    [Header("Камеры, контролирующие Ось Z")]
    public List<EdgeVisionCamera> zAxisCameras = new List<EdgeVisionCamera>();

    [Header("Сетевой шлюз (batch HTTP)")]
    public string batchTelemetryUrl = "http://127.0.0.1:8050/api/v1/telemetry/batch";

    private Worker sharedEngine;
    private Tensor<float> sharedInputTensor;

    [System.Serializable]
    private class LaneDetectionDTO
    {
        public string lane_id;
        public int car_count;
        public float avg_speed;
        public int max_capacity;
    }

    [System.Serializable]
    private class BatchTelemetryDTO
    {
        public string intersection_id;
        public List<CameraTelemetryDTO> cameras;
    }

    [System.Serializable]
    private class CameraTelemetryDTO
    {
        public string camera_id;
        public List<LaneDetectionDTO> lanes;
    }

    // Кэш камер для batch inference
    private List<EdgeVisionCamera> allCameras = new List<EdgeVisionCamera>();
    private int[] cameraResults;

    void Start()
    {
        if (intersectionController == null)
        {
            intersectionController = GetComponent<IntersectionManager>();
        }

        if (sharedYoloModel == null) return;

        Model runtimeModel = ModelLoader.Load(sharedYoloModel);
        sharedEngine = new Worker(runtimeModel, BackendType.GPUCompute);
        sharedInputTensor = new Tensor<float>(new TensorShape(1, 3, 1280, 1280));

        // Собираем все камеры в единый список для batch-обработки
        allCameras.Clear();
        allCameras.AddRange(xAxisCameras);
        allCameras.AddRange(zAxisCameras);
        cameraResults = new int[allCameras.Count];

        StartCoroutine(CentralizedInferenceLoop());
    }

    IEnumerator CentralizedInferenceLoop()
    {
        while (true)
        {
            // Шаг 1: Захватываем кадры со ВСЕХ камер (без инференса)
            List<RenderTexture> capturedRTs = new List<RenderTexture>();
            for (int i = 0; i < allCameras.Count; i++)
            {
                if (allCameras[i] != null)
                {
                    RenderTexture rt = allCameras[i].CaptureFrame();
                    capturedRTs.Add(rt);
                }
                else
                {
                    capturedRTs.Add(null);
                }
            }

            // Шаг 2: Последовательный инференс для каждой камеры.
            // ВАЖНО: PeekOutput() возвращает ОДИН И ТОТ ЖЕ тензор для всех Schedule.
            // Нельзя сделать Schedule для всех камер сразу, а потом читать — 
            // после первого Dispose() остальные упадут с NullReferenceException.
            // Решение: Schedule → Readback → Dispose → следующая камера.
            for (int i = 0; i < capturedRTs.Count; i++)
            {
                if (capturedRTs[i] == null) continue;

                try
                {
                    // Schedule для одной камеры
                    TextureConverter.ToTensor(capturedRTs[i], sharedInputTensor);
                    sharedEngine.Schedule(sharedInputTensor);

                    // Ждём завершения инференса (yield не нужен — PeekOutput блокирует)
                    Tensor<float> outputTensor = sharedEngine.PeekOutput() as Tensor<float>;
                    if (outputTensor != null)
                    {
                        int count = allCameras[i].UpdateDetectionsAndGetCount(outputTensor);
                        cameraResults[i] = count;
                        outputTensor.Dispose();
                    }
                    else
                    {
                        cameraResults[i] = 0;
                    }
                }
                catch (Exception ex)
                {
                    Debug.LogError($"[{intersectionId}] Inference error camera {i}: {ex.Message}");
                    cameraResults[i] = 0;
                }
            }

            // Шаг 3: Отправляем batch телеметрию (1 POST вместо 4)
            yield return StartCoroutine(SendBatchTelemetry());

            // Освобождаем RenderTexture у камер
            for (int i = 0; i < allCameras.Count; i++)
            {
                if (allCameras[i] != null)
                {
                    allCameras[i].ReleaseFrame();
                }
            }

            yield return new WaitForSeconds(globalDetectionInterval);
        }
    }

    IEnumerator SendBatchTelemetry()
    {
        BatchTelemetryDTO batch = new BatchTelemetryDTO
        {
            intersection_id = intersectionId,
            cameras = new List<CameraTelemetryDTO>()
        };

        for (int i = 0; i < allCameras.Count; i++)
        {
            if (allCameras[i] == null) continue;
            string laneId = $"{intersectionId}_approach_{i}";

            CameraTelemetryDTO cam = new CameraTelemetryDTO
            {
                camera_id = laneId,
                lanes = new List<LaneDetectionDTO>
                {
                    new LaneDetectionDTO
                    {
                        lane_id = laneId,
                        car_count = cameraResults[i],
                        avg_speed = 0f,
                        max_capacity = allCameras[i].maxZoneCapacity
                    }
                }
            };
            batch.cameras.Add(cam);
        }

        if (batch.cameras.Count == 0) yield break;

        string json = JsonUtility.ToJson(batch);

        if (enableDebugLogs)
            Debug.Log($"[{intersectionId}] Batch telemetry: {json}");

        using (UnityWebRequest request = new UnityWebRequest(batchTelemetryUrl, "POST"))
        {
            byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes(json);
            request.uploadHandler = new UploadHandlerRaw(bodyRaw);
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");
            request.timeout = 5;

            yield return request.SendWebRequest();

            if (request.result == UnityWebRequest.Result.Success)
            {
                string jsonResponse = request.downloadHandler.text;
                if (enableDebugLogs)
                    Debug.Log($"[{intersectionId}] Batch response: {jsonResponse}");

                try
                {
                    // Парсим batch-ответ: {"type":"batch_response","responses":[{"camera_id":"...","target_phase":"GREEN","green_duration":10.0},...]}
                    BatchResponseDTO responseData = JsonUtility.FromJson<BatchResponseDTO>(jsonResponse);
                    if (responseData?.responses != null && intersectionController != null)
                    {
                        foreach (var resp in responseData.responses)
                        {
                            if (enableDebugLogs)
                                Debug.Log($"[{intersectionId}] Command {resp.camera_id}: {resp.target_phase} ({resp.green_duration}s)");

                            intersectionController.ReceiveCommandForLane(
                                resp.camera_id,
                                resp.target_phase,
                                resp.green_duration
                            );
                        }
                    }
                }
                catch (Exception ex)
                {
                    Debug.LogError($"[{intersectionId}] Batch parse error: {ex.Message}\nResponse: {jsonResponse}");
                }
            }
            else
            {
                Debug.LogError($"[{intersectionId}] Batch request failed: {request.error}");
            }
        }
    }

    void OnDestroy()
    {
        sharedEngine?.Dispose();
        sharedInputTensor?.Dispose();

        if (allCameras != null)
        {
            foreach (var cam in allCameras)
            {
                if (cam != null) cam.ReleaseFrame();
            }
        }
    }

    // Вспомогательные классы

    [System.Serializable]
    private class BatchResponseDTO
    {
        public string type;
        public List<SingleResponseDTO> responses;
    }

    [System.Serializable]
    private class SingleResponseDTO
    {
        public string camera_id;
        public string target_phase;
        public float green_duration;
    }
}