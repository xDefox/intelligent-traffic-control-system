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

    // Принимает команды "Z_GREEN" или "X_GREEN" от ИИ
    public void ReceiveCommandFromPython(string command)
    {
        if (useAutonomousCycle && cycleCoroutine != null)
        {
            StopCoroutine(cycleCoroutine);
            cycleCoroutine = null;
            useAutonomousCycle = false;
            Debug.Log("[IntersectionManager] Переключено на внешнее управление ИИ (FastAPI).");
        }

        if (isTransitioning) return;

        switch (command.ToUpper())
        {
            case "Z_GREEN":
                if (currentPhase == IntersectionPhase.X_Green)
                {
                    StartCoroutine(NetworkTransitionRoutine(IntersectionPhase.YellowBeforeZ, IntersectionPhase.Z_Green));
                }
                break;

            case "X_GREEN":
                if (currentPhase == IntersectionPhase.Z_Green)
                {
                    StartCoroutine(NetworkTransitionRoutine(IntersectionPhase.YellowBeforeX, IntersectionPhase.X_Green));
                }
                break;
        }
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