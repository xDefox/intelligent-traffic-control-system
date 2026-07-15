using UnityEngine;
using System.Collections.Generic;

/// <summary>
/// Упрощённый навигатор для глобального графа waypoints.
/// Движется от waypoint к waypoint, на каждом выбирая случайного соседа.
/// </summary>
public class WaypointNavigator : MonoBehaviour
{
    [Header("Настройки движения")]
    public float speed = 30f;
    public float rotationSpeed = 25f;

    [Header("Дистанция и детекция")]
    [Tooltip("Дистанция до машины впереди")]
    public float maxCheckDistance = 2.2f;
    [Tooltip("Угол поворота (в градусах), при котором луч полностью гаснет")]
    public float turnAngleThreshold = 25f;
    [Tooltip("Радиус SphereCast для детекции машин")]
    public float sphereCastRadius = 0.5f;
    [Tooltip("Дистанция для переключения на следующий waypoint")]
    public float waypointReachDistance = 1.0f;

    [Header("Аварийное торможение")]
    [Tooltip("Минимальное расстояние до впереди идущей машины")]
    public float emergencyBrakeDistance = 1.2f;
    [Tooltip("Скорость сброса при аварийном торможении")]
    public float emergencyBrakeStrength = 15f;
    [Tooltip("Сглаживание изменения скорости (больше = плавнее)")]
    public float speedSmoothing = 10f;

    [Header("Визуализация")]
    public Color debugRayColor = Color.red;

    // Текущий waypoint
    private WaypointNode currentNode;
    private float originalSpeed;

    // Светофор
    private bool isStoppedByLight = false;
    private TrafficLightViewer currentTrafficLight;

    // Physics
    private Rigidbody rb;
    private bool usePhysics = false;
    private WheelCollider[] wheelColliders;

    // Правило правой руки (помеха справа)
    private IntersectionRightOfWay currentIntersectionRule;
    private bool isYieldingAtIntersection = false;
    private bool isDeadlockCreeping = false;
    private float deadlockCreepTimer = 0f;
    private float deadlockPauseTimer = 0f;
    private bool isInDeadlockPause = false;

    void Start()
    {
        originalSpeed = speed;

        // Check if car has Rigidbody
        rb = GetComponent<Rigidbody>();
        if (rb != null)
        {
            usePhysics = true;
            rb.isKinematic = true;
            rb.useGravity = false;
        }

        // Disable WheelColliders to prevent physics conflicts
        wheelColliders = GetComponentsInChildren<WheelCollider>();
        if (wheelColliders != null && wheelColliders.Length > 0)
        {
            foreach (var wc in wheelColliders)
            {
                wc.enabled = false;
            }
        }
    }

    void Update()
    {
        if (currentNode == null) return;

        // Движение к текущему waypoint
        MoveToCurrentNode();
    }

    /// <summary>
    /// Установить начальный waypoint для машины
    /// </summary>
    public void SetupNode(WaypointNode startNode)
    {
        if (startNode == null) return;

        currentNode = startNode;
        isStoppedByLight = false;
        currentTrafficLight = null;

        // Поворачиваемся к первому waypoint
        if (currentNode != null)
        {
            Vector3 lookTarget = new Vector3(currentNode.transform.position.x, transform.position.y, currentNode.transform.position.z);
            transform.LookAt(lookTarget);
        }
    }

    /// <summary>
    /// Основное движение - едем с постоянной скоростью, только светофоры и машины впереди
    /// </summary>
    private void MoveToCurrentNode()
    {
        if (currentNode == null) return;

        Vector3 targetPosition = currentNode.transform.position;
        Vector3 directionToTarget = targetPosition - transform.position;
        directionToTarget.y = 0;
        float distanceToTarget = directionToTarget.magnitude;

        // Если достигли waypoint - переключаемся на следующий
        if (distanceToTarget < waypointReachDistance)
        {
            ReachedWaypoint();
            // Сразу пересчитываем направление к новому waypoint
            targetPosition = currentNode.transform.position;
            directionToTarget = targetPosition - transform.position;
            directionToTarget.y = 0;
        }

        // Направление к текущему waypoint
        Vector3 moveDirection = directionToTarget.normalized;

        // Проверка на светофор
        float targetSpeed = CheckTrafficLight();

        // Проверка правила правой руки (помеха справа) на перекрёстках
        CheckIntersectionRightOfWay(ref targetSpeed);

        // Проверка на машину впереди
        bool carDetected = CheckCarInFront(out float detectedDistance);

        if (carDetected)
        {
            if (detectedDistance < emergencyBrakeDistance)
            {
                // Аварийное торможение
                targetSpeed = 0f;
            }
            else
            {
                // Плавное замедление
                float brakeFactor = Mathf.Clamp01((detectedDistance - emergencyBrakeDistance) / (maxCheckDistance - emergencyBrakeDistance));
                targetSpeed = Mathf.Min(targetSpeed, speed * brakeFactor);
            }
        }

        // Плавное изменение скорости
        if (targetSpeed == 0f)
        {
            speed = Mathf.Lerp(speed, 0f, emergencyBrakeStrength * Time.deltaTime);
        }
        else
        {
            speed = Mathf.Lerp(speed, targetSpeed, speedSmoothing * Time.deltaTime);
        }

        // Если скорость слишком низкая - не двигаемся
        //if (speed < 0.05f) return;

        // Плавный поворот
        if (moveDirection != Vector3.zero)
        {
            Quaternion targetRotation = Quaternion.LookRotation(moveDirection);
            transform.rotation = Quaternion.Slerp(transform.rotation, targetRotation, rotationSpeed * Time.deltaTime);
        }

        // Движение
        if (usePhysics && rb != null)
        {
            rb.MovePosition(rb.position + moveDirection * speed * Time.deltaTime);
        }
        else
        {
            transform.Translate(moveDirection * speed * Time.deltaTime, Space.World);
        }
    }

    /// <summary>
    /// Переход к следующему waypoint
    /// </summary>
    private void ReachedWaypoint()
    {
        // Сбрасываем состояние правила правой руки и кэш
        ResetIntersectionState();
        currentIntersectionRule = null;

        // Выбираем случайного соседа
        WaypointNode nextNode = currentNode.GetRandomNeighbour();

        if (nextNode != null)
        {
            currentNode = nextNode;
        }
        else
        {
            // Нет куда ехать - уничтожаем машину
            Destroy(gameObject);
        }
    }

    /// <summary>
    /// Проверка светофора на текущем waypoint
    /// </summary>
    private float CheckTrafficLight()
    {
        if (isStoppedByLight && currentTrafficLight != null)
        {
            TrafficLightViewer.LightColor currentLight = currentTrafficLight.GetCurrentLight();
            if (currentLight == TrafficLightViewer.LightColor.Red || currentLight == TrafficLightViewer.LightColor.Yellow)
            {
                return 0f; // Стоим на красный
            }
            else
            {
                isStoppedByLight = false;
                currentTrafficLight = null;
            }
        }

        return originalSpeed;
    }

    /// <summary>
    /// Проверка правила правой руки (помеха справа) на перекрёстках.
    /// Если текущий waypoint — перекрёсток с компонентом IntersectionRightOfWay,
    /// машина уступает дорогу тем, кто приближается справа.
    /// </summary>
    private void CheckIntersectionRightOfWay(ref float targetSpeed)
    {
        // Проверяем, есть ли на текущем waypoint компонент правила правой руки
        if (currentNode == null || !currentNode.isIntersection) return;

        // Ищем компонент IntersectionRightOfWay на currentNode или родителе
        if (currentIntersectionRule == null)
        {
            currentIntersectionRule = currentNode.GetComponent<IntersectionRightOfWay>();
            // Если нет на самом waypoint, ищем на родительском объекте (перекрёстке)
            if (currentIntersectionRule == null && currentNode.transform.parent != null)
            {
                currentIntersectionRule = currentNode.transform.parent.GetComponentInChildren<IntersectionRightOfWay>();
            }
        }

        if (currentIntersectionRule == null) return;

        // Проверяем дистанцию до перекрёстка — правило действует только в радиусе детекции
        float distanceToIntersection = Vector3.Distance(transform.position, currentIntersectionRule.transform.position);
        if (distanceToIntersection > currentIntersectionRule.detectionRadius * 1.5f)
        {
            // Слишком далеко от перекрёстка — сбрасываем состояние
            if (isYieldingAtIntersection || isDeadlockCreeping)
            {
                ResetIntersectionState();
            }
            return;
        }

        // Проверяем, нужно ли уступать
        bool isDeadlockActive;
        bool shouldYield = currentIntersectionRule.CheckRightOfWay(
            transform.position, transform.forward, gameObject, out isDeadlockActive);

        if (isDeadlockActive)
        {
            // Режим дедлока — подкрадываемся
            HandleDeadlockMode(ref targetSpeed);
        }
        else if (shouldYield)
        {
            // Уступаем — тормозим
            isYieldingAtIntersection = true;
            isDeadlockCreeping = false;
            isInDeadlockPause = false;
            targetSpeed = 0f;
        }
        else
        {
            // Путь свободен — сбрасываем состояние
            if (isYieldingAtIntersection || isDeadlockCreeping)
            {
                ResetIntersectionState();
            }
        }
    }

    /// <summary>
    /// Обработка режима дедлока — медленное подкрадывание с паузами.
    /// </summary>
    private void HandleDeadlockMode(ref float targetSpeed)
    {
        isDeadlockCreeping = true;
        isYieldingAtIntersection = false;

        if (isInDeadlockPause)
        {
            // В паузе — стоим
            deadlockPauseTimer += Time.deltaTime;
            targetSpeed = 0f;

            if (deadlockPauseTimer >= currentIntersectionRule.creepPauseInterval)
            {
                isInDeadlockPause = false;
                deadlockPauseTimer = 0f;
            }
        }
        else
        {
            // Подкрадываемся
            deadlockCreepTimer += Time.deltaTime;
            targetSpeed = currentIntersectionRule.GetDeadlockSpeed(gameObject);

            // Проверяем, нужно ли сделать паузу
            if (currentIntersectionRule.ShouldPauseBetweenCreeps(gameObject))
            {
                isInDeadlockPause = true;
                deadlockPauseTimer = 0f;
            }
        }
    }

    /// <summary>
    /// Сброс состояния правила правой руки.
    /// </summary>
    private void ResetIntersectionState()
    {
        isYieldingAtIntersection = false;
        isDeadlockCreeping = false;
        isInDeadlockPause = false;
        deadlockCreepTimer = 0f;
        deadlockPauseTimer = 0f;
    }

    /// <summary>
    /// Проверка машины впереди - только для замедления
    /// </summary>
    private bool CheckCarInFront(out float distance)
    {
        distance = float.MaxValue;
        Vector3 rayStart = transform.position + Vector3.up * 0.4f + transform.forward * 0.6f;

        // Угол к текущему waypoint
        Vector3 directionToTarget = (currentNode.transform.position - transform.position).normalized;
        directionToTarget.y = 0;
        float angleToTarget = Vector3.Angle(transform.forward, directionToTarget);

        // Используем SphereCast только если смотрим примерно вперёд
        if (angleToTarget < turnAngleThreshold)
        {
            RaycastHit hit;
            int layerMask = LayerMask.GetMask("Traffic");

            Debug.DrawLine(rayStart, rayStart + transform.forward * maxCheckDistance, debugRayColor);

            if (Physics.SphereCast(rayStart, sphereCastRadius, transform.forward, out hit, maxCheckDistance, layerMask))
            {
                if (hit.collider.gameObject != gameObject)
                {
                    distance = hit.distance;
                    return true;
                }
            }
        }

        return false;
    }

    void OnDestroy()
    {
        if (wheelColliders != null)
        {
            foreach (var wc in wheelColliders)
            {
                if (wc != null) wc.enabled = true;
            }
        }

        if (rb != null)
        {
            rb.isKinematic = false;
            rb.useGravity = true;
        }
    }

    /// <summary>
    /// Визуализация в редакторе
    /// </summary>
    void OnDrawGizmos()
    {
        if (currentNode == null || !TrafficGenerator.ShowDebugGizmos)
            return;

        // Линия к текущему waypoint
        Gizmos.color = Color.yellow;
        Gizmos.DrawLine(transform.position, currentNode.transform.position);
        Gizmos.DrawSphere(currentNode.transform.position, 0.5f);
    }
}