using UnityEditor;
using UnityEngine;

public static class TransformWorldCopier
{
    private static Vector3 savedWorldPosition;
    private static bool hasSavedPosition = false;

    [MenuItem("CONTEXT/Transform/Save World Position", false, 150)]
    static void SaveWorldPos(MenuCommand command)
    {
        Transform t = (Transform)command.context;
        savedWorldPosition = t.position;
        hasSavedPosition = true;
        Debug.Log($"[WorldCopier] Мировая координата сохранена: {savedWorldPosition}");
    }

    [MenuItem("CONTEXT/Transform/Paste World Position", false, 151)]
    static void PasteWorldPos(MenuCommand command)
    {
        if (!hasSavedPosition)
        {
            Debug.LogWarning("[WorldCopier] Сначала сохраните позицию!");
            return;
        }

        Transform t = (Transform)command.context;
        // Записываем действие для отмены (Ctrl+Z)
        Undo.RecordObject(t, "Paste World Position");

        // Присвоение мирового .position само пересчитает локальные координаты внутри префаба
        t.position = savedWorldPosition;
    }
}