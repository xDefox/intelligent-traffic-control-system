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

        if (selectedRoute.routeParent == null || selectedRoute.spawnPoint == null) return;

        // --- НАЧАЛО ПРОВЕРКИ ЗАНЯТОСТИ ТОЧКИ СПАВНА ---
        // Получаем маску слоя Traffic
        int trafficMask = LayerMask.GetMask("Traffic");

        // Полуразмеры коробки проверки. Коробка будет размером 2.4м в ширину, 2м в высоту и 4м в длину.
        // Этого с запасом хватит, чтобы заметить бампер или кузов предыдущей машины.
        Vector3 boxHalfExtents = new Vector3(1.2f, 1f, 2f);

        // Проверяем физическое пространство вокруг выбранной точки спавна
        if (Physics.CheckBox(selectedRoute.spawnPoint.position, boxHalfExtents, selectedRoute.spawnPoint.rotation, trafficMask))
        {
            // Если точка занята другой машиной, просто прерываем метод.
            // Машина не заспавнится, пока точка не освободится.
            Debug.Log($"[TrafficGenerator] Спавн на маршруте '{selectedRoute.routeName}' отменен: точка занята.");
            return;
        }
        // --- КОНЕЦ ПРОВЕРКИ ЗАНЯТОСТИ ---

        GameObject randomCarPrefab = carPrefabs[Random.Range(0, carPrefabs.Count)];

        // Собираем вейпоинты
        List<Transform> extractedWaypoints = new List<Transform>();
        foreach (Transform child in selectedRoute.routeParent)
        {
            extractedWaypoints.Add(child);
        }

        if (extractedWaypoints.Count == 0) return;

        // Спавним машину (теперь это гарантированно безопасно)
        GameObject car = Instantiate(randomCarPrefab, selectedRoute.spawnPoint.position, selectedRoute.spawnPoint.rotation);

        WaypointNavigator navigator = car.GetComponent<WaypointNavigator>();
        if (navigator != null)
        {
            // Передаем вейпоинты, детектор и индекс стоп-точки
            navigator.SetupRoute(extractedWaypoints, selectedRoute.oncomingDetector, selectedRoute.stopWaypointIndex);
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