using UnityEngine;
using System.Collections.Generic;

/// <summary>
/// Компонент для определения занятости целевой дороги на перекрёстке.
/// Повторяет логику IntersectionRightOfWay, но для проверки дороги, 
/// куда поворачивает машина (не справа, а по целевому направлению).
/// 
/// Использование:
/// 1. На перекрёстке создаём 4 объекта-детектора (по одному на каждое направление: N, S, E, W)
/// 2. Каждому детектору задаём targetDirection = "N", "S", "E" или "W"
/// 3. WaypointNavigator проверяет IsTargetRoadOccupied() перед тем как продолжить движение после поворота
/// </summary>
public class IntersectionTargetDetector : MonoBehaviour
{
    [Header("Направление целевой дороги")]
    [Tooltip("N, S, E, или W - направление дороги, которую нужно проверять")]
    public string targetDirection = "N";
    
    [Header("Параметры детекции")]
    public float detectionRadius = 10f;
    [Tooltip("Порог угла для определения 'прямо' по целевой дороге")]
    public float straightAngleThreshold = 30f;
    
    /// <summary>
    /// Проверить, есть ли машины на целевой дороге, едущие прямо.
    /// Возвращает true если на целевой дороге есть машины, которые едут прямо (не поворачивают).
    /// </summary>
    public bool IsTargetRoadOccupied()
    {
        Collider[] colliders = Physics.OverlapSphere(transform.position, detectionRadius, LayerMask.GetMask("Traffic"));

        foreach (var col in colliders)
        {
            if (!col.CompareTag("Car")) continue;

            Vector3 otherForward = col.transform.forward;
            string otherDirection = GetCarDirection(otherForward);

            // Проверяем, едет ли машина по целевой дороге
            if (otherDirection == targetDirection)
            {
                WaypointNavigator otherNav = col.GetComponent<WaypointNavigator>();
                if (otherNav == null || !otherNav.IsTurning())
                {
                    // Машина едет прямо по целевой дороге - занята
                    return true;
                }
            }
        }

        return false;
    }

    /// <summary>
    /// Получить количество машин на целевой дороге.
    /// </summary>
    public int GetTargetRoadCarCount()
    {
        int count = 0;
        Collider[] colliders = Physics.OverlapSphere(transform.position, detectionRadius, LayerMask.GetMask("Traffic"));

        foreach (var col in colliders)
        {
            if (!col.CompareTag("Car")) continue;

            Vector3 otherForward = col.transform.forward;
            string otherDirection = GetCarDirection(otherForward);

            if (otherDirection == targetDirection)
            {
                count++;
            }
        }

        return count;
    }

    /// <summary>
    /// Определить направление машины (N/S/E/W).
    /// </summary>
    private string GetCarDirection(Vector3 forward)
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

    void OnDrawGizmos()
    {
#if UNITY_EDITOR
        Gizmos.color = new Color(0f, 1f, 1f, 0.15f);
        Gizmos.DrawSphere(transform.position, detectionRadius);
        
        Gizmos.color = Color.cyan;
        Gizmos.DrawWireSphere(transform.position, detectionRadius);
        
        // Подпись направления
        UnityEditor.Handles.Label(transform.position + Vector3.up * 2f, $"Target: {targetDirection}");
#endif
    }
}