using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

public class CameraStreamer : MonoBehaviour
{
    // Адрес нашего Python-сервера
    private string serverUrl = "http://127.0.0.1:8050/api/v1/upload-frame";

    // Интервал отправки кадров (например, 5 раз в секунду, чтобы не спамить)
    public float streamInterval = 0.2f;

    private Camera targetCamera;

    void Start()
    {
        targetCamera = GetComponent<Camera>();
        StartCoroutine(StreamFramesCo());
    }

    IEnumerator StreamFramesCo()
    {
        while (true)
        {
            yield return new WaitForSeconds(streamInterval);
            yield return new WaitForEndOfFrame(); // Ждем, пока кадр полностью отрендерится

            // Создаем текстуру по размерам экрана
            RenderTexture rt = new RenderTexture(Screen.width, Screen.height, 24);
            targetCamera.targetTexture = rt;
            Texture2D screenShot = new Texture2D(Screen.width, Screen.height, TextureFormat.RGB24, false);

            targetCamera.Render();
            RenderTexture.active = rt;
            screenShot.ReadPixels(new Rect(0, 0, Screen.width, Screen.height), 0, 0);
            screenShot.Apply();

            // Сбрасываем текстуры, чтобы камера продолжала показывать картинку на экран
            targetCamera.targetTexture = null;
            RenderTexture.active = null;
            Destroy(rt);

            // Сжимаем в JPEG (минимальный размер для передачи по сети)
            byte[] bytes = screenShot.EncodeToJPG(75);
            Destroy(screenShot);

            // Отправляем байты на Python через HTTP POST
            StartCoroutine(SendFrameToServer(bytes));
        }
    }

    IEnumerator SendFrameToServer(byte[] bytes)
    {
        WWWForm form = new WWWForm();
        form.AddBinaryData("image", bytes, "frame.jpg", "image/jpeg");

        using (UnityWebRequest www = UnityWebRequest.Post(serverUrl, form))
        {
            yield return www.SendWebRequest();

            if (www.result != UnityWebRequest.Result.Success)
            {
                Debug.LogWarning("Ошибка отправки кадра: " + www.error);
            }
            else
            {
                // Тут в будущем мы будем принимать ответ от Python (команды светофору!)
                // string jsonResponse = www.downloadHandler.text;
            }
        }
    }
}