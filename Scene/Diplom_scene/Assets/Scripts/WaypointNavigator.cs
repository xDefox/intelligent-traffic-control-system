using UnityEngine;
using System.Collections.Generic;

public class WaypointNavigator : MonoBehaviour
{
    [Header("Настройки движения")]
    public List<Transform> waypoints = new List<Transform>();
    public float speed = 5f;
    public float rotationSpeed = 10f;

    [Header("Дистанция и Векторы")]
    [Tooltip("Дистанция до машины впереди (2.2 — идеальный плотный поджим)")]
    public float maxCheckDistance = 2.2f;
    [Tooltip("Угол поворота (в градусах), при котором луч полностью гаснет")]
    public float turnAngleThreshold = 15f;

    [Header("Плавность переходов")]
    [Tooltip("Время плавного перехода между сегментами (сек)")]
    public float segmentTransitionSmoothness = 0.3f;
    [Tooltip("Сглаживание изменения скорости (меньше = быстрее)")]
    public float speedSmoothing = 5f;

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

    // Frame counter for Raycast throttling (every 3rd frame when not on intersection)
    private int raycastFrameCounter = 0;
    private const int RAYCAST_INTERVAL = 3; // Process raycast every N frames when not on intersection

    void Start()
    {
        originalSpeed = speed;
        currentSpeed = speed;
        
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

        // ЕСЛИ МЫ НА ПЕРЕКРЕСТКЕ: укорачиваем луч до минимума, чтобы не бить в бока
        if (isOnIntersection)
        {
            actualMaxDistance = 0.5f;
        }

        // Считаем угол до цели
        Vector3 directionToTarget = (targetWaypoint.position - transform.position).normalized;
        directionToTarget.y = 0;
        float angleToTarget = Vector3.Angle(transform.forward, directionToTarget);

        bool carDetectedInFront = false;

        Vector3 rayStart = transform.position + Vector3.up * 0.4f + transform.forward * 0.6f;
        Vector3 rayEnd = rayStart + transform.forward * actualMaxDistance;

        // Throttle Physics.Raycast: на перекрёстке проверяем каждый кадр,
        // вне перекрёстка — раз в 3 кадра (экономия CPU).
        raycastFrameCounter++;
        bool shouldRaycast = isOnIntersection || (raycastFrameCounter % RAYCAST_INTERVAL == 0);

        // Если едем прямо И не на перекрестке (или на перекрестке, но с коротким лучом)
        if (shouldRaycast && angleToTarget < turnAngleThreshold)
        {
            RaycastHit hit;
            int layerMask = CreateLayerMask();

            Debug.DrawLine(rayStart, rayEnd, Color.red);

            if (Physics.Raycast(rayStart, transform.forward, out hit, actualMaxDistance, layerMask))
            {
                if (hit.collider.gameObject != gameObject)
                {
                    carDetectedInFront = true;
                }
            }
        }
        else
        {
            Debug.DrawLine(rayStart, rayStart + transform.forward * 0.5f, Color.green);
        }

        // Логика торможения
        float targetSpeed = originalSpeed;
        if (isStoppedByLight)
        {
            targetSpeed = 0f;
            currentReason = "Стоит перед светофором";
        }
        else if (carDetectedInFront)
        {
            targetSpeed = 0f;
            currentReason = "Держит дистанцию";
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

        // Smoothly interpolate speed to prevent jerky changes
        speed = Mathf.Lerp(speed, targetSpeed, speedSmoothing * Time.deltaTime);

        // Removed Debug.Log spam - was logging every state change every frame
        // if (currentReason != lastStopReason)
        // {
        //     Debug.Log($"[{gameObject.name}] {currentReason}");
        //     lastStopReason = currentReason;
        // }

        if (speed < 0.1f) return;

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
