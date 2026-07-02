using UnityEngine;
using System.Collections;

public class TrafficLightTimer : MonoBehaviour
{
    [Header("Сферы сигналов")]
    public MeshRenderer redLight;
    public MeshRenderer yellowLight;
    public MeshRenderer greenLight;

    [Header("Настройки таймингов (в секундах)")]
    public float redDuration = 10f;
    public float yellowDuration = 2f;
    public float greenDuration = 10f;

    // Честные цвета свечения для URP (настраиваются в инспекторе)
    [Header("Цвета свечения (HDR)")]
    [ColorUsage(true, true)] public Color redEmission = Color.red;
    [ColorUsage(true, true)] public Color yellowEmission = Color.yellow;
    [ColorUsage(true, true)] public Color greenEmission = Color.green;

    public enum LightColor { Red, Yellow, Green }
    private LightColor currentLight = LightColor.Red;

    void Start()
    {
        StartCoroutine(TrafficLightCycle());
    }

    public LightColor GetCurrentLight()
    {
        return currentLight;
    }

    // Правильный городской цикл по кругу
    IEnumerator TrafficLightCycle()
    {
        while (true)
        {
            // 1. КРАСНЫЙ
            currentLight = LightColor.Red;
            UpdateMaterials();
            yield return new WaitForSeconds(redDuration);

            // 2. ЖЁЛТЫЙ (горит перед зелёным)
            currentLight = LightColor.Yellow;
            UpdateMaterials();
            yield return new WaitForSeconds(yellowDuration);

            // 3. ЗЕЛЁНЫЙ
            currentLight = LightColor.Green;
            UpdateMaterials();
            yield return new WaitForSeconds(greenDuration);

            // 4. ЖЁЛТЫЙ (горит перед красным)
            currentLight = LightColor.Yellow;
            UpdateMaterials();
            yield return new WaitForSeconds(yellowDuration);
        }
    }

    void UpdateMaterials()
    {
        // По умолчанию гасим все лампы (делаем черными)
        SetEmission(redLight, false, Color.black);
        SetEmission(yellowLight, false, Color.black);
        SetEmission(greenLight, false, Color.black);

        // Включаем нужную лампу её родным цветом
        switch (currentLight)
        {
            case LightColor.Red:
                SetEmission(redLight, true, redEmission);
                break;
            case LightColor.Yellow:
                SetEmission(yellowLight, true, yellowEmission);
                break;
            case LightColor.Green:
                SetEmission(greenLight, true, greenEmission);
                break;
        }
    }

    void SetEmission(MeshRenderer renderer, bool isOn, Color emissionColor)
    {
        if (renderer == null) return;
        Material mat = renderer.material;

        if (isOn)
        {
            mat.EnableKeyword("_EMISSION");
            mat.SetColor("_EmissionColor", emissionColor);
        }
        else
        {
            mat.SetColor("_EmissionColor", Color.black); // Выключено — чернота
            mat.DisableKeyword("_EMISSION");
        }
    }
}