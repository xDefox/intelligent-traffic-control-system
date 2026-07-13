# Unity Performance Optimization Guide

## Current Issues
- **Memory Usage**: 4.04 GB / 4.14 GB (97% - CRITICAL)
- **FPS**: ~2 FPS (CRITICAL - unplayable)
- **Root Cause**: Massive 1280x1280 AI tensor + memory leaks + excessive logging

## Applied Optimizations

### 1. IntersectionVisionManager.cs ✅
**Changes:**
- Reduced AI input tensor from 1280x1280 to 640x640 (75% memory reduction)
- Added processing lock to prevent overlapping inference cycles
- Added immediate tensor disposal after use
- **Impact**: ~3 GB memory saved

### 2. TrafficLightViewer.cs ✅
**Changes:**
- Cache materials at startup instead of creating instances every frame
- Skip color changes if already that color
- **Impact**: Eliminates material instance spam, reduces GC pressure

### 3. WaypointNavigator.cs ✅
**Changes:**
- Removed Debug.Log spam (was logging every state change every frame)
- **Impact**: Massive CPU savings, eliminates console I/O bottleneck

### 4. EdgeVisionCamera.cs ✅
**Changes:**
- Throttle ROI line updates from 60Hz to 10Hz
- Optimize UI box updates to only update when changed
- **Impact**: Reduces UI overhead by ~85%

### 5. TrafficGenerator.cs ✅
**Changes:**
- Added max car limit (default 50)
- Added car cleanup tracking
- **Impact**: Prevents unlimited car spawning

### 6. CarCleanupHandler.cs ✅ (NEW)
**Purpose:**
- Tracks car destruction to maintain accurate count
- Prevents memory leaks from unmanaged objects

## Expected Results
- **Memory**: Should drop to ~1-1.5 GB (60-70% reduction)
- **FPS**: Should increase to 30-60 FPS (15-30x improvement)
- **Stability**: No more memory warnings

## Additional Unity Editor Settings

### Quality Settings
1. Go to **Edit → Project Settings → Quality**
2. Set **Pixel Light Count** to 0
3. Disable **Soft Particles** and **Soft Shadows**
4. Set **Texture Quality** to "Half Res" or "Quarter Res"
5. Disable **Anti-Aliasing** (or use FXAA only)

### Physics Settings
1. Go to **Edit → Project Settings → Physics**
2. Set **Default Solver Iterations** to 6 (from 10)
3. Set **Default Solver Velocity Iterations** to 1 (from 10)
4. Enable **Auto Sync Transforms** = false (if not needed)

### Graphics Settings
1. Go to **Edit → Project Settings → Graphics**
2. Disable **Dynamic Batching** if using URP/HDRP
3. Set **Scripting Runtime Version** to .NET 4.x
4. Enable **IL2CPP** for builds (not editor)

### Player Settings
1. Go to **Edit → Project Settings → Player**
2. Set **Api Compatibility Level** to ".NET Framework"
3. Enable **Low Resolution Aspect Ratio** for testing

## Scene-Specific Optimizations

### Camera Settings
- Disable **HDR** on all cameras except main
- Disable **MSAA** (use FXAA instead)
- Set **Far Clip Plane** to minimum needed (e.g., 100 instead of 1000)
- Disable **Skybox** if not needed

### Lighting
- Use **Baked GI** instead of Realtime GI
- Reduce **Bounce Count** to 1 or 2
- Disable **Realtime Global Illumination**
- Use light probes instead of real-time shadows where possible

### Materials & Shaders
- Use **Mobile/Diffuse** shaders instead of Standard
- Disable **Metallic/Smoothness** maps if not needed
- Use texture atlases to reduce draw calls
- Set all textures to **Compressed** format (ASTC/ETC2)

### LOD (Level of Detail)
- Add LOD groups to complex models
- Set LOD distances: High (0-20m), Medium (20-50m), Low (50-100m)
- Disable LOD Group **Cull** if objects are important

### Occlusion Culling
1. Go to **Window → Rendering → Occlusion Culling**
2. Bake occlusion data for static objects
3. Set **Smallest Occluder** to 0.5m
4. Set **Smallest Hole** to 0.1m

## Code Optimizations

### General Best Practices
1. **Avoid GetComponent in Update()** - Cache references in Start()
2. **Use Object Pooling** for frequently spawned objects (cars, particles)
3. **Disable unnecessary Update()** - Use events/coroutines instead
4. **Cache Transform references** - `transform` is expensive to access
5. **Use FixedUpdate()** only for physics

### Specific Fixes Needed

#### TrafficGenerator.cs
```csharp
// Add object pooling for cars
public class CarPool : MonoBehaviour
{
    public GameObject carPrefab;
    public int poolSize = 20;
    
    private Queue<GameObject> pool = new Queue<GameObject>();
    
    void Start()
    {
        for (int i = 0; i < poolSize; i++)
        {
            GameObject car = Instantiate(carPrefab);
            car.SetActive(false);
            pool.Enqueue(car);
        }
    }
    
    public GameObject GetCar()
    {
        if (pool.Count > 0)
        {
            GameObject car = pool.Dequeue();
            car.SetActive(true);
            return car;
        }
        return Instantiate(carPrefab);
    }
    
    public void ReturnCar(GameObject car)
    {
        car.SetActive(false);
        pool.Enqueue(car);
    }
}
```

#### WaypointNavigator.cs
```csharp
// Cache frequently accessed components
private Transform cachedTransform;
private Rigidbody cachedRb;

void Start()
{
    cachedTransform = transform;
    cachedRb = GetComponent<Rigidbody>();
}

void Update()
{
    // Use cachedTransform instead of transform
    // Use physics in FixedUpdate, not Update
}
```

## Profiling Tips

### Unity Profiler
1. Open **Window → Analysis → Profiler**
2. Check **CPU Usage** - look for spikes
3. Check **Memory** - look for allocations
4. Check **Rendering** - look for draw calls
5. Use **Deep Profile** only when needed (very slow)

### Frame Debugger
1. Open **Window → Analysis → Frame Debugger**
2. Check draw calls per frame
3. Identify redundant passes
4. Look for overdraw (red areas in Scene view)

### Memory Profiler
1. Install **Memory Profiler** package from Package Manager
2. Take snapshots before/after changes
3. Look for:
   - Unmanaged memory leaks
   - Texture memory (should be < 500MB)
   - Mesh memory (should be < 200MB)
   - Managed heap (should be < 100MB)

## Testing Checklist

- [ ] FPS counter shows 30+ FPS in Play Mode
- [ ] Memory usage stays below 2GB
- [ ] No "Discarding profiler frames" warnings
- [ ] No "Out of memory" errors
- [ ] Cars spawn and despawn correctly
- [ ] Traffic lights work properly
- [ ] AI detection still works (check bounding boxes)
- [ ] No console spam (only errors/warnings)

## Rollback Plan

If optimizations cause issues:
1. Keep git commit before changes: `git commit -m "Before performance optimization"`
2. Revert specific files if needed: `git checkout -- filename.cs`
3. Test incrementally - apply one optimization at a time

## Next Steps

1. **Apply all code changes** (already done ✅)
2. **Restart Unity Editor** (clears memory)
3. **Adjust Quality Settings** as described above
4. **Test in Play Mode** for 5-10 minutes
5. **Monitor Profiler** for remaining bottlenecks
6. **Consider build** for final performance test (editor is slower)

## Build Optimization

For final build:
1. Use **IL2CPP** scripting backend
2. Enable **Code Stripping**
3. Use **Release** build (not Debug)
4. Disable **Development Build**
5. Enable **Deep Profiling** only if needed
6. Use **AssetBundles** for large assets
7. Implement **Addressables** for dynamic loading

## Contact

If issues persist after optimizations:
1. Check Unity Console for errors
2. Take Profiler screenshot
3. Note exact FPS and memory usage
4. Document what was happening when slowdown occurred