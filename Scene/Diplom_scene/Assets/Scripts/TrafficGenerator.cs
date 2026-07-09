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
        [Tooltip("Стартовая полоса (перетаскивать сюда объект Lane_Forward нужной дороги)")]
        public RoadSegment startSegment;
    }

    [Header("Настройки префабов машин")]
    public List<GameObject> carPrefabs;

    [Header("Точки спавна на въездах в систему")]
    public List<SpawnRoute> spawnRoutes;

    [Header("Настройки интенсивности")]
    public float spawnInterval = 3f;

    [Header("Настройки отображения")]
    public bool showRoadGizmos = true;
    public static bool ShowDebugGizmos { get; private set; } = true;

    private float timer;

    private void OnValidate()
    {
        ShowDebugGizmos = showRoadGizmos;
    }

    private void Awake()
    {
        ShowDebugGizmos = showRoadGizmos;
    }

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
        if (carPrefabs.Count == 0 || spawnRoutes.Count == 0) return;

        // Выбираем случайную точку въезда из списка
        SpawnRoute selectedRoute = spawnRoutes[Random.Range(0, spawnRoutes.Count)];

        if (selectedRoute.startSegment == null || selectedRoute.spawnPoint == null) return;

        // Спавним случайную машину из доступных префабов
        GameObject randomCarPrefab = carPrefabs[Random.Range(0, carPrefabs.Count)];
        GameObject car = Instantiate(randomCarPrefab, selectedRoute.spawnPoint.position, selectedRoute.spawnPoint.rotation);

        WaypointNavigator navigator = car.GetComponent<WaypointNavigator>();
        if (navigator != null)
        {
            // Передаем машине сегмент дороги. Настройки ПДД она подтянет из него автоматически
            navigator.SetupSegment(selectedRoute.startSegment, true);
        }
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