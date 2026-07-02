using UnityEngine;

public class CarDistanceSensor : MonoBehaviour
{
    private WaypointNavigator parentNavigator;

    void Start()
    {
        parentNavigator = GetComponentInParent<WaypointNavigator>();
    }

    void OnTriggerStay(Collider other)
    {
        // Если в наш вытянутый вперед триггер-бампер попал чужой твердый коллайдер машины
        if (other.CompareTag("Car") && other.gameObject != transform.parent.gameObject)
        {
            if (parentNavigator != null)
            {
                parentNavigator.SetCarInFrontTrigger(true);
            }
        }
    }

    void OnTriggerExit(Collider other)
    {
        if (other.CompareTag("Car"))
        {
            if (parentNavigator != null)
            {
                parentNavigator.SetCarInFrontTrigger(false);
            }
        }
    }
}