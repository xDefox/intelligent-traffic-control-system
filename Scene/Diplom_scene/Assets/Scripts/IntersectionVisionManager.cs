using System.Collections;
using System.Collections.Generic;
using Unity.InferenceEngine;
using UnityEngine;
using UnityEngine.Networking;

public class IntersectionVisionManager : MonoBehaviour
{
    [Header("Связь с контроллером светофоров")]
    [SerializeField] private IntersectionManager trafficLightController;

    [Header("Уникальный ID перекрестка")]
    public string intersectionId = "intersection_1";

    [Header("Настройки ИИ")]
    public ModelAsset sharedYoloModel;
    public float globalDetectionInterval = 0.2f;

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
        public bool cascade_applied;
    }

    void Start()
    {
        if (trafficLightController == null)
        {
            trafficLightController = GetComponent<IntersectionManager>();
        }

        if (sharedYoloModel == null) return;

        Model runtimeModel = ModelLoader.Load(sharedYoloModel);
        sharedEngine = new Worker(runtimeModel, BackendType.GPUCompute);
        sharedInputTensor = new Tensor<float>(new TensorShape(1, 3, 1280, 1280));

        StartCoroutine(CentralizedInferenceLoop());
    }

    IEnumerator CentralizedInferenceLoop()
    {
        while (true)
        {
            List<LaneDetectionDTO> lanesList = new List<LaneDetectionDTO>();

            // Опрашиваем все назначенные камеры для оси X
            for (int i = 0; i < xAxisCameras.Count; i++)
            {
                if (xAxisCameras[i] != null)
                {
                    lanesList.Add(new LaneDetectionDTO
                    {
                        lane_id = $"lane_{intersectionId}_X_{i}",
                        car_count = ProcessSingleCamera(xAxisCameras[i]),
                        avg_speed = 0f
                    });
                }
            }

            // Опрашиваем все назначенные камеры для оси Z
            for (int i = 0; i < zAxisCameras.Count; i++)
            {
                if (zAxisCameras[i] != null)
                {
                    lanesList.Add(new LaneDetectionDTO
                    {
                        lane_id = $"lane_{intersectionId}_Z_{i}",
                        car_count = ProcessSingleCamera(zAxisCameras[i]),
                        avg_speed = 0f
                    });
                }
            }

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
        return cam.UpdateDetectionsAndGetCount(outputTensor);
    }

    IEnumerator SendCombinedTelemetry(List<LaneDetectionDTO> lanes)
    {
        IntersectionUpdateDTO payload = new IntersectionUpdateDTO
        {
            intersection_id = intersectionId,
            camera_id = "central_manager",
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

            if (request.result == UnityWebRequest.Result.Success)
            {
                string jsonResponse = request.downloadHandler.text;
                try
                {
                    BackendResponseDTO responseData = JsonUtility.FromJson<BackendResponseDTO>(jsonResponse);
                    if (responseData != null && trafficLightController != null)
                    {
                        trafficLightController.ReceiveCommandFromPython(responseData.target_phase);
                    }
                }
                catch (System.Exception ex)
                {
                    Debug.LogError($"[JSON Parse Error] {ex.Message}");
                }
            }
        }
    }

    void OnDestroy()
    {
        sharedEngine?.Dispose();
        sharedInputTensor?.Dispose();
    }
}