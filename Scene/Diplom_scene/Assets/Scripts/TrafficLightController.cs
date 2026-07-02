using UnityEngine;

public class TrafficLightController : MonoBehaviour
{
    public MeshRenderer redLight;
    public MeshRenderer yellowLight;
    public MeshRenderer greenLight;

    [ColorUsage(true, true)] public Color activeColor; // Яркий свет для Emission
    private Color darkColor = Color.black;             // Выключенный свет

    public enum LightState { Red, Yellow, Green }
    private LightState currentState = LightState.Red;

    void Start()
    {
        SetState(LightState.Red); // По умолчанию горит красный
    }

    public void SetState(LightState newState)
    {
        currentState = newState;

        // Выключаем все
        ToggleEmission(redLight, false);
        ToggleEmission(yellowLight, false);
        ToggleEmission(greenLight, false);

        // Включаем нужный
        switch (currentState)
        {
            case LightState.Red:
                ToggleEmission(redLight, true);
                break;
            case LightState.Yellow:
                ToggleEmission(yellowLight, true);
                break;
            case LightState.Green:
                ToggleEmission(greenLight, true);
                break;
        }
    }

    private void ToggleEmission(MeshRenderer renderer, bool isOn)
    {
        if (renderer == null) return;
        Material mat = renderer.material;
        if (isOn)
        {
            mat.EnableKeyword("_EMISSION");
            mat.SetColor("_EmissionColor", activeColor);
        }
        else
        {
            mat.DisableKeyword("_EMISSION");
            mat.SetColor("_EmissionColor", darkColor);
        }
    }

    // Этот метод мы вызовем, когда настроим связь с Python (например, через сокеты или HTTP)
    public void ReceiveCommandFromPython(string stateName)
    {
        if (stateName == "GREEN") SetState(LightState.Green);
        if (stateName == "YELLOW") SetState(LightState.Yellow);
        if (stateName == "RED") SetState(LightState.Red);
    }
}