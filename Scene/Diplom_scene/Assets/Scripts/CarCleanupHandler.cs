using UnityEngine;

public class CarCleanupHandler : MonoBehaviour
{
    private TrafficGenerator generator;
    
    public void Initialize(TrafficGenerator gen)
    {
        generator = gen;
    }
    
    void OnDestroy()
    {
        if (generator != null)
        {
            generator.OnCarDestroyed();
        }
    }
}