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

    private RoadSegment currentSegment;
    private int currentWaypointIndex = 0;
    private float originalSpeed;

    // Светофор
    private bool isStoppedByLight = false;
    private bool isOnIntersection = false;
    private string lastStopReason = "Едет";

    // Встречный поток
    private OncomingTrafficDetector oncomingDetector;
    private int stopWaypointIndex = -1;

    void Start()
    {
        originalSpeed = speed;
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

        // Если едем прямо И не на перекрестке (или на перекрестке, но с коротким лучом)
        if (angleToTarget < turnAngleThreshold)
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
        if (isStoppedByLight)
        {
            speed = 0f;
            currentReason = "Стоит перед светофором";
        }
        else if (carDetectedInFront)
        {
            speed = 0f;
            currentReason = "Держит дистанцию";
        }
        else if (oncomingDetector != null && !oncomingDetector.IsClear && currentWaypointIndex == stopWaypointIndex)
        {
            float distanceToStop = Vector3.Distance(transform.position, waypoints[stopWaypointIndex].position);
            if (distanceToStop < 1.5f)
            {
                speed = 0f;
                currentReason = "Пропускает встречку";
            }
        }
        else
        {
            speed = originalSpeed;
        }

        if (currentReason != lastStopReason)
        {
            Debug.Log($"[{gameObject.name}] {currentReason}");
            lastStopReason = currentReason;
        }

        if (speed <= 0f) return;

        // Движение
        Vector3 moveDirection = targetWaypoint.position - transform.position;
        moveDirection.y = 0;

        if (moveDirection != Vector3.zero)
        {
            Quaternion targetRotation = Quaternion.LookRotation(moveDirection);
            transform.rotation = Quaternion.Slerp(transform.rotation, targetRotation, rotationSpeed * Time.deltaTime);
        }

        transform.Translate(Vector3.forward * speed * Time.deltaTime);

        if (Vector3.Distance(transform.position, targetWaypoint.position) < 0.6f)
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

            SetupSegment(nextSegment, false);
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
            isOnIntersection = false;

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
            isOnIntersection = true;
        }
    }
}