using UnityEngine;
using System.Collections.Generic;

public class WaypointNavigator : MonoBehaviour
{
    [Header("Настройки движения")]
    public List<Transform> waypoints = new List<Transform>();
    public float speed = 12f;  // Увеличена скорость для более быстрого движения
    public float rotationSpeed = 15f;  // Увеличена скорость поворота

    [Header("Дистанция и Векторы")]
    [Tooltip("Дистанция до машины впереди (2.2 — идеальный плотный поджим)")]
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
    [Tooltip("Минимальное расстояние до впереди идущей машины при котором резко тормозим (не полагаясь на Lerp)")]
    public float emergencyBrakeDistance = 1.5f;
    [Tooltip("Скорость сброса при аварийном торможении (чем больше, тем резче)")]
    public float emergencyBrakeStrength = 30f;

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

    // Счётчики для определения "застревания" (2 секунды на месте)
    private float stuckTimer = 0f;
    private const float STUCK_TIME_THRESHOLD = 2.0f;
    private Vector3 lastPosition;

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
            rb.isKinematic = true; // We control movement manually
            rb.useGravity = false; // Disable gravity for kinematic control
        }
        
        // Disable WheelColliders to prevent physics conflicts
        wheelColliders = GetComponentsInChildren<WheelCollider>();
        if (wheelColliders != null && wheelColliders.Length > 0)
        {
            foreach (var wc in wheelColliders)
            {
                wc.enabled = false;
            }
            Debug.Log($"[{gameObject.name}] Disabled {wheelColliders.Length} WheelColliders to prevent movement conflicts");
        }
    }

    public void SetupSegment(RoadSegment segment, bool isInitialSpawn = false)
    {
        // Исправленная проверка: сегмент не null, список точек существует и не пуст
        if (segment == null || segment.localWaypoints == null || segment.localWaypoints.Count == 0) return;

        currentSegment = segment;
        waypoints = new List<Transform>(segment.localWaypoints);
        oncomingDetector = segment.oncomingDetector;
        stopWaypointIndex = segment.stopWaypointIndex;
        currentWaypointIndex = 0;
        isOnIntersection = false;

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

        string currentReason = "Едет";
        Transform targetWaypoint = waypoints[currentWaypointIndex];

        if (targetWaypoint == null) return;

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
        Vector3 rayEnd = rayStart + transform.forward * actualMaxDistance;

        // Используем SphereCast вместо Raycast для более надёжного детектирования
        // (ловит машины не только строго по лучу, но и сбоку)
        if (angleToTarget < turnAngleThreshold)
        {
            RaycastHit hit;
            int layerMask = CreateLayerMask();

            Debug.DrawLine(rayStart, rayEnd, Color.red);

            // SphereCast — более надёжный, чем Raycast
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
        else if (carDetectedInFront)
        {
            // Аварийное торможение, если слишком близко
            if (detectedCarDistance < emergencyBrakeDistance)
            {
                emergencyStop = true;
                currentReason = $"Аварийное торможение ({detectedCarDistance:F2}м)";
            }
            else
            {
                // Пропорциональное замедление: чем ближе, тем сильнее жмём тормоз
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
            // Smoothly interpolate speed to prevent jerky changes
            speed = Mathf.Lerp(speed, targetSpeed, speedSmoothing * Time.deltaTime);
        }

        // Защита от застревания: если машина стоит на месте >2 секунд, пытаемся её расшевелить
        float movedDistance = Vector3.Distance(transform.position, lastPosition);
        if (movedDistance < 0.01f && speed > 0.1f)
        {
            stuckTimer += Time.deltaTime;
            if (stuckTimer > STUCK_TIME_THRESHOLD)
            {
                // Впереди что-то мешает — пытаемся объехать
                Debug.Log($"[{gameObject.name}] Застрял, пытаюсь объехать препятствие");
                speed = originalSpeed * 0.3f;
                
                // Поворачиваем слегка, чтобы обойти
                transform.Rotate(0, 30 * Time.deltaTime, 0);
            }
        }
        else
        {
            stuckTimer = 0f;
        }
        lastPosition = transform.position;

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

        // Движение
        Vector3 moveDirection = targetWaypoint.position - transform.position;
        moveDirection.y = 0;
        float distanceToWaypoint = moveDirection.magnitude;
        moveDirection.Normalize();

        // Look ahead to next waypoint for smoother turns
        Vector3 lookAheadDirection = moveDirection;
        if (currentWaypointIndex + 1 < waypoints.Count && waypoints[currentWaypointIndex + 1] != null)
        {
            Vector3 nextWaypointDir = (waypoints[currentWaypointIndex + 1].position - targetWaypoint.position).normalized;
            nextWaypointDir.y = 0;
            // Blend current direction with next waypoint direction for smoother turning
            lookAheadDirection = Vector3.Lerp(moveDirection, nextWaypointDir, 0.3f).normalized;
        }

        if (lookAheadDirection != Vector3.zero)
        {
            Quaternion targetRotation = Quaternion.LookRotation(lookAheadDirection);
            transform.rotation = Quaternion.Slerp(transform.rotation, targetRotation, rotationSpeed * Time.deltaTime);
        }

        // Move using physics if available, otherwise transform
        if (usePhysics && rb != null)
        {
            rb.MovePosition(rb.position + moveDirection * speed * Time.deltaTime);
        }
        else
        {
            // Move in the direction of the waypoint (world space) instead of local forward
            transform.Translate(moveDirection * speed * Time.deltaTime, Space.World);
        }

        if (distanceToWaypoint < 0.6f)
        {
            currentWaypointIndex++;

            if (currentWaypointIndex >= waypoints.Count)
            {
                SwitchToNextSegment();
            }
        }
    }

    private void SwitchToNextSegment()
    {
        if (currentSegment != null && currentSegment.nextPossibleSegments != null && currentSegment.nextPossibleSegments.Count > 0)
        {
            // Случайный выбор следующего направления на развилке
            int randomIndex = Random.Range(0, currentSegment.nextPossibleSegments.Count);
            RoadSegment nextSegment = currentSegment.nextPossibleSegments[randomIndex];

            // Store current position before switching
            Vector3 carPosition = transform.position;
            
            SetupSegment(nextSegment, false);
            
            // Smoothly orient car towards first waypoint of new segment
            if (waypoints.Count > 0 && waypoints[0] != null)
            {
                Vector3 lookTarget = new Vector3(waypoints[0].position.x, carPosition.y, waypoints[0].position.z);
                Quaternion targetRotation = Quaternion.LookRotation(lookTarget - carPosition);
                transform.rotation = Quaternion.Slerp(transform.rotation, targetRotation, 0.5f);
            }

            // Start transition period to prevent epileptic movement
            isTransitioning = true;
            transitionTimer = segmentTransitionSmoothness;
        }
        else
        {
            // Если дорожная сеть закончилась
            Destroy(gameObject);
        }
    }

    void OnTriggerStay(Collider other)
    {
        if (other.CompareTag("StopTrigger"))
        {
            isOnIntersection = true;  // FIXED: Was false, should be true when inside trigger

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
            isOnIntersection = false;  // FIXED: Was true, should be false when exiting trigger
        }
    }
    
    void OnDestroy()
    {
        // Re-enable WheelColliders when car is destroyed (cleanup)
        if (wheelColliders != null)
        {
            foreach (var wc in wheelColliders)
            {
                if (wc != null) wc.enabled = true;
            }
        }
        
        // Cleanup Rigidbody
        if (rb != null)
        {
            rb.isKinematic = false;
            rb.useGravity = true;
        }
    }
}