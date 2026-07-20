"""
Централизованный логгер для системы управления трафиком.

Настраивается через переменную окружения LOG_LEVEL:
- DEBUG: все сообщения (много шума, для разработки)
- INFO: основные события (рекомендуется для продакшена)
- WARNING: только предупреждения и ошибки
- ERROR: только ошибки
- OFF: логирование отключено
"""

import os
import sys
from typing import Optional


class LogLevel:
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    OFF = 50


# Сопоставление строковых уровней к числовым
LEVEL_MAP = {
    "DEBUG": LogLevel.DEBUG,
    "INFO": LogLevel.INFO,
    "WARNING": LogLevel.WARNING,
    "ERROR": LogLevel.ERROR,
    "OFF": LogLevel.OFF,
}


def _get_log_level() -> int:
    """Получить уровень логирования из переменной окружения"""
    level_str = os.environ.get("LOG_LEVEL", "INFO").upper()
    return LEVEL_MAP.get(level_str, LogLevel.INFO)


# Глобальный уровень (можно переопределить в runtime)
_current_level = _get_log_level()


def set_log_level(level: str) -> None:
    """Изменить уровень логирования во время выполнения"""
    global _current_level
    _current_level = LEVEL_MAP.get(level.upper(), LogLevel.INFO)


def get_log_level() -> int:
    """Получить текущий уровень логирования"""
    return _current_level


def _format_message(level_name: str, component: str, message: str) -> str:
    """Форматирование сообщения лога"""
    return f"[{level_name}] [{component}] {message}"


def debug(component: str, message: str) -> None:
    """DEBUG уровень - для разработки, очень много сообщений"""
    if _current_level <= LogLevel.DEBUG:
        print(_format_message("DEBUG", component, message), file=sys.stderr)


def info(component: str, message: str) -> None:
    """INFO уровень - основные события системы"""
    if _current_level <= LogLevel.INFO:
        print(_format_message("INFO", component, message), file=sys.stderr)


def warning(component: str, message: str) -> None:
    """WARNING уровень - предупреждения"""
    if _current_level <= LogLevel.WARNING:
        print(_format_message("WARN", component, message), file=sys.stderr)


def error(component: str, message: str) -> None:
    """ERROR уровень - ошибки"""
    if _current_level <= LogLevel.ERROR:
        print(_format_message("ERROR", component, message), file=sys.stderr)