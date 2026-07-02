using UnityEngine;
using System.Collections.Generic;

public class WaypointNavigator : MonoBehaviour
{
    public List<Transform> waypoints = new List<Transform>();
    public float speed = 5f;
    public float rotationSpeed = 10f;

    private int currentWaypointIndex = 0;

    // Флаги состояния движения
    private bool isStoppedByLight = false;
    private bool isStoppedByCarInFront = false;
    private float originalSpeed;

    private OncomingTrafficDetector oncomingDetector;
    private int stopWaypointIndex = 0;

    // Для отслеживания текущего состояния логирования
    private string lastStopReason = "Едет";

    void Start()
    {
        originalSpeed = speed;
    }

    public void SetupRoute(List<Transform> routePoints, OncomingTrafficDetector detector, int stopIndex)
    {
        waypoints = new List<Transform>(routePoints);
        oncomingDetector = detector;
        stopWaypointIndex = stopIndex;
        currentWaypointIndex = 0;

        if (waypoints.Count > 0)
        {
            transform.LookAt(new Vector3(waypoints[0].position.x, transform.position.y, waypoints[0].position.z));
        }
    }

    void Update()
    {
        if (waypoints == null || waypoints.Count == 0) return;

        string currentReason = "Едет";

        // 1. Проверка светофора
        if (isStoppedByLight)
        {
            speed = 0f;
            currentReason = "Стоит перед светофором (StopTrigger)";
        }
        // 2. Проверка дистанции до машины впереди
        else if (isStoppedByCarInFront)
        {
            speed = 0f;
            currentReason = "Держит дистанцию (Машина впереди в зоне бампера)";
        }
        // 3. Проверка детектора встречки у стоп-линии
        else if (oncomingDetector != null && !oncomingDetector.IsClear && currentWaypointIndex == stopWaypointIndex)
        {
            float distanceToStop = Vector3.Distance(transform.position, waypoints[stopWaypointIndex].position);
            if (distanceToStop < 1.5f)
            {
                speed = 0f;
                currentReason = "Пропускает встречный поток (Детектор занят)";
            }
        }
        else
        {
            speed = originalSpeed;
        }

        // Выводим лог в консоль только при изменении поведения машины, чтобы не спамить
        if (currentReason != lastStopReason)
        {
            Debug.Log($"[{gameObject.name}] Изменил состояние: {currentReason}. Текущий вейпоинт: {currentWaypointIndex}");
            lastStopReason = currentReason;
        }

        if (speed <= 0f) return;

        // Логика движения к текущему вейпоинту
        Transform targetWaypoint = waypoints[currentWaypointIndex];
        Vector3 direction = targetWaypoint.position - transform.position;
        direction.y = 0;

        if (direction != Vector3.zero)
        {
            Quaternion targetRotation = Quaternion.LookRotation(direction);
            transform.rotation = Quaternion.Slerp(transform.rotation, targetRotation, rotationSpeed * Time.deltaTime);
        }

        transform.Translate(Vector3.forward * speed * Time.deltaTime);

        if (Vector3.Distance(transform.position, targetWaypoint.position) < 0.5f)
        {
            currentWaypointIndex++;
            if (currentWaypointIndex >= waypoints.Count)
            {
                Destroy(gameObject);
            }
        }
    }

    // Обработка внешних триггеров (Светофор и Бампер)
    void OnTriggerStay(Collider other)
    {
        // Проверка светофора
        if (other.CompareTag("StopTrigger"))
        {
            TrafficLightViewer trafficLight = other.GetComponentInParent<TrafficLightViewer>();
            if (trafficLight != null)
            {
                if (trafficLight.GetCurrentLight() == TrafficLightViewer.LightColor.Red ||
                    trafficLight.GetCurrentLight() == TrafficLightViewer.LightColor.Yellow)
                {
                    isStoppedByLight = true;
                    return;
                }
            }
            isStoppedByLight = false;
        }
    }

    void OnTriggerExit(Collider other)
    {
        if (other.CompareTag("StopTrigger"))
        {
            isStoppedByLight = false;
        }
    }

    // Эти методы будут вызываться из дочернего датчика-бампера
    public void SetCarInFrontTrigger(bool isBlocked)
    {
        isStoppedByCarInFront = isBlocked;
    }
}