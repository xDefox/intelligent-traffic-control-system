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

    [Header("Уникальный ID перекрестка (автоматически определяется из имени GameObject)")]
    public string intersectionId = "";
    
    [Header("Автоматически генерировать ID из имени GameObject")]
    public bool autoGenerateIdFromName = true;

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
        public bool emergency_vehicle_detected;
        public string emergency_approach;
    }

    // Кэш камер для batch inference
    private List<EdgeVisionCamera> allCameras = new List<EdgeVisionCamera>();
    private int[] cameraResults;

    void Start()
    {
        // Автоматически определяем ID перекрёстка из имени GameObject
        if (autoGenerateIdFromName || string.IsNullOrEmpty(intersectionId))
        {
            intersectionId = ExtractIntersectionIdFromName(gameObject.name);
        }
        
        Debug.Log($"[{gameObject.name}] 🚦 IntersectionVisionManager запущен: ID={intersectionId}, Камер X={xAxisCameras.Count}, Z={zAxisCameras.Count}, Всего={xAxisCameras.Count + zAxisCameras.Count}");
        
        if (xAxisCameras.Count == 0 && zAxisCameras.Count == 0)
        {
            Debug.LogError($"[{gameObject.name}] ❌ ОШИБКА: Не назначены камеры! X={xAxisCameras.Count}, Z={zAxisCameras.Count}. Хотя бы один список камер должен быть заполнен!");
            return; // Не запускаем без камер вообще
        }
        
        if (xAxisCameras.Count == 0 || zAxisCameras.Count == 0)
        {
            Debug.Log($"[{gameObject.name}] ℹ️ Режим одного направления: X={xAxisCameras.Count}, Z={zAxisCameras.Count}. Система будет работать с доступными камерами.");
        }
        
        if (intersectionController == null)
        {
            intersectionController = GetComponent<IntersectionManager>();
        }

        if (sharedYoloModel == null) 
        {
            Debug.LogWarning($"[{gameObject.name}] ⚠️ YOLO модель не назначена!");
            return;
        }

        Model runtimeModel = ModelLoader.Load(sharedYoloModel);
        sharedEngine = new Worker(runtimeModel, BackendType.GPUCompute);
        sharedInputTensor = new Tensor<float>(new TensorShape(1, 3, 1280, 1280));

        // Собираем все камеры в единый список для batch-обработки
        allCameras.Clear();
        allCameras.AddRange(xAxisCameras);
        allCameras.AddRange(zAxisCameras);
        cameraResults = new int[allCameras.Count];

        Debug.Log($"[{gameObject.name}] ✅ Запуск inference loop: {allCameras.Count} камер, ID={intersectionId}");
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

        // Debug: показываем что детектирует каждая камера
        if (enableDebugLogs)
        {
            for (int i = 0; i < allCameras.Count; i++)
            {
                string axis = i < xAxisCameras.Count ? "X" : "Z";
                int camIndex = i < xAxisCameras.Count ? i : i - xAxisCameras.Count;
                Debug.Log($"[{intersectionId}] Камера {i} ({axis}-{camIndex}): {cameraResults[i]} машин");
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
            
            // Правильный индексация: X-камеры получают approach_0,1; Z-камеры получают approach_2,3
            int approachIndex;
            if (i < xAxisCameras.Count)
            {
                approachIndex = i;  // X-axis: 0, 1
            }
            else
            {
                approachIndex = 2 + (i - xAxisCameras.Count);  // Z-axis: 2, 3 (всегда начинается с 2)
            }
            
            string laneId = $"{intersectionId}_approach_{approachIndex}";
            string axisType = i < xAxisCameras.Count ? "X" : "Z";

            // Определяем подход для emergency (approach_0,1 = X; approach_2,3 = Z)
            string emergencyApproach = $"approach_{approachIndex}";
            
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
                },
                emergency_vehicle_detected = allCameras[i].emergencyDetected,
                emergency_approach = allCameras[i].emergencyDetected ? emergencyApproach : null
            };
            batch.cameras.Add(cam);
            
            if (allCameras[i].emergencyDetected)
            {
                Debug.Log($"[{intersectionId}] 🚨 Спецтранспорт на камере {i} ({axisType}-ось) → {laneId}, approach={emergencyApproach}");
            }
            else
            {
                Debug.Log($"[{intersectionId}] Камера {i} ({axisType}-ось) → {laneId}");
            }
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
                        // Проверяем emergency коридор
                        bool emergencyActive = responseData.emergency_corridor_active;
                        
                        foreach (var resp in responseData.responses)
                        {
                            if (enableDebugLogs)
                                Debug.Log($"[{intersectionId}] Command {resp.camera_id}: {resp.target_phase} ({resp.green_duration}s, emergency={resp.emergency_override})");

                            intersectionController.ReceiveCommandForLane(
                                resp.camera_id,
                                resp.target_phase,
                                resp.green_duration
                            );
                        }
                        
                        // Если emergency активен — сообщаем контроллеру
                        if (emergencyActive && intersectionController != null)
                        {
                            intersectionController.SetEmergencyMode(true, responseData.emergency_corridor_phase);
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

    /// <summary>
    /// Автоматически извлекает ID перекрёстка из имени GameObject.
    /// Поддерживает форматы: "intersection_1", "Intersection 2", "TrafficLight_3", etc.
    /// </summary>
    private string ExtractIntersectionIdFromName(string gameObjectName)
    {
        // Приводим к нижнему регистру для универсальности
        string name = gameObjectName.ToLower();
        
        // Извлекаем номер из конца имени (например, "manager2" -> "2")
        System.Text.RegularExpressions.Regex numberRegex = new System.Text.RegularExpressions.Regex(@"(\d+)$");
        var numberMatch = numberRegex.Match(name.Replace(" ", ""));
        
        string number = "1"; // По умолчанию номер 1
        if (numberMatch.Success)
        {
            number = numberMatch.Groups[1].Value;
        }
        
        // Проверяем содержит ли имя слово intersection/traffic/crossroad
        System.Text.RegularExpressions.Regex typeRegex = new System.Text.RegularExpressions.Regex(@"(intersection|traffic|crossroad)");
        var typeMatch = typeRegex.Match(name);
        
        if (typeMatch.Success)
        {
            // Если содержит - используем этот тип и номер
            string type = typeMatch.Groups[1].Value;
            return $"{type}_{number}";
        }
        
        // Если паттерн не найден, используем имя как есть, заменив пробелы на _
        return "intersection_" + gameObjectName.Replace(" ", "_").ToLower();
    }
    
    // Вспомогательные классы

    [System.Serializable]
    private class SingleResponseDTO
    {
        public string camera_id;
        public string target_phase;
        public float green_duration;
        public bool emergency_override;
    }

    [System.Serializable]
    private class BatchResponseDTO
    {
        public string type;
        public List<SingleResponseDTO> responses;
        public bool emergency_corridor_active;
        public string emergency_corridor_phase;
    }
}