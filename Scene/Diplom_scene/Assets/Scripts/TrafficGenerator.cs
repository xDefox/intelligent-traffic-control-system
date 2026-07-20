using UnityEngine;
using System.Collections.Generic;

public class TrafficGenerator : MonoBehaviour
{
    [System.Serializable]
    public struct SpawnRoute
    {
        public string name;
        [Tooltip("Точка, где физически появится машина (обычно в самом начале дороги)")]
        public Transform spawnPoint;
        [Tooltip("Стартовый waypoint (перетаскивать сюда первый WaypointNode дороги)")]
        public WaypointNode startWaypoint;
    }

    [Header("Настройки префабов машин")]
    public List<GameObject> carPrefabs;

    [Header("Точки спавна на въездах в систему")]
    public List<SpawnRoute> spawnRoutes;

    [Header("Настройки интенсивности")]
    public float spawnInterval = 3f;
    public int maxActiveCars = 50; // Limit total cars to prevent memory issues

    [Header("Настройки отображения")]
    public bool showRoadGizmos = true;
    public static bool ShowDebugGizmos { get; private set; } = true;

    private float timer;
    private int activeCarCount = 0;

    private void OnValidate()
    {
        ShowDebugGizmos = showRoadGizmos;
    }

    private void Awake()
    {
        ShowDebugGizmos = showRoadGizmos;
    }

    [Header("Спавн спецтранспорта по клавише")]
    [Tooltip("Клавиша для спавна полицейской машины")]
    public KeyCode emergencySpawnKey = KeyCode.E;
    [Tooltip("Префаб полицейской машины (опционально). Если не задан — использует случайный из carPrefabs")]
    public GameObject policeCarPrefab;
    [Tooltip("Тег для спецтранспорта")]
    public string emergencyVehicleTag = "EmergencyVehicle";

    [Header("Traffic Constraints")]
    [Tooltip("Менеджер ограничений трафика (блокировка забитых дорог)")]
    public TrafficConstraintsManager trafficConstraints;
    
    [Tooltip("Порог загруженности, выше которого не спавним на въезд (0-1)")]
    [Range(0f, 1f)]
    public float spawnCongestionThreshold = 0.7f;

    void Update()
    {
        timer += Time.deltaTime;
        if (timer >= spawnInterval && activeCarCount < maxActiveCars)
        {
            timer = 0f;
            SpawnRandomVehicle();
        }

        // Спавн полицейской машины по клавише
        try
        {
            if (Input.GetKeyDown(emergencySpawnKey))
            {
                SpawnEmergencyVehicle();
            }
        }
        catch (System.InvalidOperationException)
        {
            // Игнорируем при использовании Input System Package
        }
    }

    /// <summary>
    /// Спавнит полицейскую машину на случайном въезде.
    /// </summary>
    void SpawnEmergencyVehicle()
    {
        if (spawnRoutes.Count == 0) return;

        SpawnRoute selectedRoute = spawnRoutes[Random.Range(0, spawnRoutes.Count)];
        if (selectedRoute.startWaypoint == null || selectedRoute.spawnPoint == null) return;

        // Проверяем, нет ли уже машины на спаунпоинте
        Collider[] hitColliders = Physics.OverlapSphere(selectedRoute.spawnPoint.position, 0.7f);
        foreach (var hit in hitColliders)
        {
            if (hit.CompareTag(emergencyVehicleTag) || hit.CompareTag("Car")) return;
        }

        // Выбираем префаб: полицейский или случайный
        GameObject prefabToSpawn = policeCarPrefab != null ? policeCarPrefab : carPrefabs[Random.Range(0, carPrefabs.Count)];
        
        GameObject car = Instantiate(prefabToSpawn, selectedRoute.spawnPoint.position, selectedRoute.spawnPoint.rotation);
        
        // Добавляем тег спецтранспорта
        car.tag = emergencyVehicleTag;
        
        activeCarCount++;
        
        var cleanup = car.AddComponent<CarCleanupHandler>();
        cleanup.Initialize(this);

        WaypointNavigator navigator = car.GetComponent<WaypointNavigator>();
        if (navigator != null)
        {
            navigator.SetupNode(selectedRoute.startWaypoint);
            navigator.SetEmergencyMode(true); // Включаем игнорирование светофоров
            Debug.Log("[TrafficGenerator] 🚨 Полицейская машина заспавнена! Клавиша E");
        }
    }

    void SpawnRandomVehicle()
    {
        if (carPrefabs.Count == 0 || spawnRoutes.Count == 0) return;

        // Выбираем случайную точку въезда из списка
        SpawnRoute selectedRoute = spawnRoutes[Random.Range(0, spawnRoutes.Count)];

        if (selectedRoute.startWaypoint == null || selectedRoute.spawnPoint == null) return;

        // Проверяем, нет ли уже машины на спаунпоинте (радиус 0.7м)
        Collider[] hitColliders = Physics.OverlapSphere(selectedRoute.spawnPoint.position, 0.7f);
        foreach (var hit in hitColliders)
        {
            if (hit.CompareTag("Car")) return;  // Место занято — не спавним
        }

        // Проверяем загруженность дороги (если trafficConstraints назначен)
        if (trafficConstraints != null)
        {
            string laneId = trafficConstraints.GetLaneIdForWaypoint(selectedRoute.startWaypoint);
            if (!string.IsNullOrEmpty(laneId))
            {
                // laneId формат: "lane_intersection_1_approach_0"
                // Нужно извлечь intersection_id и direction
                string interId = laneId;
                string direction = "";
                
                if (interId.StartsWith("lane_"))
                {
                    interId = interId.Substring(4);
                }
                
                int idx = interId.LastIndexOf("_approach_");
                if (idx > 0)
                {
                    direction = interId.Substring(idx + 9); // После "_approach_"
                    interId = interId.Substring(0, idx);
                }
                
                // Проверяем загруженность
                if (!trafficConstraints.IsRoadAvailable(interId, direction))
                {
                    // Дорога забита - не спавним
                    return;
                }
            }
        }

        // Спавним случайную машину из доступных префабов
        GameObject randomCarPrefab = carPrefabs[Random.Range(0, carPrefabs.Count)];
        GameObject car = Instantiate(randomCarPrefab, selectedRoute.spawnPoint.position, selectedRoute.spawnPoint.rotation);

        activeCarCount++;
        
        // Auto-cleanup when car is destroyed
        var cleanup = car.AddComponent<CarCleanupHandler>();
        cleanup.Initialize(this);

        WaypointNavigator navigator = car.GetComponent<WaypointNavigator>();
        if (navigator != null)
        {
            // Передаем машине начальный waypoint
            navigator.SetupNode(selectedRoute.startWaypoint);
        }
    }
    
    // Called by CarCleanupHandler when a car is destroyed
    public void OnCarDestroyed()
    {
        activeCarCount = Mathf.Max(0, activeCarCount - 1);
    }

    // Отрисовка зон спавна в редакторе
    void OnDrawGizmosSelected()
    {
        if (spawnRoutes == null) return;

        Gizmos.color = Color.yellow;
        foreach (var route in spawnRoutes)
        {
            if (route.spawnPoint != null)
            {
                Gizmos.matrix = Matrix4x4.TRS(route.spawnPoint.position, route.spawnPoint.rotation, Vector3.one);
                Gizmos.DrawWireCube(Vector3.zero, new Vector3(2.4f, 2f, 4f));
            }
        }
    }
}