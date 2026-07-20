using UnityEngine;
using System;

/// <summary>
/// Централизованный логгер для Unity-скриптов.
/// Управляется через PlayerPrefs: "LOG_LEVEL" (DEBUG, INFO, WARNING, ERROR, OFF)
/// По умолчанию INFO.
/// </summary>
public static class Logger
{
    public enum LogLevel
    {
        DEBUG = 10,
        INFO = 20,
        WARNING = 30,
        ERROR = 40,
        OFF = 50
    }

    private static LogLevel _currentLevel = LogLevel.INFO;
    private static bool _initialized = false;

    private static void Initialize()
    {
        if (_initialized) return;
        
        string levelStr = PlayerPrefs.GetString("LOG_LEVEL", "INFO").ToUpper();
        if (Enum.TryParse<LogLevel>(levelStr, out LogLevel level))
        {
            _currentLevel = level;
        }
        _initialized = true;
    }

    public static void SetLogLevel(LogLevel level)
    {
        _currentLevel = level;
        PlayerPrefs.SetString("LOG_LEVEL", level.ToString());
    }

    public static void SetLogLevel(string level)
    {
        if (Enum.TryParse<LogLevel>(level.ToUpper(), out LogLevel logLevel))
        {
            SetLogLevel(logLevel);
        }
    }

    public static void LogDebug(string component, string message)
    {
        Initialize();
        if (_currentLevel <= LogLevel.DEBUG)
        {
            UnityEngine.Debug.Log($"[DEBUG] [{component}] {message}");
        }
    }

    public static void LogInfo(string component, string message)
    {
        Initialize();
        if (_currentLevel <= LogLevel.INFO)
        {
            UnityEngine.Debug.Log($"[INFO] [{component}] {message}");
        }
    }

    public static void LogWarning(string component, string message)
    {
        Initialize();
        if (_currentLevel <= LogLevel.WARNING)
        {
            UnityEngine.Debug.LogWarning($"[WARN] [{component}] {message}");
        }
    }

    public static void LogError(string component, string message)
    {
        Initialize();
        if (_currentLevel <= LogLevel.ERROR)
        {
            UnityEngine.Debug.LogError($"[ERROR] [{component}] {message}");
        }
    }
}
