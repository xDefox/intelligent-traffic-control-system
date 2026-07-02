using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

public class CameraStreamer : MonoBehaviour
{
    private string serverUrl = "http://127.0.0.1:8050/api/v1/upload-frame";

    [Tooltip("Интервал отправки (0.1f = 10 кадров в секунду. Для адаптивного UTC-UX Fusion этого за глаза)")]
    public float streamInterval = 0.1f;

    private Texture2D screenshot;
    private bool isSending = false;

    void Start()
    {
        // Создаем текстуру один раз под разрешение экрана (или фиксированное)
        // Чтобы не грузить CPU, лучше запустить игру в окне 1280x720
        screenshot = new Texture2D(Screen.width, Screen.height, TextureFormat.RGB24, false);
        StartCoroutine(StreamFramesCo());
    }

    IEnumerator StreamFramesCo()
    {
        while (true)
        {
            yield return new WaitForSeconds(streamInterval);
            yield return new WaitForEndOfFrame(); // Ждем, когда кадр полностью отрисуется на экране

            // Читаем пиксели прямо с экрана — это НЕ ломает рендер самой камеры
            screenshot.ReadPixels(new Rect(0, 0, Screen.width, Screen.height), 0, 0);
            screenshot.Apply();

            // Сжимаем в JPEG (качество 60-70 оптимально для YOLO)
            byte[] bytes = screenshot.EncodeToJPG(65);

            // Отправляем асинхронно, чтобы не вешать Unity
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
            // Нам пока не важен ответ, главное — доставить кадр до шлюза
        }
    }

    void OnDestroy()
    {
        if (screenshot != null) Destroy(screenshot);
    }
}