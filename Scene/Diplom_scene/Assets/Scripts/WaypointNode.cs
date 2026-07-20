using UnityEngine;
using System.Collections.Generic;
using System.Linq;

/// <summary>
/// Узел в глобальном графе waypoints. Хранит ссылки на возможные следующие waypoints.
/// </summary>
public class WaypointNode : MonoBehaviour
{
    [Header("Соседние waypoints (куда можно ехать отсюда)")]
    [Tooltip("Список возможных направлений дальше по маршруту")]
    public List<WaypointNode> neighbours = new List<WaypointNode>();

    [Header("Параметры узла")]
    [Tooltip("Это перекрёсток/развилка? (для отладки)")]
    public bool isIntersection = false;
    
    [Header("Traffic Constraints")]
    [Tooltip("ID полосы (автоматически определяется из позиции и направления)")]
    public string laneId = "";
    
    [Tooltip("Признак выхода с перекрёстка (для блокировки забитых дорог)")]
    public bool isExitPoint = false;
    
    [Tooltip("Индекс камеры, отвечающей за эту полосу (0-3)")]
    public int cameraIndex = -1;

    [Header("Визуализация")]
    public float gizmoSize = 1.0f;

    private void OnDrawGizmos()
    {
        if (!TrafficGenerator.ShowDebugGizmos)
            return;

        // Рисуем этот waypoint
        Gizmos.color = isIntersection ? Color.yellow : Color.cyan;
        Gizmos.DrawSphere(transform.position, gizmoSize * 0.3f);

        // Рисуем связи с соседями
        if (neighbours != null && neighbours.Count > 0)
        {
            Gizmos.color = Color.green;
            foreach (var neighbour in neighbours)
            {
                if (neighbour != null)
                {
                    Gizmos.DrawLine(transform.position, neighbour.transform.position);
                    // Рисуем стрелку в середине отрезка, указывающую в сторону соседа
                    Vector3 midPoint = (transform.position + neighbour.transform.position) * 0.5f;
                    Vector3 direction = (neighbour.transform.position - transform.position).normalized;
                    float arrowLength = gizmoSize * 0.3f;
                    float arrowAngle = 30f;

                    // Основная линия стрелки
                    Gizmos.DrawRay(midPoint, direction * arrowLength);

                    // Усики стрелки (два отрезка под углом назад)
                    Vector3 right = Quaternion.LookRotation(direction) * Quaternion.Euler(0, 180 - arrowAngle, 0) * Vector3.forward;
                    Vector3 left = Quaternion.LookRotation(direction) * Quaternion.Euler(0, 180 + arrowAngle, 0) * Vector3.forward;
                    Gizmos.DrawRay(midPoint + direction * arrowLength, right * arrowLength * 0.5f);
                    Gizmos.DrawRay(midPoint + direction * arrowLength, left * arrowLength * 0.5f);
                }
            }
        }
    }

    /// <summary>
    /// Получить случайного соседа (для обычного движения)
    /// </summary>
    public WaypointNode GetRandomNeighbour()
    {
        if (neighbours == null || neighbours.Count == 0)
            return null;

        // Убираем null-ссылки
        neighbours = neighbours.Where(n => n != null).ToList();

        if (neighbours.Count == 0)
            return null;

        // Возвращаем случайного соседа
        // ВАЖНО: настраивайте граф правильно:
        // - На прямых дорогах: только 1 сосед (вперёд по направлению движения)
        // - На развилках: все возможные направления
        return neighbours[Random.Range(0, neighbours.Count)];
    }

    /// <summary>
    /// Получить соседа по индексу (если нужно детерминированное движение)
    /// </summary>
    public WaypointNode GetNeighbour(int index)
    {
        if (neighbours == null || index < 0 || index >= neighbours.Count)
            return null;

        return neighbours[index];
    }

    /// <summary>
    /// Количество доступных направлений
    /// </summary>
    public int NeighbourCount
    {
        get
        {
            if (neighbours == null) return 0;
            return neighbours.Count(n => n != null);
        }
    }

    [ContextMenu("Найти nearby waypoints")]
    public void FindNearbyWaypoints()
    {
        // Ищем все waypoints в радиусе 5м
        WaypointNode[] allWaypoints = FindObjectsOfType<WaypointNode>();
        
        int addedCount = 0;

        foreach (var wp in allWaypoints)
        {
            if (wp == this) continue;
            if (neighbours.Contains(wp)) continue; // Уже добавлен

            float distance = Vector3.Distance(transform.position, wp.transform.position);
            if (distance < 5.0f)
            {
                neighbours.Add(wp);
                addedCount++;
            }
        }

        Debug.Log($"[{gameObject.name}] Добавлено {addedCount} новых соседей, всего {neighbours.Count}");
    }

    [ContextMenu("Очистить соседей")]
    public void ClearNeighbours()
    {
        neighbours.Clear();
    }
}