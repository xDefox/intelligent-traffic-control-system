using UnityEngine;
using System.Collections;
using System.Collections.Generic;

public class IntersectionManager : MonoBehaviour
{
    [Header("Светофоры Главной дороги")]
    public List<TrafficLightViewer> mainRoadLights;

    [Header("Светофоры Боковой дороги")]
    public List<TrafficLightViewer> sideRoadLights;

    [Header("Настройки автономных фаз (в секундах)")]
    public bool useAutonomousCycle = true;
    public float mainGreenDuration = 12f;
    public float yellowDuration = 2f;
    public float sideGreenDuration = 8f;

    public enum IntersectionPhase { MainGreen, AllYellowBeforeSide, SideGreen, AllYellowBeforeMain }
    private IntersectionPhase currentPhase = IntersectionPhase.MainGreen;

    private Coroutine cycleCoroutine;
    private bool isTransitioning = false; // Блокировка, пока горит жёлтый свет

    void Start()
    {
        if (useAutonomousCycle)
        {
            cycleCoroutine = StartCoroutine(IntersectionCycle());
        }
    }

    // Автономный режим работы перекрёстка по кругу
    IEnumerator IntersectionCycle()
    {
        while (true)
        {
            SetPhase(IntersectionPhase.MainGreen);
            yield return new WaitForSeconds(mainGreenDuration);

            SetPhase(IntersectionPhase.AllYellowBeforeSide);
            yield return new WaitForSeconds(yellowDuration);

            SetPhase(IntersectionPhase.SideGreen);
            yield return new WaitForSeconds(sideGreenDuration);

            SetPhase(IntersectionPhase.AllYellowBeforeMain);
            yield return new WaitForSeconds(yellowDuration);
        }
    }

    // Единая точка изменения состояния всего перекрёстка
    public void SetPhase(IntersectionPhase newPhase)
    {
        currentPhase = newPhase;

        switch (currentPhase)
        {
            case IntersectionPhase.MainGreen:
                SetLightsState(mainRoadLights, TrafficLightViewer.LightColor.Green);
                SetLightsState(sideRoadLights, TrafficLightViewer.LightColor.Red);
                break;

            case IntersectionPhase.AllYellowBeforeSide:
            case IntersectionPhase.AllYellowBeforeMain:
                SetLightsState(mainRoadLights, TrafficLightViewer.LightColor.Yellow);
                SetLightsState(sideRoadLights, TrafficLightViewer.LightColor.Yellow);
                break;

            case IntersectionPhase.SideGreen:
                SetLightsState(mainRoadLights, TrafficLightViewer.LightColor.Red);
                SetLightsState(sideRoadLights, TrafficLightViewer.LightColor.Green);
                break;
        }
    }

    // Этот метод вызывается снаружи (шлюзом FastAPI / сервером)
    public void ReceiveCommandFromPython(string command)
    {
        // Отключаем автономный таймер при первой же команде извне
        if (useAutonomousCycle && cycleCoroutine != null)
        {
            StopCoroutine(cycleCoroutine);
            cycleCoroutine = null;
            useAutonomousCycle = false;
            Debug.Log("[IntersectionManager] Переключено на внешнее управление ИИ (FastAPI).");
        }

        // Если прямо сейчас перекресток меняет фазу (горит жёлтый) — игнорируем спам командами
        if (isTransitioning) return;

        switch (command.ToUpper())
        {
            case "MAIN_GREEN":
                // Переключаем на Главную, только если сейчас горит Боковая
                if (currentPhase == IntersectionPhase.SideGreen)
                {
                    StartCoroutine(NetworkTransitionRoutine(IntersectionPhase.AllYellowBeforeMain, IntersectionPhase.MainGreen));
                }
                break;

            case "SIDE_GREEN":
                // Переключаем на Боковую, только если сейчас горит Главная
                if (currentPhase == IntersectionPhase.MainGreen)
                {
                    StartCoroutine(NetworkTransitionRoutine(IntersectionPhase.AllYellowBeforeSide, IntersectionPhase.SideGreen));
                }
                break;
        }
    }

    // Корутина безопасного сетевого перехода через жёлтый свет
    IEnumerator NetworkTransitionRoutine(IntersectionPhase yellowPhase, IntersectionPhase finalPhase)
    {
        isTransitioning = true;

        // Включаем предупреждающий желтый
        SetPhase(yellowPhase);
        yield return new WaitForSeconds(yellowDuration);

        // Включаем целевой зеленый свет
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