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
    
    // Cache materials to avoid creating instances
    private Material redMat;
    private Material yellowMat;
    private Material greenMat;

    public LightColor GetCurrentLight() => currentLight;

    void Start()
    {
        // Cache materials once at startup
        if (redLight != null) redMat = redLight.material;
        if (yellowLight != null) yellowMat = yellowLight.material;
        if (greenLight != null) greenMat = greenLight.material;

        // Применяем стартовый цвет принудительно: currentLight по умолчанию Red,
        // а SwitchToColor() выходит раньше при том же цвете, поэтому без этого
        // вызова красный сигнал не зажигался при старте сцены.
        ApplyColor(currentLight);
    }

    // Этот метод теперь вызывается из главного менеджера перекрестка
    public void SwitchToColor(LightColor newColor)
    {
        if (newColor == currentLight) return; // Skip if same color

        currentLight = newColor;
        ApplyColor(currentLight);
    }

    private void ApplyColor(LightColor color)
    {
        // Гасим все
        SetEmission(redMat, false, Color.black);
        SetEmission(yellowMat, false, Color.black);
        SetEmission(greenMat, false, Color.black);

        // Зажигаем нужный
        switch (color)
        {
            case LightColor.Red: SetEmission(redMat, true, redEmission); break;
            case LightColor.Yellow: SetEmission(yellowMat, true, yellowEmission); break;
            case LightColor.Green: SetEmission(greenMat, true, greenEmission); break;
        }
    }

    private void SetEmission(Material mat, bool isOn, Color color)
    {
        if (mat == null) return;
        
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
