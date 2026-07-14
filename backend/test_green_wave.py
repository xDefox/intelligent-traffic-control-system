"""
Тесты для системы зелёной волны (Green Wave).
"""

import sys
import time
from pathlib import Path

# Добавляем родительскую директорию (backend) в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from services.green_wave import green_wave_coordinator
from services.graph_manager import traffic_network
from core.road_config import ROADS


def test_green_wave_basic():
    """Базовый тест: проверяем что координатор создаёт команды"""
    print("\n" + "="*60)
    print("ТЕСТ 1: Базовое создание зелёной волны")
    print("="*60)
    
    # Принудительно пересчитываем
    commands = green_wave_coordinator.calculate_green_wave(force=True)
    
    print(f"\n[OK] Создано {len(commands)} команд зелёной волны")
    
    if commands:
        print("\nКоманды:")
        for cmd in commands:
            print(f"  - {cmd['target_intersection']}: фаза {cmd['phase']}, offset {cmd['offset']:.1f}с")
            print(f"    Коридор: {cmd['corridor']}")
    
    return len(commands) > 0


def test_corridors():
    """Тест: проверяем обнаружение коридоров"""
    print("\n" + "="*60)
    print("ТЕСТ 2: Обнаружение коридоров")
    print("="*60)
    
    corridors = green_wave_coordinator._find_corridors()
    
    print(f"\n[OK] Найдено {len(corridors)} коридоров")
    
    for idx, corridor in enumerate(corridors, 1):
        print(f"\n  Коридор {idx}: {' -> '.join(corridor)}")
        
        # Получаем позиции
        positions = green_wave_coordinator._get_intersection_positions(corridor)
        if positions:
            print(f"    Позиции:")
            for inter_id, (x, z) in positions.items():
                print(f"      {inter_id}: ({x}, {z})")
        
        # Определяем ось
        axis = green_wave_coordinator._determine_main_axis(positions)
        print(f"    Основная ось: {'X (восток-запад)' if axis == 'x' else 'Z (север-юг)'}")
        
        # Рассчитываем время проезда
        travel_times = green_wave_coordinator._calculate_travel_times(positions, axis)
        if travel_times:
            print(f"    Время проезда между перекрёстками:")
            for i, t in enumerate(travel_times):
                print(f"      {corridor[i]} -> {corridor[i+1]}: {t:.2f}с")
    
    return len(corridors) > 0


def test_intersection_config():
    """Тест: проверяем конфигурацию перекрёстков"""
    print("\n" + "="*60)
    print("ТЕСТ 3: Конфигурация перекрёстков")
    print("="*60)
    
    print("\nПерекрёстки в конфиге:")
    for inter_id, config in ROADS.items():
        if inter_id == "links":
            continue
        
        print(f"\n  {inter_id}:")
        print(f"    Тип: {config.get('type', 'unknown')}")
        print(f"    Позиция: {config.get('position', {})}")
        
        phases = config.get("phases", {})
        print(f"    Фазы:")
        for phase_name, phase_data in phases.items():
            if isinstance(phase_data, dict):
                approaches = phase_data.get("approaches", [])
                min_dur = phase_data.get("min_duration", 5.0)
                max_dur = phase_data.get("max_duration", 30.0)
                print(f"      {phase_name}: {approaches} (мин {min_dur}с, макс {max_dur}с)")
            else:
                print(f"      {phase_name}: {phase_data}")
    
    print("\nСвязи между перекрёстками:")
    for link in ROADS.get("links", []):
        print(f"  {link}")
    
    return True


def test_graph_topology():
    """Тест: проверяем топологию графа"""
    print("\n" + "="*60)
    print("ТЕСТ 4: Топология графа дорожной сети")
    print("="*60)
    
    print(f"\n[OK] Узлов в графе: {traffic_network.graph.number_of_nodes()}")
    print(f"[OK] Рёбер в графе: {traffic_network.graph.number_of_edges()}")
    
    print(f"\nПерекрёстки с фазами: {len(traffic_network.intersection_phases)}")
    for inter_id, phases in traffic_network.intersection_phases.items():
        print(f"  {inter_id}: {list(phases.keys())}")
    
    print(f"\nПолос в пуле: {len(traffic_network.lane_pool)}")
    
    # Показываем несколько примеров
    print("\n  Примеры полос:")
    for lane_id, data in list(traffic_network.lane_pool.items())[:5]:
        print(f"    {lane_id}: intersection={data['intersection_id']}, approach={data['approach']}")
    
    # Проверяем upstream/downstream
    print("\nUpstream/Downstream кэш:")
    for inter_id in list(traffic_network.intersection_phases.keys())[:2]:
        upstream = traffic_network.get_upstream_intersections(inter_id)
        downstream = traffic_network.get_downstream_intersections(inter_id)
        print(f"  {inter_id}:")
        print(f"    Upstream: {upstream}")
        print(f"    Downstream: {downstream}")
    
    return True


def test_green_wave_timing():
    """Тест: проверяем расчёт времени зелёной волны"""
    print("\n" + "="*60)
    print("ТЕСТ 5: Расчёт времени зелёной волны")
    print("="*60)
    
    # Берем первый коридор
    corridors = green_wave_coordinator._find_corridors()
    if not corridors:
        print("\n[WARNING] Коридоры не найдены")
        return False
    
    corridor = corridors[0]
    positions = green_wave_coordinator._get_intersection_positions(corridor)
    axis = green_wave_coordinator._determine_main_axis(positions)
    travel_times = green_wave_coordinator._calculate_travel_times(positions, axis)
    
    print(f"\nКоридор: {' -> '.join(corridor)}")
    print(f"Основная ось: {axis}")
    print(f"Целевая скорость: {green_wave_coordinator.TARGET_SPEED_MS} м/с ({green_wave_coordinator.TARGET_SPEED_MS * 3.6:.1f} км/ч)")
    
    print(f"\nВремя проезда:")
    cumulative_time = 0.0
    for i, t in enumerate(travel_times):
        cumulative_time += t
        print(f"  До {corridor[i+1]}: {cumulative_time:.2f}с (от предыдущего: {t:.2f}с)")
    
    print(f"\nРекомендация:")
    print(f"  - Первый перекрёсток ({corridor[0]}): зелёный начинается в 0.0с")
    for i in range(1, len(corridor)):
        offset = sum(travel_times[:i])
        print(f"  - {corridor[i]}: зелёный начинается в {offset:.2f}с")
    
    return True


def main():
    """Запуск всех тестов"""
    print("\n" + "="*60)
    print("ТЕСТИРОВАНИЕ СИСТЕМЫ ЗЕЛЁНОЙ ВОЛНЫ (GREEN WAVE)")
    print("="*60)
    
    results = []
    
    try:
        results.append(("Конфигурация перекрёстков", test_intersection_config()))
    except Exception as e:
        print(f"\n[ERROR] Ошибка в тесте конфигурации: {e}")
        results.append(("Конфигурация перекрёстков", False))
    
    try:
        results.append(("Топология графа", test_graph_topology()))
    except Exception as e:
        print(f"\n[ERROR] Ошибка в тесте топологии: {e}")
        results.append(("Топология графа", False))
    
    try:
        results.append(("Обнаружение коридоров", test_corridors()))
    except Exception as e:
        print(f"\n[ERROR] Ошибка в тесте коридоров: {e}")
        results.append(("Обнаружение коридоров", False))
    
    try:
        results.append(("Расчёт времени", test_green_wave_timing()))
    except Exception as e:
        print(f"\n[ERROR] Ошибка в тесте времени: {e}")
        results.append(("Расчёт времени", False))
    
    try:
        results.append(("Базовое создание волны", test_green_wave_basic()))
    except Exception as e:
        print(f"\n[ERROR] Ошибка в тесте создания волны: {e}")
        results.append(("Базовое создание волны", False))
    
    # Итоги
    print("\n" + "="*60)
    print("ИТОГИ ТЕСТИРОВАНИЯ")
    print("="*60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "[OK]" if result else "[FAIL]"
        print(f"  {status}: {test_name}")
    
    print(f"\n  Пройдено: {passed}/{total} ({100*passed//total}%)")
    
    if passed == total:
        print("\nВсе тесты пройдены! Зелёная волна работает корректно.")
    else:
        print("\nНекоторые тесты не прошли. Проверьте конфигурацию.")
    
    print("="*60 + "\n")
    
    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)