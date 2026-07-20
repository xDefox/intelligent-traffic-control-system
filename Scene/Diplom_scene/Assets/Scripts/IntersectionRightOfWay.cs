using UnityEngine;
using System.Collections.Generic;

/// <summary>
/// Реализация правила правой руки (помеха справа) для нерегулируемых перекрёстков.
/// Машина уступает дорогу транспорту, приближающемуся справа.
/// 
/// Принцип работы:
/// 1. Когда машина приближается к перекрёстку (isIntersection = true), она проверяет,
///    нет ли других машин, приближающихся к этому же перекрёстку СПРАВА от неё.
/// 2. Если такая машина есть — уступаем (тормозим до полной остановки).
/// 3. Как только правая сторона свободна — проезжаем.
/// 
/// Защита от дедлока (все 4 стороны забиты):
/// - Если машина уступает дольше maxWaitTime, она активирует режим "срочного проезда":
///   сначала пропускает одну машину справа (в порядке очереди), потом делает рывок.
/// - Если все стороны заблокированы — приоритет у того, кто ждал дольше всех.
/// 
/// УЛУЧШЕНИЯ (v0.8.1):
/// - Машины на перекрёстке (isOnIntersection) имеют приоритет над приближающимися
/// - Машины, едущие прямо, имеют приоритет над поворачивающими
/// - Система очередей: первая машина на оси проезжает первой
/// - Поворотщики проверяют загруженность целевой дороги
/// </summary>
public class IntersectionRightOfWay : MonoBehaviour
{
    [Header("Радиус детекции перекрёстка")]
    [Tooltip("Радиус, в пределах которого ищем другие машины на перекрёстке")]
    public float detectionRadius = 8f;

    [Header("Угол обзора помехи справа")]
    [Tooltip("Диапазон углов (в градусах) справа от машины, где ищем приблюжающиеся машины")]
    [Range(30f, 120f)]
    public float rightSideAngleRange = 60f;

    [Header("Минимальная дистанция для уступления")]
    [Tooltip("Если машина справа дальше этого расстояния, не уступаем")]
    public float minRightCarDistance = 2f;

    [Header("Защита от дедлока")]
    [Tooltip("Максимальное время ожидания перед принудительным проездом (сек)")]
    public float deadlockTimeout = 3.0f;
    [Tooltip("Дистанция, на которую машина 'подкрадывается' при дедлоке за раз")]
    public float creepDistance = 0.5f;
    [Tooltip("Скорость подкрадывания (м/с) в режиме дедлока")]
    public float creepSpeed = 1.5f;
    [Tooltip("Время ожидания между подкрадываниями (сек)")]
    public float creepPauseInterval = 0.8f;

    [Header("Проверка направления движения")]
    [Tooltip("Порог dot-произведения: насколько машина справа должна двигаться в сторону перекрёстка")]
    [Range(0f, 1f)]
    public float approachThreshold = 0.3f;

    [Header("Параметры приоритета")]
    [Tooltip("Время (сек), в течение которого машина считается 'на перекрёстке' после выхода из StopTrigger")]
    public float onIntersectionGracePeriod = 2.0f;
    [Tooltip("Угол (градусов), при котором машина считается поворачивающей")]
    public float turnAngleThreshold = 30f;
    [Tooltip("Расстояние (м) для определения очереди - машины ближе к перекрёстку имеют приоритет")]
    public float queueDistanceThreshold = 3f;

    [Header("Визуализация")]
    public bool showDebugGizmos = true;

    // Словарь для отслеживания времени ожидания каждой машины
    private Dictionary<GameObject, float> carWaitTimers = new Dictionary<GameObject, float>();
    
    // Словарь для отслеживания машин на перекрёстке
    private Dictionary<GameObject, float> carsOnIntersection = new Dictionary<GameObject, float>();

    // Словарь состояния подкрадывания
    private Dictionary<GameObject, int> creepCount = new Dictionary<GameObject, int>();
    
    // Словарь для отслеживания направления машины (N/S/E/W)
    private Dictionary<GameObject, string> carDirections = new Dictionary<GameObject, string>();

    private void Update()
    {
        CleanupDestroyedCars();
        CleanupPassedIntersectionCars();
    }

    /// <summary>
    /// Проверить, должна ли машина уступить.
    /// </summary>
    public bool CheckRightOfWay(Vector3 carPosition, Vector3 carForward, GameObject requestingCar,
                               out bool isDeadlockActive, bool isOnIntersection = false, bool isTurning = false)
    {
        isDeadlockActive = false;

        // Определяем направление машины
        string myDirection = GetCarDirection(carForward);
        
        // Проверяем, есть ли помеха справа
        bool hasRightObstacle = HasCarOnRight(carPosition, carForward, requestingCar, out float rightCarDistance, 
                                            isOnIntersection, isTurning, myDirection);

        if (hasRightObstacle)
        {
            if (!carWaitTimers.ContainsKey(requestingCar))
            {
                carWaitTimers[requestingCar] = Time.time;
            }

            float waitTime = Time.time - carWaitTimers[requestingCar];

            if (waitTime > deadlockTimeout)
            {
                isDeadlockActive = true;
                return false;
            }

            return true;
        }

        if (carWaitTimers.ContainsKey(requestingCar))
        {
            carWaitTimers.Remove(requestingCar);
        }
        if (creepCount.ContainsKey(requestingCar))
        {
            creepCount.Remove(requestingCar);
        }

        return false;
    }

    /// <summary>
    /// Получить скорость для режима дедлока.
    /// </summary>
    public float GetDeadlockSpeed(GameObject car)
    {
        if (!creepCount.ContainsKey(car))
        {
            creepCount[car] = 0;
        }

        creepCount[car]++;
        return creepSpeed;
    }

    /// <summary>
    /// Проверить, нужно ли сделать паузу между подкрадываниями.
    /// </summary>
    public bool ShouldPauseBetweenCreeps(GameObject car)
    {
        if (!creepCount.ContainsKey(car)) return false;

        int count = creepCount[car];
        return count > 0 && count % 2 == 0;
    }

    /// <summary>
    /// Пометить машину как находящуюся на перекрёстке.
    /// </summary>
    public void MarkCarOnIntersection(GameObject car)
    {
        carsOnIntersection[car] = Time.time;
    }

    /// <summary>
    /// Проверить, находится ли машина на перекрёстке.
    /// </summary>
    public bool IsCarOnIntersection(GameObject car)
    {
        return carsOnIntersection.ContainsKey(car);
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

    /// <summary>
    /// Проверить, есть ли машина на перекрёстке, которой нужно уступить.
    /// УЛУЧШЕНО: учитывает приоритет машин на перекрёстке, прямого движения и очередь.
    /// </summary>
    private bool HasCarOnRight(Vector3 myPosition, Vector3 myForward, GameObject myCar, out float closestDistance,
                              bool isOnIntersection = false, bool isTurning = false, string myDirection = "")
    {
        closestDistance = float.MaxValue;
        bool foundCar = false;

        Collider[] colliders = Physics.OverlapSphere(transform.position, detectionRadius, LayerMask.GetMask("Traffic"));

        // Сначала находим всех участников на перекрёстке и строим очередь
        List<(GameObject car, float distance, string direction, bool isTurning, bool isOnIntersection)> allCars = 
            new List<(GameObject, float, string, bool, bool)>();

        foreach (var col in colliders)
        {
            if (col.gameObject == myCar) continue;
            if (!col.CompareTag("Car")) continue;

            Vector3 otherPosition = col.transform.position;
            Vector3 otherForward = col.transform.forward;
            string otherDirection = GetCarDirection(otherForward);
            
            float distanceToIntersection = Vector3.Distance(otherPosition, transform.position);
            bool otherIsOnIntersection = IsCarOnIntersection(col.gameObject);
            
            WaypointNavigator otherNav = col.GetComponent<WaypointNavigator>();
            bool otherIsTurning = otherNav != null && otherNav.IsTurning();

            allCars.Add((col.gameObject, distanceToIntersection, otherDirection, otherIsTurning, otherIsOnIntersection));
        }

        // Сортируем по расстоянию к перекрёстку (ближайшие первые)
        allCars.Sort((a, b) => a.distance.CompareTo(b.distance));

        // Проверяем каждую машину
        foreach (var (otherCar, distance, otherDirection, otherIsTurning, otherIsOnIntersection) in allCars)
        {
            Vector3 otherPosition = otherCar.transform.position;
            Vector3 directionToOther = (otherPosition - myPosition).normalized;

            float signedAngle = Vector3.SignedAngle(myForward, directionToOther, Vector3.up);

            // ПРАВИЛО ПДД: проверяем машины слева и справа от нас (углы 30-150 и -30--150)
            // На перекрёстке уступаем ВСЕМ, кто едет поперёк
            bool isOnLeftOrRight = Mathf.Abs(signedAngle) > 30f && Mathf.Abs(signedAngle) < 150f;

            if (isOnLeftOrRight)
            {
                // ЛОГИКА ПРИОРИТЕТА:
                
                // 1. Машины на перекрёстке имеют ПРИОРИТЕТ
                if (otherIsOnIntersection)
                {
                    closestDistance = Mathf.Min(closestDistance, distance);
                    foundCar = true;
                    continue;
                }
                
                // 2. Если мы поворачиваем, а справа/встречная едет прямо - уступаем
                if (isTurning && !otherIsTurning)
                {
                    closestDistance = Mathf.Min(closestDistance, distance);
                    foundCar = true;
                    continue;
                }
                
                // 3. Проверка очереди: если машина на той же оси и ближе к перекрёстку - уступаем
                if (myDirection != "" && otherDirection == myDirection)
                {
                    // Та же ось - проверяем очередь
                    float myDistanceToIntersection = Vector3.Distance(myPosition, transform.position);
                    if (distance < myDistanceToIntersection)
                    {
                        // Машина на той же оси и ближе - уступаем
                        closestDistance = Mathf.Min(closestDistance, distance);
                        foundCar = true;
                        continue;
                    }
                }
                
                // 4. Стандартная логика: машина справа/встречная приближается
                // Проверяем, движется ли другая машина в сторону перекрёстка
                Vector3 toIntersection = (transform.position - otherPosition).normalized;
                float approachDot = Vector3.Dot(otherCar.transform.forward, toIntersection);
                
                if (approachDot > approachThreshold)
                {
                    float myDistanceToIntersection = Vector3.Distance(myPosition, transform.position);
                    
                    // Если другая машина ближе к перекрёстку - уступаем
                    if (distance < myDistanceToIntersection)
                    {
                        closestDistance = Mathf.Min(closestDistance, distance);
                        foundCar = true;
                    }
                }
            }
        }

        return foundCar;
    }

    /// <summary>
    /// Проверить, занята ли целевая дорога для поворачивающей машины.
    /// Возвращает true если на целевой дороге есть машины, которые едут прямо.
    /// </summary>
    /// <param name="turnDirection">Направление целевой дороги: "N", "S", "E", или "W"</param>
    public bool IsTargetRoadOccupied(string turnDirection)
    {
        if (string.IsNullOrEmpty(turnDirection)) return false;

        Collider[] colliders = Physics.OverlapSphere(transform.position, detectionRadius, LayerMask.GetMask("Traffic"));

        foreach (var col in colliders)
        {
            if (!col.CompareTag("Car")) continue;

            Vector3 otherForward = col.transform.forward;
            string otherDirection = GetCarDirection(otherForward);

            // Проверяем, едет ли машина по целевой дороге
            if (otherDirection == turnDirection)
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
    public int GetTargetRoadCarCount(string direction)
    {
        if (string.IsNullOrEmpty(direction)) return 0;

        int count = 0;
        Collider[] colliders = Physics.OverlapSphere(transform.position, detectionRadius, LayerMask.GetMask("Traffic"));

        foreach (var col in colliders)
        {
            if (!col.CompareTag("Car")) continue;

            Vector3 otherForward = col.transform.forward;
            string otherDirection = GetCarDirection(otherForward);

            if (otherDirection == direction)
            {
                count++;
            }
        }

        return count;
    }

    /// <summary>
    /// Очистка словарей от уничтоженных машин.
    /// </summary>
    private void CleanupDestroyedCars()
    {
        List<GameObject> toRemove = new List<GameObject>();

        foreach (var kvp in carWaitTimers)
        {
            if (kvp.Key == null)
            {
                toRemove.Add(kvp.Key);
            }
        }

        foreach (var kvp in carsOnIntersection)
        {
            if (kvp.Key == null)
            {
                toRemove.Add(kvp.Key);
            }
        }

        foreach (var car in toRemove)
        {
            carWaitTimers.Remove(car);
            carsOnIntersection.Remove(car);
            creepCount.Remove(car);
            carDirections.Remove(car);
        }
    }

    /// <summary>
    /// Очистка машин, которые успели пройти перекрёсток.
    /// </summary>
    private void CleanupPassedIntersectionCars()
    {
        List<GameObject> toRemove = new List<GameObject>();
        
        foreach (var kvp in carsOnIntersection)
        {
            if (kvp.Key == null)
            {
                toRemove.Add(kvp.Key);
            }
            else
            {
                float timeOnIntersection = Time.time - kvp.Value;
                if (timeOnIntersection > onIntersectionGracePeriod)
                {
                    toRemove.Add(kvp.Key);
                }
            }
        }
        
        foreach (var car in toRemove)
        {
            carsOnIntersection.Remove(car);
        }
    }

    /// <summary>
    /// Получить число машин, приближающихся к перекрёстку.
    /// </summary>
    public int GetApproachingCarCount()
    {
        int count = 0;
        Collider[] colliders = Physics.OverlapSphere(transform.position, detectionRadius, LayerMask.GetMask("Traffic"));

        foreach (var col in colliders)
        {
            if (!col.CompareTag("Car")) continue;

            Vector3 colForward = col.transform.forward;
            Vector3 toIntersection = (transform.position - col.transform.position).normalized;

            if (Vector3.Dot(colForward, toIntersection) > approachThreshold)
            {
                count++;
            }
        }

        return count;
    }

    private void OnDrawGizmos()
    {
#if UNITY_EDITOR
        if (!showDebugGizmos || !TrafficGenerator.ShowDebugGizmos) return;

        Gizmos.color = new Color(1f, 0f, 1f, 0.08f);
        Gizmos.DrawSphere(transform.position, detectionRadius);

        Gizmos.color = Color.magenta;
        Gizmos.DrawWireSphere(transform.position, detectionRadius);

        Collider[] colliders = Physics.OverlapSphere(transform.position, detectionRadius, LayerMask.GetMask("Traffic"));

        foreach (var col in colliders)
        {
            if (!col.CompareTag("Car")) continue;
            DrawRightSector(col.transform.position, col.transform.forward);
        }

        foreach (var col in colliders)
        {
            if (!col.CompareTag("Car")) continue;

            Vector3 carPos = col.transform.position;
            Vector3 carForward = col.transform.forward;

            bool isDeadlockActive;
            bool shouldYield = CheckRightOfWay(carPos, carForward, col.gameObject, out isDeadlockActive);

            if (isDeadlockActive)
            {
                Gizmos.color = Color.yellow;
            }
            else if (shouldYield)
            {
                Gizmos.color = Color.red;
            }
            else
            {
                Gizmos.color = Color.green;
            }

            Gizmos.DrawLine(carPos, transform.position);

            Vector3 labelPos = carPos + Vector3.up * 2f;
            Gizmos.DrawIcon(labelPos, 
                isDeadlockActive ? "console.warnicon" : 
                shouldYield ? "console.erroricon" : "console.infoicon", true);
        }
#endif
    }

    private void DrawRightSector(Vector3 carPos, Vector3 carForward)
    {
        float halfRange = rightSideAngleRange * 0.5f;
        float sectorRadius = detectionRadius * 0.35f;

        Vector3 rightStart = Quaternion.Euler(0, -90f - halfRange, 0) * carForward;
        Vector3 rightEnd = Quaternion.Euler(0, -90f + halfRange, 0) * carForward;

        Gizmos.color = new Color(1f, 0.5f, 0f, 0.5f);
        Gizmos.DrawLine(carPos, carPos + rightStart * sectorRadius);
        Gizmos.DrawLine(carPos, carPos + rightEnd * sectorRadius);

        int segments = 8;
        Vector3 prevPoint = carPos + rightStart * sectorRadius;
        for (int i = 1; i <= segments; i++)
        {
            float t = (float)i / segments;
            float angle = Mathf.Lerp(-90f - halfRange, -90f + halfRange, t);
            Vector3 dir = Quaternion.Euler(0, angle, 0) * carForward;
            Vector3 point = carPos + dir * sectorRadius;
            Gizmos.DrawLine(prevPoint, point);
            prevPoint = point;
        }

        Gizmos.color = new Color(1f, 0.5f, 0f, 0.1f);
        Vector3 center = carPos;
        prevPoint = carPos + rightStart * sectorRadius;
        for (int i = 1; i <= segments; i++)
        {
            float t = (float)i / segments;
            float angle = Mathf.Lerp(-90f - halfRange, -90f + halfRange, t);
            Vector3 dir = Quaternion.Euler(0, angle, 0) * carForward;
            Vector3 point = carPos + dir * sectorRadius;
            Gizmos.DrawLine(center, prevPoint);
            Gizmos.DrawLine(center, point);
            prevPoint = point;
        }
    }
}