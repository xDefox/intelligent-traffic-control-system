using UnityEngine;
using System.Collections.Generic;

public class OncomingTrafficDetector : MonoBehaviour
{
    private List<GameObject> carsInZone = new List<GameObject>();

    // Свойство: свободна ли встречная полоса?
    public bool IsClear => carsInZone.Count == 0;

    void OnTriggerEnter(Collider other)
    {
        if (other.CompareTag("Car") && !carsInZone.Contains(other.gameObject))
        {
            carsInZone.Add(other.gameObject);
        }
    }

    void OnTriggerExit(Collider other)
    {
        if (carsInZone.Contains(other.gameObject))
        {
            carsInZone.Remove(other.gameObject);
        }
    }

    void Update()
    {
        // Очистка на случай удаления машин
        carsInZone.RemoveAll(item => item == null);
    }
}