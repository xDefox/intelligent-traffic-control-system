using UnityEngine;
using System.Collections.Generic;

public class WaypointNavigator : MonoBehaviour
{
    [Header("Настройки движения")]
    public List<Transform> waypoints = new List<Transform>();
    public float speed = 12f;
    public float rotationSpeed = 15f;

    [Header("Дистанция и Векторы")]
    [Tooltip("Дистанция до машины впереди")]
    public float maxCheckDistance = 2.2f;
    [Tooltip("Угол поворота (в градусах), при котором луч полностью гаснет")]
    public float turnAngleThreshold = 25f;
    [Tooltip("Радиус SphereCast для детекции машин не только строго спереди")]
    public float sphereCastRadius = 0.5f;

    [Header("Плавность переходов")]
    [Tooltip("Время плавного перехода между сегментами (сек)")]
    public float segmentTransitionSmoothness = 0.3f;
    [Tooltip("Сглаживание изменения скорости (меньше = быстрее)")]
    public float speedSmoothing = 5f;

    [Header("Аварийное торможение")]
    [Tooltip("Минимальное расстояние до впереди идущей машины при котором резко тормозим")]
    public float emergencyBrakeDistance = 1.5f;
    [Tooltip("Скорость сброса при аварийном торможении (чем больше, тем резче)")]
    public float emergencyBrakeStrength = 30f;

    [Header("Deadlock / Jam Resolution")]
    [Tooltip("Скорость заднего хода при застревании")]
    public float reverseSpeed = 5f;
    [Tooltip("Время езды задним ходом перед новой попыткой")]
    public float reverseDuration = 1.2f;
    [Tooltip("Время простоя (сек), после которого считаем что застряли")]
    public float stuckTimeThreshold = 3.0f;
    [Tooltip("Максимум попыток объехать, после которых переходим к заднему ходу")]
    public int maxStuckAttemptsBeforeReverse = 3;

    [Header("Intersection Queue Guard")]
    [Tooltip("Сколько СТОЯЩИХ машин допускается на следующем сегменте, чтобы мы ещё влезли")]
    public int maxCarsAheadOnNextSegment = 3;
    [Tooltip("Радиус проверки занятости следующего сегмента")]
    public float nextSegmentCheckRadius = 1.0f;
    [Tooltip("Порог скорости, ниже которого машина считается 'стоящей' для детекции пробки")]
    public float stuckSpeedThreshold = 0.8f;

    private RoadSegment currentSegment;
    private int currentWaypointIndex = 0;
    private float originalSpeed;
    private float currentSpeed;
    private bool isTransitioning = false;
    private float transitionTimer = 0f;

    // Светофор
    private bool isStoppedByLight = false;
    private bool isOnIntersection = false;
    private string lastStopReason = "Едет";

    // Встречный поток
    private OncomingTrafficDetector oncomingDetector;
    private int stopWaypointIndex = -1;
    
    // Physics
    private Rigidbody rb;
    private bool usePhysics = false;
    private WheelCollider[] wheelColliders;

    // Счётчики для определения "застревания"
    private float stuckTimer = 0f;
    private Vector3 lastPosition;
    private int stuckAttempts = 0;
    
    // Состояние заднего хода
    private bool isReversing = false;
    private float reverseTimer = 0f;
    
    // Блокировка въезда на перекрёсток: если впереди пробка, стоим
    private bool intersectionBlockedAhead = false;

    void Start()
    {
        originalSpeed = speed;
        currentSpeed = speed;
        lastPosition = transform.position;

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

    public void SetupSegment(RoadSegment segment, bool isInitialSpawn = false)
    {
        if (segment == null || segment.localWaypoints == null || segment.localWaypoints.Count == 0) return;

        currentSegment = segment;
        waypoints = new List<Transform>(segment.localWaypoints);
        oncomingDetector = segment.oncomingDetector;
        stopWaypointIndex = segment.stopWaypointIndex;
        currentWaypointIndex = 0;
        isOnIntersection = false;
        isReversing = false;
        reverseTimer = 0f;
        stuckTimer = 0f;
        stuckAttempts = 0;
        intersectionBlockedAhead = false;

        if (isInitialSpawn && waypoints.Count > 0 && waypoints[0] != null)
        {
            Vector3 lookTarget = new Vector3(waypoints[0].position.x, transform.position.y, waypoints[0].position.z);
            transform.LookAt(lookTarget);
        }
    }

    private LayerMask CreateLayerMask()
    {
        return LayerMask.GetMask("Traffic");
    }

    void Update()
    {
        if (waypoints == null || waypoints.Count == 0 || currentWaypointIndex >= waypoints.Count) return;

        // Если мы на заднем ходу — обрабатываем отдельно
        if (isReversing)
        {
            HandleReverse();
            return;
        }

        string currentReason = "Едет";
        Transform targetWaypoint = waypoints[currentWaypointIndex];

        if (targetWaypoint == null) return;

        // --- ПРОВЕРКА: СВОБОДЕН ЛИ ПЕРЕКРЁСТОК ВПЕРЕДИ? ---
        // Смотрим на 1-2 машины впереди по нашему пути
        if (isOnIntersection)
        {
            // Если мы уже на перекрёстке — проверяем, не забит ли выезд с него
            CheckIntersectionExitBlocked();
        }
        else
        {
            // Если мы перед перекрёстком — проверяем, можем ли мы на него въехать
            CheckIntersectionEntranceBlocked();
        }

        // Базовая рабочая дистанция
        float actualMaxDistance = maxCheckDistance;

        // ЕСЛИ МЫ НА ПЕРЕКРЕСТКЕ: укорачиваем луч, чтобы не бить в бока
        if (isOnIntersection)
        {
            actualMaxDistance = 1.2f;
        }

        // Считаем угол до цели
        Vector3 directionToTarget = (targetWaypoint.position - transform.position).normalized;
        directionToTarget.y = 0;
        float angleToTarget = Vector3.Angle(transform.forward, directionToTarget);

        bool carDetectedInFront = false;
        float detectedCarDistance = float.MaxValue;

        Vector3 rayStart = transform.position + Vector3.up * 0.4f + transform.forward * 0.6f;

        // Используем SphereCast вместо Raycast
        if (angleToTarget < turnAngleThreshold)
        {
            RaycastHit hit;
            int layerMask = CreateLayerMask();

            Debug.DrawLine(rayStart, rayStart + transform.forward * actualMaxDistance, Color.red);

            if (Physics.SphereCast(rayStart, sphereCastRadius, transform.forward, out hit, actualMaxDistance, layerMask))
            {
                if (hit.collider.gameObject != gameObject)
                {
                    carDetectedInFront = true;
                    detectedCarDistance = hit.distance;
                }
            }
        }
        else
        {
            Debug.DrawLine(rayStart, rayStart + transform.forward * 0.5f, Color.green);
        }

        // Логика торможения
        float targetSpeed = originalSpeed;
        bool emergencyStop = false;

        if (isStoppedByLight)
        {
            targetSpeed = 0f;
            currentReason = "Стоит перед светофором";
        }
        else if (intersectionBlockedAhead)
        {
            // Выезд с перекрёстка заблокирован — стоим и не въезжаем
            targetSpeed = 0f;
            currentReason = "Выезд с перекрёстка заблокирован";
        }
        else if (carDetectedInFront)
        {
            if (detectedCarDistance < emergencyBrakeDistance)
            {
                emergencyStop = true;
                currentReason = $"Аварийное торможение ({detectedCarDistance:F2}м)";
            }
            else
            {
                float brakeFactor = Mathf.Clamp01((detectedCarDistance - emergencyBrakeDistance) / (actualMaxDistance - emergencyBrakeDistance));
                targetSpeed = originalSpeed * brakeFactor;
                currentReason = $"Держит дистанцию ({detectedCarDistance:F2}м)";
            }
        }
        else if (oncomingDetector != null && !oncomingDetector.IsClear && currentWaypointIndex == stopWaypointIndex)
        {
            float distanceToStop = Vector3.Distance(transform.position, waypoints[stopWaypointIndex].position);
            if (distanceToStop < 1.5f)
            {
                targetSpeed = 0f;
                currentReason = "Пропускает встречку";
            }
        }

        // Аварийное торможение
        if (emergencyStop)
        {
            speed = Mathf.Lerp(speed, 0f, emergencyBrakeStrength * Time.deltaTime);
        }
        else
        {
            speed = Mathf.Lerp(speed, targetSpeed, speedSmoothing * Time.deltaTime);
        }

        // --- УЛУЧШЕННАЯ ЗАЩИТА ОТ ЗАСТРЕВАНИЯ ---
        float movedDistance = Vector3.Distance(transform.position, lastPosition);
        
        if (movedDistance < 0.01f && speed > 0.5f && !isStoppedByLight && !intersectionBlockedAhead)
        {
            stuckTimer += Time.deltaTime;
            
            if (stuckTimer > stuckTimeThreshold)
            {
                stuckAttempts++;
                
                if (stuckAttempts >= maxStuckAttemptsBeforeReverse)
                {
                    // Слишком много попыток — переходим к заднему ходу
                    Debug.Log($"[{gameObject.name}] Застрял ({stuckAttempts} попыток) → задний ход");
                    StartReverse();
                }
                else
                {
                    // Пытаемся объехать
                    Debug.Log($"[{gameObject.name}] Застрял, попытка {stuckAttempts}/{maxStuckAttemptsBeforeReverse}");
                    speed = originalSpeed * 0.3f;
                    
                    // Поворачиваем слегка, чтобы обойти (чередуем направление)
                    float turnDirection = (stuckAttempts % 2 == 0) ? 1f : -1f;
                    transform.Rotate(0, 30 * turnDirection * Time.deltaTime, 0);
                }
                
                stuckTimer = 0f;
            }
        }
        else
        {
            // Сброс счётчика, если двинулись или стоим по уважительной причине
            if (movedDistance >= 0.01f)
            {
                stuckTimer = 0f;
                if (!isStoppedByLight && !intersectionBlockedAhead)
                {
                    stuckAttempts = 0;
                }
            }
        }
        lastPosition = transform.position;

        // Если стоим — не двигаемся
        if (speed < 0.05f) return;

        // Handle segment transition smoothing
        if (isTransitioning)
        {
            transitionTimer -= Time.deltaTime;
            if (transitionTimer <= 0f)
            {
                isTransitioning = false;
            }
        }

        // Движение вперёд
        Vector3 moveDirection = targetWaypoint.position - transform.position;
        moveDirection.y = 0;
        float distanceToWaypoint = moveDirection.magnitude;
        moveDirection.Normalize();

        // Look ahead для плавных поворотов
        Vector3 lookAheadDirection = moveDirection;
        if (currentWaypointIndex + 1 < waypoints.Count && waypoints[currentWaypointIndex + 1] != null)
        {
            Vector3 nextWaypointDir = (waypoints[currentWaypointIndex + 1].position - targetWaypoint.position).normalized;
            nextWaypointDir.y = 0;
            lookAheadDirection = Vector3.Lerp(moveDirection, nextWaypointDir, 0.3f).normalized;
        }

        if (lookAheadDirection != Vector3.zero)
        {
            Quaternion targetRotation = Quaternion.LookRotation(lookAheadDirection);
            transform.rotation = Quaternion.Slerp(transform.rotation, targetRotation, rotationSpeed * Time.deltaTime);
        }

        // Move
        if (usePhysics && rb != null)
        {
            rb.MovePosition(rb.position + moveDirection * speed * Time.deltaTime);
        }
        else
        {
            transform.Translate(moveDirection * speed * Time.deltaTime, Space.World);
        }

        if (distanceToWaypoint < 0.6f)
        {
            currentWaypointIndex++;
            intersectionBlockedAhead = false; // сбрасываем при смене точки

            if (currentWaypointIndex >= waypoints.Count)
            {
                // Перед переходом на следующий сегмент — проверяем, есть ли там место
                if (IsNextSegmentAvailable())
                {
                    SwitchToNextSegment();
                }
                else
                {
                    // Следующий сегмент забит — стоим и ждём
                    Debug.Log($"[{gameObject.name}] Следующий сегмент забит, ждём");
                    currentWaypointIndex = waypoints.Count - 1; // остаёмся на последней точке
                    intersectionBlockedAhead = true;
                }
            }
        }
    }

    /// <summary>
    /// Проверяет, не забит ли выезд с перекрёстка (чтобы не блокировать его).
    /// Блокирует выезд ТОЛЬКО если впереди стоит машина вплотную.
    /// </summary>
    private void CheckIntersectionExitBlocked()
    {
        // Короткий луч — проверяем только непосредственно перед носом
        float checkDistance = 1.5f;
        RaycastHit hit;
        int layerMask = CreateLayerMask();
        
        Vector3 checkStart = transform.position + Vector3.up * 0.4f;
        Debug.DrawLine(checkStart, checkStart + transform.forward * checkDistance, Color.yellow);
        
        if (Physics.SphereCast(checkStart, 0.6f, transform.forward, out hit, checkDistance, layerMask))
        {
            if (hit.collider.gameObject != gameObject)
            {
                // Только если машина стоит или еле ползёт (<20% скорости)
                WaypointNavigator otherNav = hit.collider.GetComponent<WaypointNavigator>();
                if (otherNav != null && otherNav.speed < 0.3f)
                {
                    intersectionBlockedAhead = true;
                }
            }
        }
    }

    /// <summary>
    /// Проверяет, можем ли мы въехать на перекрёсток (свободен ли выезд с него).
    /// Блокирует въезд ТОЛЬКО если на следующем сегменте СТОЯТ машины (образуется пробка).
    /// </summary>
    private void CheckIntersectionEntranceBlocked()
    {
        // По умолчанию проезд свободен
        intersectionBlockedAhead = false;
        
        // Если следующих сегментов нет — нечего проверять
        if (currentSegment == null || currentSegment.nextPossibleSegments == null || currentSegment.nextPossibleSegments.Count == 0)
            return;

        // Проверяем ТОЛЬКО непосредственно выезд с перекрёстка (первые 3 вейпоинта следующего сегмента)
        // Блокируем только если там СТОЯТ машины (скорость ниже порога)
        foreach (var nextSeg in currentSegment.nextPossibleSegments)
        {
            if (nextSeg != null && IsSegmentBackedUp(nextSeg, 3))
            {
                intersectionBlockedAhead = true;
                return;
            }
        }
    }

    /// <summary>
    /// Проверяет, забит ли сегмент СТОЯЩИМИ машинами.
    /// Считает только машины, скорость которых ниже stuckSpeedThreshold.
    /// </summary>
    private bool IsSegmentBackedUp(RoadSegment segment, int maxCarsThreshold)
    {
        if (segment == null || segment.localWaypoints == null || segment.localWaypoints.Count == 0) return false;
        
        Transform firstWp = segment.localWaypoints[0];
        if (firstWp == null) return false;
        
        // Проверяем сферой вокруг первого вейпоинта сегмента
        Collider[] colliders = Physics.OverlapSphere(firstWp.position, 2.5f, CreateLayerMask());
        int stuckCarCount = 0;
        foreach (var col in colliders)
        {
            if (col.CompareTag("Car") && col.gameObject != gameObject)
            {
                // Смотрим скорость машины — если едет, то не считаем за пробку
                WaypointNavigator otherNav = col.GetComponent<WaypointNavigator>();
                if (otherNav != null && otherNav.speed < stuckSpeedThreshold)
                {
                    stuckCarCount++;
                    if (stuckCarCount >= maxCarsThreshold) return true;
                }
            }
        }
        return false;
    }

    /// <summary>
    /// Проверяет, достаточно ли места на следующем сегменте, чтобы туда перейти.
    /// Блокирует переход только если на сегменте СТОИТ (maxCarsAheadOnNextSegment) машин.
    /// </summary>
    private bool IsNextSegmentAvailable()
    {
        if (currentSegment == null || currentSegment.nextPossibleSegments == null || currentSegment.nextPossibleSegments.Count == 0)
        {
            return true; // если сегментов нет — уничтожится, пропускаем проверку
        }

        // Проверяем все возможные следующие сегменты
        foreach (var nextSeg in currentSegment.nextPossibleSegments)
        {
            if (nextSeg != null && IsSegmentBackedUp(nextSeg, maxCarsAheadOnNextSegment))
            {
                return false; // есть сегмент где стоят машины — подождём
            }
        }
        return true;
    }

    /// <summary>
    /// Запуск заднего хода для разрешения дедлока.
    /// </summary>
    private void StartReverse()
    {
        isReversing = true;
        reverseTimer = reverseDuration;
        stuckTimer = 0f;
        speed = 0f;
        Debug.Log($"[{gameObject.name}] 🚗 Начинаю сдавать назад на {reverseDuration}с");
    }

    /// <summary>
    /// Обработка заднего хода.
    /// </summary>
    private void HandleReverse()
    {
        reverseTimer -= Time.deltaTime;
        
        // Движемся назад
        Vector3 reverseDirection = -transform.forward;
        if (usePhysics && rb != null)
        {
            rb.MovePosition(rb.position + reverseDirection * reverseSpeed * Time.deltaTime);
        }
        else
        {
            transform.Translate(reverseDirection * reverseSpeed * Time.deltaTime, Space.World);
        }

        if (reverseTimer <= 0f)
        {
            // Задний ход завершён — поворачиваем и пробуем снова
            isReversing = false;
            stuckAttempts = 0;
            speed = originalSpeed * 0.5f; // начинаем медленно
            
            // Поворачиваем в случайную сторону для объезда
            float turnAngle = Random.Range(-30f, 30f);
            transform.Rotate(0, turnAngle, 0);
            
            Debug.Log($"[{gameObject.name}] ↪️ Закончил сдавать назад, пробую объехать");
        }
    }

    private void SwitchToNextSegment()
    {
        if (currentSegment != null && currentSegment.nextPossibleSegments != null && currentSegment.nextPossibleSegments.Count > 0)
        {
            int randomIndex = Random.Range(0, currentSegment.nextPossibleSegments.Count);
            RoadSegment nextSegment = currentSegment.nextPossibleSegments[randomIndex];

            Vector3 carPosition = transform.position;
            
            SetupSegment(nextSegment, false);
            
            if (waypoints.Count > 0 && waypoints[0] != null)
            {
                Vector3 lookTarget = new Vector3(waypoints[0].position.x, carPosition.y, waypoints[0].position.z);
                Quaternion targetRotation = Quaternion.LookRotation(lookTarget - carPosition);
                transform.rotation = Quaternion.Slerp(transform.rotation, targetRotation, 0.5f);
            }

            isTransitioning = true;
            transitionTimer = segmentTransitionSmoothness;
        }
        else
        {
            Destroy(gameObject);
        }
    }

    void OnTriggerStay(Collider other)
    {
        if (other.CompareTag("StopTrigger"))
        {
            isOnIntersection = true;

            TrafficLightViewer trafficLight = other.GetComponentInParent<TrafficLightViewer>();
            if (trafficLight != null)
            {
                TrafficLightViewer.LightColor currentLight = trafficLight.GetCurrentLight();
                if (currentLight == TrafficLightViewer.LightColor.Red || currentLight == TrafficLightViewer.LightColor.Yellow)
                {
                    isStoppedByLight = true;
                }
                else
                {
                    isStoppedByLight = false;
                }
            }
        }
    }

    void OnTriggerExit(Collider other)
    {
        if (other.CompareTag("StopTrigger"))
        {
            isStoppedByLight = false;
            isOnIntersection = false;
            intersectionBlockedAhead = false;
        }
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
}