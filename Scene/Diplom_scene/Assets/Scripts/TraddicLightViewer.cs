using UnityEngine;

public class TrafficLightViewer : MonoBehaviour
{
    [Header("Сферы сигналов")]
    public MeshRenderer redLight;
    public MeshRenderer yellowLight;
    public MeshRenderer greenLight;

    [Header("Цвета свечения (HDR)")]
    [ColorUsage(true, true)] public Color redEmission = Color.red;
    [ColorUsage(true, true)] public Color yellowEmission = Color.yellow;
    [ColorUsage(true, true)] public Color greenEmission = Color.green;

    public enum LightColor { Red, Yellow, Green }
    private LightColor currentLight = LightColor.Red;

    public LightColor GetCurrentLight() => currentLight;

    // Этот метод теперь вызывается из главного менеджера перекрестка
    public void SwitchToColor(LightColor newColor)
    {
        currentLight = newColor;

        // Гасим все
        SetEmission(redLight, false, Color.black);
        SetEmission(yellowLight, false, Color.black);
        SetEmission(greenLight, false, Color.black);

        // Зажигаем нужный
        switch (currentLight)
        {
            case LightColor.Red: SetEmission(redLight, true, redEmission); break;
            case LightColor.Yellow: SetEmission(yellowLight, true, yellowEmission); break;
            case LightColor.Green: SetEmission(greenLight, true, greenEmission); break;
        }
    }

    private void SetEmission(MeshRenderer renderer, bool isOn, Color color)
    {
        if (renderer == null) return;
        Material mat = renderer.material;
        if (isOn)
        {
            mat.EnableKeyword("_EMISSION");
            mat.SetColor("_EmissionColor", color);
        }
        else
        {
            mat.SetColor("_EmissionColor", Color.black);
            mat.DisableKeyword("_EMISSION");
        }
    }
}