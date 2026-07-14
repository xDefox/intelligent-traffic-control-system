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

    [Header("Визуализация")]
    public Color gizmoColor = Color.cyan;
    public float gizmoSize = 1.0f;

    private void OnDrawGizmos()
    {
        if (!TrafficGenerator.ShowDebugGizmos)
            return;

        // Рисуем этот waypoint
        Gizmos.color = isIntersection ? Color.yellow : gizmoColor;
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
                    // Рисуем стрелку в направлении соседа
                    Vector3 midPoint = (transform.position + neighbour.transform.position) * 0.5f;
                    Gizmos.DrawSphere(midPoint, gizmoSize * 0.15f);
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