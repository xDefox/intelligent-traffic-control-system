using UnityEngine;
using System.Collections;
using System.Collections.Generic;

public class IntersectionManager : MonoBehaviour
{
    [Header("Светофоры Главной дороги (их может быть 2)")]
    public List<TrafficLightViewer> mainRoadLights;

    [Header("Светофоры Боковой дороги")]
    public List<TrafficLightViewer> sideRoadLights;

    [Header("Настройки фаз (в секундах)")]
    public float mainGreenDuration = 12f;
    public float yellowDuration = 2f;
    public float sideGreenDuration = 8f;

    void Start()
    {
        StartCoroutine(IntersectionCycle());
    }

    IEnumerator IntersectionCycle()
    {
        while (true)
        {
            // === ФАЗА 1: Главная едет, Боковая стоит ===
            SetLightsState(mainRoadLights, TrafficLightViewer.LightColor.Green);
            SetLightsState(sideRoadLights, TrafficLightViewer.LightColor.Red);
            yield return new WaitForSeconds(mainGreenDuration);

            // === ФАЗА 2: Внимание (Везде желтый) ===
            SetLightsState(mainRoadLights, TrafficLightViewer.LightColor.Yellow);
            SetLightsState(sideRoadLights, TrafficLightViewer.LightColor.Yellow);
            yield return new WaitForSeconds(yellowDuration);

            // === ФАЗА 3: Главная стоит, Боковая едет ===
            SetLightsState(mainRoadLights, TrafficLightViewer.LightColor.Red);
            SetLightsState(sideRoadLights, TrafficLightViewer.LightColor.Green);
            yield return new WaitForSeconds(sideGreenDuration);

            // === ФАЗА 4: Внимание перед переключением назад ===
            SetLightsState(mainRoadLights, TrafficLightViewer.LightColor.Yellow);
            SetLightsState(sideRoadLights, TrafficLightViewer.LightColor.Yellow);
            yield return new WaitForSeconds(yellowDuration);
        }
    }

    void SetLightsState(List<TrafficLightViewer> lights, TrafficLightViewer.LightColor color)
    {
        foreach (var light in lights)
        {
            if (light != null) light.SwitchToColor(color);
        }
    }
}