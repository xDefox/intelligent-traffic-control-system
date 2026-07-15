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
/// </summary>
public class IntersectionRightOfWay : MonoBehaviour
{
    [Header("Радиус детекции перекрёстка")]
    [Tooltip("Радиус, в пределах которого ищем другие машины на перекрёстке")]
    public float detectionRadius = 8f;

    [Header("Угол обзора помехи справа")]
    [Tooltip("Диапазон углов (в градусах) справа от машины, где ищем приближающиеся машины")]
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

    [Header("Визуализация")]
    public bool showDebugGizmos = true;

    // Словарь для отслеживания времени ожидания каждой машины (GameObject -> время начала ожидания)
    private Dictionary<GameObject, float> carWaitTimers = new Dictionary<GameObject, float>();

    // Словарь состояния подкрадывания: сколько раз машина уже подкралась
    private Dictionary<GameObject, int> creepCount = new Dictionary<GameObject, int>();

    private void Update()
    {
        // Очищаем словари от уничтоженных машин
        CleanupDestroyedCars();
    }

    /// <summary>
    /// Проверить, должна ли машина уступить (есть ли помеха справа или дедлок).
    /// </summary>
    /// <param name="carPosition">Позиция проверяющей машины</param>
    /// <param name="carForward">Направление вперёд проверяющей машины</param>
    /// <param name="requestingCar">GameObject проверяющей машины (чтобы игнорировать саму себя)</param>
    /// <param name="targetSpeed">Скорость, с которой машина хочет ехать (будет изменена при необходимости)</param>
    /// <param name="isStopped">Была ли машина уже остановлена (чтобы отличать торможение от стоянки)</param>
    /// <returns>true, если нужно полностью остановиться, false — если можно ехать</returns>
    public bool CheckRightOfWay(Vector3 carPosition, Vector3 carForward, GameObject requestingCar,
                                out bool isDeadlockActive)
    {
        isDeadlockActive = false;

        // Проверяем, есть ли помеха справа
        bool hasRightObstacle = HasCarOnRight(carPosition, carForward, requestingCar, out float rightCarDistance);

        if (hasRightObstacle)
        {
            // Запоминаем время начала ожидания
            if (!carWaitTimers.ContainsKey(requestingCar))
            {
                carWaitTimers[requestingCar] = Time.time;
            }

            float waitTime = Time.time - carWaitTimers[requestingCar];

            // Если ждём дольше deadlockTimeout — активируем режим дедлока
            if (waitTime > deadlockTimeout)
            {
                isDeadlockActive = true;
                return false; // Не стоим на месте, а начинаем подкрадываться
            }

            return true; // Уступаем (стоим)
        }

        // Если помехи справа нет — сбрасываем таймер ожидания
        if (carWaitTimers.ContainsKey(requestingCar))
        {
            carWaitTimers.Remove(requestingCar);
        }
        if (creepCount.ContainsKey(requestingCar))
        {
            creepCount.Remove(requestingCar);
        }

        return false; // Можно ехать
    }

    /// <summary>
    /// Получить скорость для режима дедлока (подкрадывание).
    /// </summary>
    public float GetDeadlockSpeed(GameObject car)
    {
        // При дедлоке едем медленно, подкрадываясь
        if (!creepCount.ContainsKey(car))
        {
            creepCount[car] = 0;
        }

        creepCount[car]++;
        return creepSpeed;
    }

    /// <summary>
    /// Проверить, нужно ли сделать паузу между подкрадываниями (чтобы дать шанс другим).
    /// </summary>
    public bool ShouldPauseBetweenCreeps(GameObject car)
    {
        if (!creepCount.ContainsKey(car)) return false;

        int count = creepCount[car];
        // После каждого подкрадывания делаем паузу
        return count > 0 && count % 2 == 0;
    }

    /// <summary>
    /// Проверить, есть ли машина справа, которой нужно уступить.
    /// </summary>
    private bool HasCarOnRight(Vector3 myPosition, Vector3 myForward, GameObject myCar, out float closestDistance)
    {
        closestDistance = float.MaxValue;
        bool foundCar = false;

        Collider[] colliders = Physics.OverlapSphere(transform.position, detectionRadius, LayerMask.GetMask("Traffic"));

        foreach (var col in colliders)
        {
            if (col.gameObject == myCar) continue;
            if (!col.CompareTag("Car")) continue;

            Transform otherCar = col.transform;
            Vector3 otherPosition = otherCar.position;
            Vector3 directionToOther = (otherPosition - myPosition).normalized;

            // Вычисляем угол между направлением вперёд нашей машины и направлением на другую машину
            float signedAngle = Vector3.SignedAngle(myForward, directionToOther, Vector3.up);

            // "Справа" = угол от -90 - половина диапазона до -90 + половина диапазона
            float rightCenter = -90f;
            float angleMin = rightCenter - rightSideAngleRange * 0.5f;
            float angleMax = rightCenter + rightSideAngleRange * 0.5f;

            // Проверяем, находится ли другая машина справа от нас
            if (signedAngle >= angleMin && signedAngle <= angleMax)
            {
                float distanceToOther = Vector3.Distance(myPosition, otherPosition);
                if (distanceToOther < minRightCarDistance)
                {
                    closestDistance = Mathf.Min(closestDistance, distanceToOther);
                    foundCar = true;
                    continue;
                }

                // Проверяем, движется ли другая машина в сторону перекрёстка
                Vector3 otherForward = otherCar.forward;
                Vector3 toIntersection = (transform.position - otherPosition).normalized;

                float approachDot = Vector3.Dot(otherForward, toIntersection);
                if (approachDot > approachThreshold)
                {
                    // Проверяем, что другая машина ещё не проехала перекрёсток
                    float otherDistanceToIntersection = Vector3.Distance(otherPosition, transform.position);
                    float myDistanceToIntersection = Vector3.Distance(myPosition, transform.position);

                    // Если другая машина уже ближе к перекрёстку — уступаем
                    if (otherDistanceToIntersection < myDistanceToIntersection * 1.2f)
                    {
                        closestDistance = Mathf.Min(closestDistance, distanceToOther);
                        foundCar = true;
                    }
                }
            }
        }

        return foundCar;
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

        foreach (var car in toRemove)
        {
            carWaitTimers.Remove(car);
            creepCount.Remove(car);
        }
    }

    /// <summary>
    /// Получить число машин, приближающихся к перекрёстку (для отладки).
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

        // Рисуем зону детекции перекрёстка
        Gizmos.color = new Color(1f, 0f, 1f, 0.08f);
        Gizmos.DrawSphere(transform.position, detectionRadius);

        Gizmos.color = Color.magenta;
        Gizmos.DrawWireSphere(transform.position, detectionRadius);

        // Визуализируем состояние каждой машины на перекрёстке
        Collider[] colliders = Physics.OverlapSphere(transform.position, detectionRadius, LayerMask.GetMask("Traffic"));

        // Сначала рисуем секторы справа для всех машин
        foreach (var col in colliders)
        {
            if (!col.CompareTag("Car")) continue;
            DrawRightSector(col.transform.position, col.transform.forward);
        }

        // Потом рисуем линии от машин к перекрёстку с цветом состояния
        foreach (var col in colliders)
        {
            if (!col.CompareTag("Car")) continue;

            Vector3 carPos = col.transform.position;
            Vector3 carForward = col.transform.forward;

            bool isDeadlockActive;
            bool shouldYield = CheckRightOfWay(carPos, carForward, col.gameObject, out isDeadlockActive);

            if (isDeadlockActive)
            {
                Gizmos.color = Color.yellow; // Дедлок — подкрадывается
            }
            else if (shouldYield)
            {
                Gizmos.color = Color.red; // Уступает
            }
            else
            {
                Gizmos.color = Color.green; // Едет
            }

            Gizmos.DrawLine(carPos, transform.position);

            // Подпись состояния
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

        // Рисуем контур сектора
        Gizmos.color = new Color(1f, 0.5f, 0f, 0.5f);
        Gizmos.DrawLine(carPos, carPos + rightStart * sectorRadius);
        Gizmos.DrawLine(carPos, carPos + rightEnd * sectorRadius);

        // Рисуем дугу сектора
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

        // Заливка сектора
        Gizmos.color = new Color(1f, 0.5f, 0f, 0.1f);
        Vector3 center = carPos;
        prevPoint = carPos + rightStart * sectorRadius;
        for (int i = 1; i <= segments; i++)
        {
            float t = (float)i / segments;
            float angle = Mathf.Lerp(-90f - halfRange, -90f + halfRange, t);
            Vector3 dir = Quaternion.Euler(0, angle, 0) * carForward;
            Vector3 point = carPos + dir * sectorRadius;
            // Рисуем треугольники заливки
            Gizmos.DrawLine(center, prevPoint);
            Gizmos.DrawLine(center, point);
            prevPoint = point;
        }
    }
}