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

        RouteData selectedRoute = allRoutes[Random.Range(0, allRoutes.Count)];

        // Теперь нам нужен стартовый RoadSegment вместо routeParent
        RoadSegment startSegment = selectedRoute.routeParent.GetComponent<RoadSegment>();
        if (startSegment == null || selectedRoute.spawnPoint == null) return;

        GameObject randomCarPrefab = carPrefabs[Random.Range(0, carPrefabs.Count)];
        GameObject car = Instantiate(randomCarPrefab, selectedRoute.spawnPoint.position, selectedRoute.spawnPoint.rotation);

        WaypointNavigator navigator = car.GetComponent<WaypointNavigator>();
        if (navigator != null)
        {
            // Инициализируем через новый метод и передаем true для первичного LookAt
            navigator.SetupSegment(startSegment, true);
        }
    }

    // Отрисовка зон спавна в редакторе для удобства настройки
    void OnDrawGizmosSelected()
    {
        if (allRoutes == null) return;

        Gizmos.color = Color.yellow;
        foreach (var route in allRoutes)
        {
            if (route.spawnPoint != null)
            {
                // Устанавливаем матрицу гизмо под позицию и поворот каждой точки спавна
                Gizmos.matrix = Matrix4x4.TRS(route.spawnPoint.position, route.spawnPoint.rotation, Vector3.one);
                // Рисуем рамку зоны проверки (размеры соответствуют boxHalfExtents * 2)
                Gizmos.DrawWireCube(Vector3.zero, new Vector3(2.4f, 2f, 4f));
            }
        }
    }
}