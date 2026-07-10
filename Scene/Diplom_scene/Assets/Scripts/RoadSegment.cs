using UnityEngine;
using System.Collections.Generic;

public class RoadSegment : MonoBehaviour
{
    [Header("Идентификатор полосы")]
    public string laneId;

    [Header("Вейпоинты этого сегмента")]
    public List<Transform> localWaypoints = new List<Transform>();

    [Header("Доступные направления дальше")]
    public List<RoadSegment> nextPossibleSegments = new List<RoadSegment>();

    [Header("Параметры ПДД для сегмента")]
    public OncomingTrafficDetector oncomingDetector;
    [Tooltip("Индекс вейпоинта ПЕРЕД стоп-линией. Если нет светофора/встречки, оставьте -1")]
    public int stopWaypointIndex = -1;

    [ContextMenu("Собрать дочерние вейпоинты")]
    private void CollectWaypoints()
    {
        localWaypoints.Clear();
        foreach (Transform child in transform)
        {
            if (child.GetComponent<OncomingTrafficDetector>() != null) continue;
            localWaypoints.Add(child);
        }
    }


    void OnDrawGizmos()
    {
        if (!TrafficGenerator.ShowDebugGizmos)
            return;

        if (localWaypoints == null || localWaypoints.Count == 0)
            return;

        // Рисуем линию сегмента
        Gizmos.color = Color.cyan;
        for (int i = 0; i < localWaypoints.Count - 1; i++)
        {
            if (localWaypoints[i] != null && localWaypoints[i + 1] != null)
                Gizmos.DrawLine(localWaypoints[i].position, localWaypoints[i + 1].position);
        }

        // Рисуем связи с другими сегментами
        if (nextPossibleSegments != null && nextPossibleSegments.Count > 0)
        {
            Gizmos.color = Color.green;
            Vector3 endPoint = localWaypoints[localWaypoints.Count - 1].position;
            foreach (var nextSeg in nextPossibleSegments)
            {
                if (nextSeg != null && nextSeg.localWaypoints != null && nextSeg.localWaypoints.Count > 0)
                {
                    Gizmos.DrawLine(endPoint, nextSeg.localWaypoints[0].position);
                }
            }
        }
    }
}