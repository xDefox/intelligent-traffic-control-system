using UnityEngine;
using System.Collections;
using System.Collections.Generic;

public class IntersectionManager : MonoBehaviour
{
    [Header("Светофоры оси X")]
    public List<TrafficLightViewer> xAxisLights;

    [Header("Светофоры оси Z")]
    public List<TrafficLightViewer> zAxisLights;

    [Header("Настройки автономных фаз (в секундах)")]
    public bool useAutonomousCycle = true;
    public float zGreenDuration = 12f;
    public float yellowDuration = 2f;
    public float xGreenDuration = 8f;

    public enum IntersectionPhase { Z_Green, YellowBeforeX, X_Green, YellowBeforeZ }
    private IntersectionPhase currentPhase = IntersectionPhase.Z_Green;

    private Coroutine cycleCoroutine;
    private bool isTransitioning = false;

    void Start()
    {
        // Явно включаем начальную фазу при старте, чтобы светофоры ожили
        SetPhase(IntersectionPhase.Z_Green);

        if (useAutonomousCycle)
        {
            cycleCoroutine = StartCoroutine(IntersectionCycle());
        }
    }

    // Автономный режим по осям
    IEnumerator IntersectionCycle()
    {
        while (true)
        {
            SetPhase(IntersectionPhase.Z_Green);
            yield return new WaitForSeconds(zGreenDuration);

            SetPhase(IntersectionPhase.YellowBeforeX);
            yield return new WaitForSeconds(yellowDuration);

            SetPhase(IntersectionPhase.X_Green);
            yield return new WaitForSeconds(xGreenDuration);

            SetPhase(IntersectionPhase.YellowBeforeZ);
            yield return new WaitForSeconds(yellowDuration);
        }
    }

    public void SetPhase(IntersectionPhase newPhase)
    {
        currentPhase = newPhase;

        switch (currentPhase)
        {
            case IntersectionPhase.Z_Green:
                SetLightsState(zAxisLights, TrafficLightViewer.LightColor.Green);
                SetLightsState(xAxisLights, TrafficLightViewer.LightColor.Red);
                break;

            case IntersectionPhase.YellowBeforeX:
            case IntersectionPhase.YellowBeforeZ:
                SetLightsState(zAxisLights, TrafficLightViewer.LightColor.Yellow);
                SetLightsState(xAxisLights, TrafficLightViewer.LightColor.Yellow);
                break;

            case IntersectionPhase.X_Green:
                SetLightsState(zAxisLights, TrafficLightViewer.LightColor.Red);
                SetLightsState(xAxisLights, TrafficLightViewer.LightColor.Green);
                break;
        }
    }

    // Принимает команды для конкретного светофора от ИИ
    public void ReceiveCommandForLane(string laneId, string command, float greenDuration = 0f)
    {
        if (useAutonomousCycle && cycleCoroutine != null)
        {
            StopCoroutine(cycleCoroutine);
            cycleCoroutine = null;
            useAutonomousCycle = false;
            Debug.Log("[IntersectionManager] Переключено на внешнее управление ИИ (FastAPI).");
        }

        if (isTransitioning) return;

        // Определяем, какой светофор нужно управлять
        TrafficLightViewer targetLight = GetLightForLane(laneId);
        if (targetLight == null)
        {
            Debug.LogWarning($"[IntersectionManager] Не найден светофор для {laneId}");
            return;
        }

        // ПРЯМО управляем светофором через TrafficLightViewer
        StartCoroutine(ExecuteLightCommand(targetLight, command, greenDuration));
    }

    private TrafficLightViewer GetLightForLane(string laneId)
    {
        // Извлекаем номер подхода: "intersection_1_approach_0" -> 0
        if (laneId.Contains("_approach_"))
        {
            string[] parts = laneId.Split('_');
            if (int.TryParse(parts[parts.Length - 1], out int approachIndex))
            {
                // X-axis: 0,1 -> xAxisLights[0], xAxisLights[1]
                // Z-axis: 2,3 -> zAxisLights[0], zAxisLights[1]
                if (approachIndex < 2 && approachIndex < xAxisLights.Count)
                {
                    return xAxisLights[approachIndex];
                }
                else if (approachIndex >= 2 && (approachIndex - 2) < zAxisLights.Count)
                {
                    return zAxisLights[approachIndex - 2];
                }
            }
        }
        return null;
    }

    private IEnumerator ExecuteLightCommand(TrafficLightViewer light, string command, float greenDuration)
    {
        isTransitioning = true;

        string cmd = command.ToUpper().Trim();
        
        switch (cmd)
        {
            case "GREEN":
            case "NS":
            case "EW":
                // Жёлтый
                light.SwitchToColor(TrafficLightViewer.LightColor.Yellow);
                yield return new WaitForSeconds(yellowDuration);
                
                // Зелёный на указанную длительность
                float greenTime = greenDuration > 0 ? greenDuration : 5f;
                light.SwitchToColor(TrafficLightViewer.LightColor.Green);
                yield return new WaitForSeconds(greenTime);
                
                // Автоматически в красный
                light.SwitchToColor(TrafficLightViewer.LightColor.Red);
                break;
                
            case "YELLOW":
                light.SwitchToColor(TrafficLightViewer.LightColor.Yellow);
                yield return new WaitForSeconds(yellowDuration);
                light.SwitchToColor(TrafficLightViewer.LightColor.Red);
                break;
                
            case "RED":
            default:
                light.SwitchToColor(TrafficLightViewer.LightColor.Red);
                break;
        }

        isTransitioning = false;
    }

    IEnumerator NetworkTransitionRoutine(IntersectionPhase yellowPhase, IntersectionPhase finalPhase)
    {
        isTransitioning = true;
        SetPhase(yellowPhase);
        yield return new WaitForSeconds(yellowDuration);
        SetPhase(finalPhase);
        isTransitioning = false;
    }

    void SetLightsState(List<TrafficLightViewer> lights, TrafficLightViewer.LightColor color)
    {
        foreach (var light in lights)
        {
            if (light != null)
            {
                light.SwitchToColor(color);
            }
        }
    }
}