using System.Collections;
using System.Collections.Generic;
using Unity.InferenceEngine;
using UnityEngine;
using UnityEngine.Networking;

public class IntersectionVisionManager : MonoBehaviour
{
    [Header("Связь с контроллером перекрёстка")]
    [SerializeField] private IntersectionManager intersectionController;

    [Header("Уникальный ID перекрестка")]
    public string intersectionId = "intersection_1";

    [Header("Настройки ИИ")]
    public ModelAsset sharedYoloModel;
    public float globalDetectionInterval = 0.5f; // Increased from 0.2s to reduce CPU load
    public bool enableDebugLogs = false; // Set to true to see debug messages

    [Header("Камеры, контролирующие Ось X")]
    public List<EdgeVisionCamera> xAxisCameras = new List<EdgeVisionCamera>();

    [Header("Камеры, контролирующие Ось Z")]
    public List<EdgeVisionCamera> zAxisCameras = new List<EdgeVisionCamera>();

    [Header("Сетевой шлюз (FastAPI)")]
    public string telemetryUrl = "http://127.0.0.1:8050/api/v1/telemetry";

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
    private class IntersectionUpdateDTO
    {
        public string intersection_id;
        public string camera_id;
        public List<LaneDetectionDTO> lanes;
    }

    [System.Serializable]
    private class BackendResponseDTO
    {
        public string target_phase;
        public float green_duration;
        public bool cascade_applied;
    }

    void Start()
    {
        if (intersectionController == null)
        {
            intersectionController = GetComponent<IntersectionManager>();
        }

        if (sharedYoloModel == null) return;

        Model runtimeModel = ModelLoader.Load(sharedYoloModel);
        sharedEngine = new Worker(runtimeModel, BackendType.GPUCompute);
        
        // Keep original 1280x1280 as required by the model
        sharedInputTensor = new Tensor<float>(new TensorShape(1, 3, 1280, 1280));

        StartCoroutine(CentralizedInferenceLoop());
    }

    private Coroutine inferenceCoroutine;
    private bool isProcessing = false;
    
    IEnumerator CentralizedInferenceLoop()
    {
        while (true)
        {
            if (!isProcessing)
            {
                isProcessing = true;
                
                try
                {
                    // Ось X -> подходы 0, 1
                    for (int i = 0; i < xAxisCameras.Count; i++)
                    {
                        if (xAxisCameras[i] != null)
                        {
                            int carCount = ProcessSingleCamera(xAxisCameras[i]);
                            
                            if (enableDebugLogs)
                                Debug.Log($"[{intersectionId}] Camera {i} detected {carCount} cars");
                            
                            StartCoroutine(SendSingleCameraTelemetry(
                                $"{intersectionId}_approach_{i}",
                                carCount,
                                xAxisCameras[i].maxZoneCapacity
                            ));
                        }
                    }

                    // Ось Z -> подходы 2, 3
                    for (int i = 0; i < zAxisCameras.Count; i++)
                    {
                        if (zAxisCameras[i] != null)
                        {
                            int carCount = ProcessSingleCamera(zAxisCameras[i]);
                            
                            if (enableDebugLogs)
                                Debug.Log($"[{intersectionId}] Camera {i+2} detected {carCount} cars");
                            
                            StartCoroutine(SendSingleCameraTelemetry(
                                $"{intersectionId}_approach_{i + 2}",
                                carCount,
                                zAxisCameras[i].maxZoneCapacity
                            ));
                        }
                    }
                }
                catch (System.Exception ex)
                {
                    Debug.LogError($"[{intersectionId}] Inference error: {ex.Message}");
                }
                finally
                {
                    isProcessing = false;
                }
            }

            yield return new WaitForSeconds(globalDetectionInterval);
        }
    }

    int ProcessSingleCamera(EdgeVisionCamera cam)
    {
        if (cam == null) return 0;
        
        // Cache RenderTexture to avoid creating new ones
        RenderTexture cameraRt = cam.CaptureFrame();
        if (cameraRt == null) return 0;

        TextureConverter.ToTensor(cameraRt, sharedInputTensor);
        sharedEngine.Schedule(sharedInputTensor);

        Tensor<float> outputTensor = sharedEngine.PeekOutput() as Tensor<float>;
        int result = cam.UpdateDetectionsAndGetCount(outputTensor);
        
        // Release tensor immediately
        outputTensor.Dispose();
        
        return result;
    }

    IEnumerator SendSingleCameraTelemetry(string laneId, int carCount, int maxCapacity)
    {
        if (enableDebugLogs)
            Debug.Log($"[{intersectionId}] Sending telemetry for {laneId}: {carCount} cars");
        
        // Создаем DTO для ОДНОЙ камеры/подхода
        LaneDetectionDTO singleLane = new LaneDetectionDTO
        {
            lane_id = laneId,
            car_count = carCount,
            avg_speed = 0f,
            max_capacity = maxCapacity
        };

        IntersectionUpdateDTO payload = new IntersectionUpdateDTO
        {
            intersection_id = intersectionId,
            camera_id = laneId,
            lanes = new List<LaneDetectionDTO> { singleLane }
        };

        string json = JsonUtility.ToJson(payload);
        
        if (enableDebugLogs)
            Debug.Log($"[{intersectionId}] Payload: {json}");

        using (UnityWebRequest request = new UnityWebRequest(telemetryUrl, "POST"))
        {
            byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes(json);
            request.uploadHandler = new UploadHandlerRaw(bodyRaw);
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");
            request.timeout = 5; // 5 second timeout

            yield return request.SendWebRequest();

            if (request.result == UnityWebRequest.Result.Success)
            {
                string jsonResponse = request.downloadHandler.text;
                if (enableDebugLogs)
                    Debug.Log($"[{intersectionId}] Response for {laneId}: {jsonResponse}");
                
                try
                {
                    BackendResponseDTO responseData = JsonUtility.FromJson<BackendResponseDTO>(jsonResponse);
                    if (responseData != null && intersectionController != null)
                    {
                        if (enableDebugLogs)
                            Debug.Log($"[{intersectionId}] Applying command: {responseData.target_phase} for {responseData.green_duration}s");
                        
                        intersectionController.ReceiveCommandForLane(
                            laneId, 
                            responseData.target_phase,
                            responseData.green_duration
                        );
                    }
                    else if (enableDebugLogs)
                    {
                        Debug.LogWarning($"[{intersectionId}] Null response or controller for {laneId}");
                    }
                }
                catch (System.Exception ex)
                {
                    Debug.LogError($"[JSON Parse Error] {ex.Message}\nResponse: {jsonResponse}");
                }
            }
            else
            {
                Debug.LogError($"[{intersectionId}] WebRequest failed for {laneId}: {request.error}\nURL: {telemetryUrl}");
            }
        }
    }

    void OnDestroy()
    {
        sharedEngine?.Dispose();
        sharedInputTensor?.Dispose();
    }
}