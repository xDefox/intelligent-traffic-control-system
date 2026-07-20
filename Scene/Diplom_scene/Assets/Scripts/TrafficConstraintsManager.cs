using UnityEngine;
using System.Collections.Generic;
using System.Collections;
using UnityEngine.Networking;

/// <summary>
/// Traffic Constraints Manager - блокирует переполненные дороги.
/// 
/// Camera-First Design: автоматически определяет laneId по геометрии waypoint'а.
/// Масштабируется на многополосное движение (каждая полоса = отдельная камера).
/// </summary>
public class TrafficConstraintsManager : MonoBehaviour
{
    [Header("Настройки обновления")]
    [Tooltip("Интервал обновления congestion_map (секунды)")]
    public float updateInterval = 2.0f;
    
    [Tooltip("Порог загруженности, выше которого дорога считается забитой (0-1)")]
    [Range(0f, 1f)]
    public float congestionThreshold = 0.7f;
    
    [Header("Настройки сервера")]
    public string backendUrl = "http://127.0.0.1:8050";
    
    // Карта загруженности: laneId -> congestion_index (0.0 = пусто, 1.0 = максимум)
    private Dictionary<string, float> congestionMap = new Dictionary<string, float>();
    
    // Карта топологии: waypoint position -> (intersection_id, direction)
    private Dictionary<Vector3, (string interId, string direction)> waypointToLane = 
        new Dictionary<Vector3, (string, string)>();
    
    // Позиции перекрёстков (можно загрузить из JSON)
    private Dictionary<string, Vector2> intersectionPositions = new Dictionary<string, Vector2>();
    
    private float lastUpdate = 0f;
    private bool isInitialized = false;
    
    void Start()
    {
        // Инициализируем позиции перекрёстков из IntersectionVisionManager'ов
        InitializeIntersectionPositions();
        
        // Запускаем обновление congestion_map
        StartCoroutine(UpdateCongestionMapLoop());
        
        isInitialized = true;
    }
    
    /// <summary>
    /// Инициализация позиций перекрёстков из сцены.
    /// </summary>
    private void InitializeIntersectionPositions()
    {
        var visionManagers = FindObjectsOfType<IntersectionVisionManager>();
        
        foreach (var vm in visionManagers)
        {
            if (!string.IsNullOrEmpty(vm.intersectionId))
            {
                Vector2 pos = new Vector2(vm.transform.position.x, vm.transform.position.z);
                intersectionPositions[vm.intersectionId] = pos;
            }
        }
        
        Debug.Log($"[TrafficConstraintsManager] Найдено {intersectionPositions.Count} перекрёстков");
    }
    
    /// <summary>
    /// Цикл обновления congestion_map.
    /// </summary>
    private IEnumerator UpdateCongestionMapLoop()
    {
        while (true)
        {
            yield return StartCoroutine(FetchCongestionMap());
            yield return new WaitForSeconds(updateInterval);
        }
    }
    
    /// <summary>
    /// Запрос congestion_map с бэкенда.
    /// </summary>
    private IEnumerator FetchCongestionMap()
    {
        string url = $"{backendUrl}/api/v1/congestion-map";
        
        using (UnityWebRequest request = UnityWebRequest.Get(url))
        {
            request.timeout = 3;
            yield return request.SendWebRequest();
            
            if (request.result == UnityWebRequest.Result.Success)
            {
                ParseCongestionMap(request.downloadHandler.text);
            }
            else
            {
                Debug.LogWarning($"[TrafficConstraintsManager] Не удалось обновить congestion_map: {request.error}");
            }
        }
    }
    
    /// <summary>
    /// Парсинг JSON ответа.
    /// </summary>
    private void ParseCongestionMap(string json)
    {
        // Простой парсинг JSON: {"lane_intersection_1_approach_0": 0.6, ...}
        congestionMap.Clear();
        
        // Убираем фигурные скобки и парсим
        json = json.Trim();
        if (json.StartsWith("{")) json = json.Substring(1);
        if (json.EndsWith("}")) json = json.Substring(0, json.Length - 1);
        
        // Разделяем по запятым
        string[] pairs = json.Split(',');
        foreach (string pair in pairs)
        {
            string[] kv = pair.Split(':');
            if (kv.Length == 2)
            {
                string key = kv[0].Trim().Trim('"');
                if (float.TryParse(kv[1].Trim(), out float value))
                {
                    congestionMap[key] = value;
                }
            }
        }
    }
    
    /// <summary>
    /// Проверить, доступна ли дорога для движения.
    /// </summary>
    /// <param name="intersectionId">ID перекрёстка (например, "intersection_1")</param>
    /// <param name="approach">Номер подхода (approach_0, approach_1, approach_2, approach_3)</param>
    /// <returns>true если дорога свободна, false если забита</returns>
    public bool IsRoadAvailable(string intersectionId, string approach)
    {
        if (!isInitialized) return true;
        
        // Формируем laneId в формате backend
        string laneId = $"lane_{intersectionId}_{approach}";
        
        if (congestionMap.TryGetValue(laneId, out float congestion))
        {
            return congestion < congestionThreshold;
        }
        
        // Если данных нет - считаем дорогу доступной
        return true;
    }
    
    /// <summary>
    /// Получить индекс загруженности дороги.
    /// </summary>
    public float GetRoadCongestion(string intersectionId, string approach)
    {
        string laneId = $"lane_{intersectionId}_{approach}";
        
        if (congestionMap.TryGetValue(laneId, out float congestion))
        {
            return congestion;
        }
        
        return 0f;
    }
    
    /// <summary>
    /// Определить laneId для waypoint'а по геометрии.
    /// Возвращает: "lane_intersection_1_approach_0" (с цифрой, а не буквой!)
    /// </summary>
    public string GetLaneIdForWaypoint(WaypointNode waypoint)
    {
        if (waypoint == null) return "";
        
        // Находим ближайший перекрёсток
        string nearestInter = FindNearestIntersection(waypoint.transform.position);
        if (string.IsNullOrEmpty(nearestInter)) return "";
        
        // Определяем направление из forward-вектора
        string directionLetter = GetDirectionFromForward(waypoint.transform.forward);
        
        // Преобразуем букву в номер approach (E=0, W=1, N=2, S=3)
        string approach = DirectionToApproach(directionLetter);
        
        return $"lane_{nearestInter}_{approach}";
    }
    
    /// <summary>
    /// Преобразовать направление (N/S/E/W) в approach (approach_0/1/2/3).
    /// </summary>
    private string DirectionToApproach(string direction)
    {
        // E=0, W=1, N=2, S=3 (как в backend/services/graph_manager.py)
        switch (direction)
        {
            case "E": return "approach_0";
            case "W": return "approach_1";
            case "N": return "approach_2";
            case "S": return "approach_3";
            default: return "approach_0";
        }
    }
    
    /// <summary>
    /// Найти ближайший перекрёсток по позиции.
    /// </summary>
    private string FindNearestIntersection(Vector3 position)
    {
        string nearest = "";
        float minDist = float.MaxValue;
        
        foreach (var kvp in intersectionPositions)
        {
            float dist = Vector2.Distance(
                new Vector2(position.x, position.z), 
                kvp.Value
            );
            
            // Порог 15м - waypoint'ы на перекрёстке
            if (dist < minDist && dist < 15f)
            {
                minDist = dist;
                nearest = kvp.Key;
            }
        }
        
        return nearest;
    }
    
    /// <summary>
    /// Определить направление из forward-вектора.
    /// </summary>
    private string GetDirectionFromForward(Vector3 forward)
    {
        Vector3 f = forward.normalized;
        
        if (Mathf.Abs(f.x) > Mathf.Abs(f.z))
        {
            return f.x > 0 ? "E" : "W";
        }
        else
        {
            return f.z > 0 ? "N" : "S";
        }
    }
    
    /// <summary>
    /// Получить список доступных соседей (с учётом загруженности).
    /// </summary>
    public List<WaypointNode> GetAvailableNeighbours(WaypointNode currentNode, string currentIntersectionId)
    {
        List<WaypointNode> available = new List<WaypointNode>();
        
        if (currentNode == null || currentNode.neighbours == null)
            return available;
        
        foreach (var neighbour in currentNode.neighbours)
        {
            if (neighbour == null) continue;
            
            // Определяем, куда ведёт этот neighbour
            string targetInterId = FindNearestIntersection(neighbour.transform.position);
            
            // Если neighbour ведёт на другой перекрёсток
            if (!string.IsNullOrEmpty(targetInterId) && targetInterId != currentIntersectionId)
            {
                // Определяем направление и преобразуем в approach
                string directionLetter = GetDirectionFromForward(neighbour.transform.forward);
                string approach = DirectionToApproach(directionLetter);
                
                // Проверяем загруженность
                if (IsRoadAvailable(targetInterId, approach))
                {
                    available.Add(neighbour);
                }
            }
            else
            {
                // Если это не перекрёсток - добавляем без проверки
                available.Add(neighbour);
            }
        }
        
        return available;
    }
    
    /// <summary>
    /// Получить текущую карту загруженности (для отладки).
    /// </summary>
    public Dictionary<string, float> GetCongestionMap()
    {
        return new Dictionary<string, float>(congestionMap);
    }
}