using UnityEngine;
using System.Collections.Generic;

public class TrafficGenerator : MonoBehaviour
{
    [System.Serializable]
    public struct RouteData
    {
        public string routeName;
        public Transform spawnPoint;
        public Transform routeParent;

        [Header("Настройки ПДД")]
        public OncomingTrafficDetector oncomingDetector;

        [Tooltip("Индекс вейпоинта (0, 1, 2...), который лежит ПЕРЕД стоп-линией этого маршрута")]
        public int stopWaypointIndex;
    }

    [Header("Настройки префабов")]
    public List<GameObject> carPrefabs; 

    [Header("Все возможные маршруты перекрестка")]
    public List<RouteData> allRoutes; 

    [Header("Настройки интенсивности")]
    public float spawnInterval = 3f;

    private float timer;

    void Update()
    {
        timer += Time.deltaTime;
        if (timer >= spawnInterval)
        {
            timer = 0f;
            SpawnRandomVehicle();
        }
    }

    void SpawnRandomVehicle()
    {
        if (carPrefabs.Count == 0 || allRoutes.Count == 0) return;

        GameObject randomCarPrefab = carPrefabs[Random.Range(0, carPrefabs.Count)];
        RouteData selectedRoute = allRoutes[Random.Range(0, allRoutes.Count)];

        if (selectedRoute.routeParent == null || selectedRoute.spawnPoint == null) return;

        // Собираем вейпоинты
        List<Transform> extractedWaypoints = new List<Transform>();
        foreach (Transform child in selectedRoute.routeParent)
        {
            extractedWaypoints.Add(child);
        }

        if (extractedWaypoints.Count == 0) return;

        // Спавним машину
        GameObject car = Instantiate(randomCarPrefab, selectedRoute.spawnPoint.position, selectedRoute.spawnPoint.rotation);

        WaypointNavigator navigator = car.GetComponent<WaypointNavigator>();
        if (navigator != null)
        {
            // Передаем вейпоинты, детектор и индекс стоп-точки
            navigator.SetupRoute(extractedWaypoints, selectedRoute.oncomingDetector, selectedRoute.stopWaypointIndex);
        }
    }
}