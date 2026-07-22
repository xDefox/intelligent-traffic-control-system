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

    [Header("Отладка")]
    [Tooltip("Включить логи отладки")]
    public bool enableDebugLogs = false;

    public enum IntersectionPhase { Z_Green, YellowBeforeX, X_Green, YellowBeforeZ }
    private IntersectionPhase currentPhase = IntersectionPhase.Z_Green;

    private Coroutine cycleCoroutine;
    private bool xIsTransitioning = false;
    private bool zIsTransitioning = false;

    // Состояние для AI-управления: запоминаем, какие оси сейчас зелёные
    // и сколько времени осталось
    private enum AxisState { Red, Yellow, Green, Transitioning }
    private AxisState xAxisState = AxisState.Red;
    private AxisState zAxisState = AxisState.Red;
    
    // Таймеры для удлинения зелёного сигнала
    private Coroutine xGreenCoroutine = null;
    private Coroutine zGreenCoroutine = null;
    private float xGreenRemaining = 0f;
    private float zGreenRemaining = 0f;

    [Header("EMERGENCY режим (зелёный коридор)")]
    [Tooltip("Время мигания зелёного в emergency режиме")]
    public float emergencyBlinkInterval = 0.3f;
    private bool emergencyMode = false;
    private string emergencyPhase = null;
    private Coroutine emergencyCoroutine = null;

    [Header("Fallback: автоматический возврат к автономному режиму")]
    [Tooltip("Сколько запросов без ответа до переключения на автономный режим")]
    public int fallbackRequestThreshold = 5;
    private int failedRequestCount = 0;
    private Coroutine fallbackCheckCoroutine = null;

    void Start()
    {
        // Явно включаем начальную фазу при старте, чтобы светофоры ожили
        SetPhase(IntersectionPhase.Z_Green);

        if (useAutonomousCycle)
        {
            cycleCoroutine = StartCoroutine(IntersectionCycle());
        }
        
        // Запускаем мониторинг fallback
        fallbackCheckCoroutine = StartCoroutine(FallbackCheckRoutine());
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
                zAxisState = AxisState.Green;
                xAxisState = AxisState.Red;
                break;

            case IntersectionPhase.YellowBeforeX:
            case IntersectionPhase.YellowBeforeZ:
                SetLightsState(zAxisLights, TrafficLightViewer.LightColor.Yellow);
                SetLightsState(xAxisLights, TrafficLightViewer.LightColor.Yellow);
                zAxisState = AxisState.Yellow;
                xAxisState = AxisState.Yellow;
                break;

            case IntersectionPhase.X_Green:
                SetLightsState(zAxisLights, TrafficLightViewer.LightColor.Red);
                SetLightsState(xAxisLights, TrafficLightViewer.LightColor.Green);
                zAxisState = AxisState.Red;
                xAxisState = AxisState.Green;
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
            if (enableDebugLogs) Debug.Log("[IntersectionManager] Переключено на внешнее управление ИИ (FastAPI).");
        }

        // Определяем, к какой оси относится laneId
        bool isXAxis = IsXAxisLane(laneId);
        string axisName = isXAxis ? "X" : "Z";
        
        if (enableDebugLogs) Debug.Log($"[IntersectionManager] 📥 Команда для {laneId}: {command} (ось {axisName}, duration={greenDuration}с)");
        
        if (isXAxis && xIsTransitioning) 
        {
            if (enableDebugLogs) Debug.Log($"[IntersectionManager] ⏸️ X-axis в переходе, пропускаем");
            return;
        }
        if (!isXAxis && zIsTransitioning) 
        {
            if (enableDebugLogs) Debug.Log($"[IntersectionManager] ⏸️ Z-axis в переходе, пропускаем");
            return;
        }

        string cmd = command.ToUpper().Trim();

        switch (cmd)
        {
            case "GREEN":
                if (isXAxis)
                {
                    // Включаем зелёный на ВСЕХ X-светофорах (оба подхода оси X)
                    // Предварительно гасим Z
                    StartCoroutine(SetGreenWithRenewal(zAxisLights, TrafficLightViewer.LightColor.Red,
                                                        xAxisLights, greenDuration, true));
                }
                else
                {
                    // Включаем зелёный на ВСЕХ Z-светофорах
                    StartCoroutine(SetGreenWithRenewal(xAxisLights, TrafficLightViewer.LightColor.Red,
                                                        zAxisLights, greenDuration, false));
                }
                break;

            case "RED":
                if (isXAxis)
                {
                    SetLightsState(xAxisLights, TrafficLightViewer.LightColor.Red);
                    xAxisState = AxisState.Red;
                }
                else
                {
                    SetLightsState(zAxisLights, TrafficLightViewer.LightColor.Red);
                    zAxisState = AxisState.Red;
                }
                break;
        }
    }

    /// <summary>
    /// Устанавливает зелёный на целевой оси, гасит противоположную.
    /// Если зелёный уже горит — продлевает его (renew), не переключая через жёлтый.
    /// </summary>
    private IEnumerator SetGreenWithRenewal(List<TrafficLightViewer> oppositeAxis, TrafficLightViewer.LightColor oppositeColor,
                                            List<TrafficLightViewer> targetAxis, float greenDuration, bool isXAxis)
    {
        if (isXAxis) xIsTransitioning = true;
        else zIsTransitioning = true;

        AxisState currentState = isXAxis ? xAxisState : zAxisState;

        // Если та же ось уже зелёная — продлеваем и выключаем противоположную
        if (currentState == AxisState.Green)
        {
            // ВСЕГДА выключаем противоположную ось
            SetLightsState(oppositeAxis, oppositeColor);
            if (isXAxis)
            {
                zAxisState = AxisState.Red;
            }
            else
            {
                xAxisState = AxisState.Red;
            }
            
            // Продлеваем зелёный
            if (isXAxis)
            {
                xGreenRemaining = Mathf.Max(xGreenRemaining, greenDuration > 0 ? greenDuration : 5f);
                if (enableDebugLogs) Debug.Log($"[IntersectionManager] X-axis зелёный продлён: +{greenDuration}с (Z выключен)");
            }
            else
            {
                zGreenRemaining = Mathf.Max(zGreenRemaining, greenDuration > 0 ? greenDuration : 5f);
                if (enableDebugLogs) Debug.Log($"[IntersectionManager] Z-axis зелёный продлён: +{greenDuration}с (X выключен)");
            }
            if (isXAxis) xIsTransitioning = false;
            else zIsTransitioning = false;
            yield break;
        }

        // Если целевая ось на жёлтом или красном — сначала гасим противоположную
        SetLightsState(oppositeAxis, oppositeColor);
        if (isXAxis)
        {
            zAxisState = AxisState.Red;
        }
        else
        {
            xAxisState = AxisState.Red;
        }

        // Короткая задержка перед включением зелёного (безопасность)
        yield return new WaitForSeconds(0.5f);

        // Включаем зелёный на целевой оси
        SetLightsState(targetAxis, TrafficLightViewer.LightColor.Green);
        if (isXAxis)
        {
            xAxisState = AxisState.Green;
            xGreenRemaining = greenDuration > 0 ? greenDuration : 5f;
            if (enableDebugLogs) Debug.Log($"[IntersectionManager] X-axis зелёный на {xGreenRemaining}с");
            
            // Запускаем таймер на смену
            if (xGreenCoroutine != null) StopCoroutine(xGreenCoroutine);
            xGreenCoroutine = StartCoroutine(GreenTimer(isXAxis));
        }
        else
        {
            zAxisState = AxisState.Green;
            zGreenRemaining = greenDuration > 0 ? greenDuration : 5f;
            if (enableDebugLogs) Debug.Log($"[IntersectionManager] Z-axis зелёный на {zGreenRemaining}с");
            
            if (zGreenCoroutine != null) StopCoroutine(zGreenCoroutine);
            zGreenCoroutine = StartCoroutine(GreenTimer(isXAxis));
        }

        if (isXAxis) xIsTransitioning = false;
        else zIsTransitioning = false;
    }

    /// <summary>
    /// Таймер для отслеживания оставшегося времени зелёного.
    /// Если время истекло — переключаем на жёлтый -> красный.
    /// Если время продлили (xGreenRemaining обновлён) — ждём дальше.
    /// </summary>
    private IEnumerator GreenTimer(bool isXAxis)
    {
        while (true)
        {
            float remaining = isXAxis ? xGreenRemaining : zGreenRemaining;
            if (remaining <= 0) break;

            // Ждём с проверкой каждую секунду (чтобы реагировать на продление)
            yield return new WaitForSeconds(1f);

            if (isXAxis)
            {
                xGreenRemaining -= 1f;
            }
            else
            {
                zGreenRemaining -= 1f;
            }
        }

        // Время вышло — переключаем на красный
        if (isXAxis) xIsTransitioning = true;
        else zIsTransitioning = true;
        
        if (isXAxis)
        {
            SetLightsState(xAxisLights, TrafficLightViewer.LightColor.Yellow);
            yield return new WaitForSeconds(yellowDuration);
            SetLightsState(xAxisLights, TrafficLightViewer.LightColor.Red);
            xAxisState = AxisState.Red;
            xGreenCoroutine = null;
            if (enableDebugLogs) Debug.Log("[IntersectionManager] X-axis зелёный истёк → RED");
        }
        else
        {
            SetLightsState(zAxisLights, TrafficLightViewer.LightColor.Yellow);
            yield return new WaitForSeconds(yellowDuration);
            SetLightsState(zAxisLights, TrafficLightViewer.LightColor.Red);
            zAxisState = AxisState.Red;
            zGreenCoroutine = null;
            if (enableDebugLogs) Debug.Log("[IntersectionManager] Z-axis зелёный истёк → RED");
        }

        if (isXAxis) xIsTransitioning = false;
        else zIsTransitioning = false;
    }

    /// <summary>
    /// Определяет, к какой оси относится laneId. 
    /// approach_0, approach_1 = X-ось; approach_2, approach_3 = Z-ось
    /// </summary>
    private bool IsXAxisLane(string laneId)
    {
        if (laneId.Contains("_approach_"))
        {
            string[] parts = laneId.Split('_');
            if (int.TryParse(parts[parts.Length - 1], out int approachIndex))
            {
                return approachIndex < 2;
            }
        }
        return false;
    }

    IEnumerator NetworkTransitionRoutine(IntersectionPhase yellowPhase, IntersectionPhase finalPhase)
    {
        xIsTransitioning = true;
        zIsTransitioning = true;
        SetPhase(yellowPhase);
        yield return new WaitForSeconds(yellowDuration);
        SetPhase(finalPhase);
        xIsTransitioning = false;
        zIsTransitioning = false;
    }

    /// <summary>
    /// Включить/выключить EMERGENCY режим (зелёный коридор).
    /// В emergency режиме зелёный мигает на фазе спецтранспорта,
    /// а противоположная ось горит красным.
    /// </summary>
    public void SetEmergencyMode(bool active, string phase = null)
    {
        if (active && !emergencyMode)
        {
            emergencyMode = true;
            emergencyPhase = phase;
            
            // Останавливаем автономный цикл
            if (useAutonomousCycle && cycleCoroutine != null)
            {
                StopCoroutine(cycleCoroutine);
                cycleCoroutine = null;
                useAutonomousCycle = false;
            }
            
            // Останавливаем обычные таймеры
            if (xGreenCoroutine != null) { StopCoroutine(xGreenCoroutine); xGreenCoroutine = null; }
            if (zGreenCoroutine != null) { StopCoroutine(zGreenCoroutine); zGreenCoroutine = null; }
            
            Debug.Log($"[IntersectionManager] 🚨 EMERGENCY режим ВКЛЮЧЁН! Фаза={phase}");
            
            // Запускаем мигание
            if (emergencyCoroutine != null) StopCoroutine(emergencyCoroutine);
            emergencyCoroutine = StartCoroutine(EmergencyBlinkRoutine());
        }
        else if (!active && emergencyMode)
        {
            emergencyMode = false;
            emergencyPhase = null;
            
            if (emergencyCoroutine != null)
            {
                StopCoroutine(emergencyCoroutine);
                emergencyCoroutine = null;
            }
            
            Debug.Log("[IntersectionManager] ✅ EMERGENCY режим ВЫКЛЮЧЕН");
            
            // Возвращаем всё на красный
            SetLightsState(xAxisLights, TrafficLightViewer.LightColor.Red);
            SetLightsState(zAxisLights, TrafficLightViewer.LightColor.Red);
            xAxisState = AxisState.Red;
            zAxisState = AxisState.Red;
        }
    }
    
    /// <summary>
    /// Мигание зелёного на фазе спецтранспорта.
    /// </summary>
    private IEnumerator EmergencyBlinkRoutine()
    {
        while (emergencyMode)
        {
            // Определяем, какая ось должна гореть зелёным
            // EW (X-axis) = approach_0,1; NS (Z-axis) = approach_2,3
            bool isXAxisEmergency = (emergencyPhase == "EW");
            
            // Включаем зелёный на emergency оси
            if (isXAxisEmergency)
            {
                SetLightsState(xAxisLights, TrafficLightViewer.LightColor.Green);
                SetLightsState(zAxisLights, TrafficLightViewer.LightColor.Red);
            }
            else
            {
                SetLightsState(zAxisLights, TrafficLightViewer.LightColor.Green);
                SetLightsState(xAxisLights, TrafficLightViewer.LightColor.Red);
            }
            
            yield return new WaitForSeconds(emergencyBlinkInterval);
            
            if (!emergencyMode) yield break;
            
            // Гасим зелёный (красный на обеих)
            if (isXAxisEmergency)
            {
                SetLightsState(xAxisLights, TrafficLightViewer.LightColor.Red);
            }
            else
            {
                SetLightsState(zAxisLights, TrafficLightViewer.LightColor.Red);
            }
            
            yield return new WaitForSeconds(emergencyBlinkInterval);
        }
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

    #region Fallback: автоматический возврат к автономному режиму

    /// <summary>
    /// Вызывается при успешном ответе от бэкенда - сбрасывает счётчик неудач.
    /// </summary>
    public void OnBackendResponseSuccess()
    {
        failedRequestCount = 0;
        if (enableDebugLogs) Debug.Log($"[IntersectionManager] ✅ Backend ответил, fallback счётчик сброшен");
    }

    /// <summary>
    /// Вызывается при неудачном запросе - увеличивает счётчик.
    /// </summary>
    public void OnBackendResponseFailed()
    {
        failedRequestCount++;
        if (enableDebugLogs) Debug.Log($"[IntersectionManager] ❌ Backend НЕ ответил, fallback счётчик: {failedRequestCount}/{fallbackRequestThreshold}");
    }

    /// <summary>
    /// Проверяет, нужно ли вернуться к автономному режиму.
    /// Если неудачных запросов > порога - включаем автономный цикл.
    /// </summary>
    private IEnumerator FallbackCheckRoutine()
    {
        while (true)
        {
            yield return new WaitForSeconds(1f);
            
            // Если AI-управление отключено и счётчик неудач превышен
            if (!useAutonomousCycle && failedRequestCount >= fallbackRequestThreshold)
            {
                Debug.Log($"[IntersectionManager] ⚠️ Fallback: {failedRequestCount} неудач, возвращаемся к автономному режиму");
                
                // Сбрасываем счётчик
                failedRequestCount = 0;
                
                // Включаем автономный цикл
                useAutonomousCycle = true;
                if (cycleCoroutine == null)
                {
                    cycleCoroutine = StartCoroutine(IntersectionCycle());
                    Debug.Log("[IntersectionManager] ✅ Автономный цикл запущен (fallback)");
                }
            }
        }
    }

    #endregion
}
