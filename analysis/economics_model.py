"""Экономическая модель «Интеллектуальной системы управления светофорными объектами».

Расчёт для конкурса «100 идей для Беларуси» (номинация ИКТ / умные города):
  - рынок (TAM/SAM/SOM);
  - CAPEX и OPEX внедрения (с учётом того, что часть перекрёстков уже имеет камеры);
  - экономический эффект актуального управления против фиксированного таймера;
  - общественная (городская) экономика пилота;
  - вендорская экономика (грант на доработку + масштабирование);
  - unit-экономика, чувствительность, сценарии.

Запуск:  python -m analysis.economics_model  (или python analysis/economics_model.py)
Зависимости: только стандартная библиотека. Все допущения — в ASSUMPTIONS ниже.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Допущения и источники -------------------------------------------------
ASSUMPTIONS = {
    "usd_rate": {"v": 3.0392, "unit": "BYN/USD", "note": "НБРБ, 12.10.2026"},
    "refin_rate": {"v": 0.0925, "unit": "%", "note": "Ставка рефинансирования НБРБ, с 01.06.2026"},
    "discount_rate": {"v": 0.12, "unit": "%", "note": "9.25% (НБРБ) + 2.75 п.п. премия за риск (консервативно)"},
    "horizon": {"v": 5, "unit": "лет", "note": "2027–2031"},
    "wage_oblast": {"v": 2668, "unit": "BYN/мес", "note": "Средняя ЗП Витебской обл., авг. 2026 (Белстат)"},
    "work_hours_month": {"v": 170, "unit": "ч/мес", "note": "Расчётно 21.25 раб. дня × 8 ч"},
    "vehicle_occupancy": {"v": 1.25, "unit": "чел/авто", "note": "Средняя наполняемость легкового авто (типовая оценка)"},
    "value_of_time": {"v": 20, "unit": "BYN/ч", "note": "≈ (2668/170)×1.25 ≈ 19.6 → с округлением 20"},
    "fuel_consumption_delay": {"v": 1.3, "unit": "л/ч", "note": "Средний расход в режиме задержки/циклов разгона-торможения"},
    "fuel_price": {"v": 2.2, "unit": "BYN/л", "note": "AI-95, рынок БР, окт. 2026 (оценка)"},
    "realization": {"v": 0.6, "unit": "доля", "note": "Система реализует только 60% теоретически возможной экономии (инженерный консерватизм)"},
    "willingness_time": {"v": 0.5, "unit": "доля", "note": "Из стоимости времени водителей в бюджет города конвертируется 50% (осторожная оценка)"},
    "traffic_objects_vitebsk": {"v": 151, "unit": "шт", "note": "Светофорных объектов Витебска (ГАИ УВД, 2026)"},
    "traffic_objects_oblast": {"v": 285, "unit": "шт", "note": "Объекты на обслуживании СМЭП Витебского облисполкома (в т.ч. Орша, Полоцк, Новополоцк)"},
    "tam_belarus": {"v": 4200, "unit": "шт", "note": "Оценка светофорных объектов по городам РБ (экстраполяция данных Витебска, ГАИ)"},
}

# --- Затраты на один перекрёсток (рыночные цены, BYN) -----------------------
EQUIPMENT = {
    "camera_unit": 700,          # IP-камера-детектор (Hikvision-класс), рынок РБ
    "cameras_per_int": 4,        # по направлениям движения
    "edge_compute": 4500,        # mini-PC уровня Jetson/GPU для инференса YOLO
    "controller_interface": 1200,  # шлюз к контроллеру (CRU/АСУДД), кабели, переходники
    "comms_install": 1500,       # коммутатор, кабельные трассы, кронштейны камер
    "install_labor": 2000,       # монтаж + пусконаладка + интеграция
    "central_server_share": 2000,  # доля сервера ЦУП (8 000 BYN / 4 перекрёстка пилота)
}
CAM_CAPEX = EQUIPMENT["camera_unit"] * EQUIPMENT["cameras_per_int"] + EQUIPMENT["comms_install"]
NEW_INT_CAPEX = CAM_CAPEX + EQUIPMENT["edge_compute"] + EQUIPMENT["controller_interface"] + EQUIPMENT["install_labor"] + EQUIPMENT["central_server_share"]
REUSE_INT_CAPEX = EQUIPMENT["edge_compute"] + EQUIPMENT["controller_interface"] + EQUIPMENT["install_labor"] + EQUIPMENT["central_server_share"]

PRICE_MARKUP = 1.35            # наценка цены продажи внедрения (маржа вендора ~26%)
NEW_INT_PRICE = round(NEW_INT_CAPEX * PRICE_MARKUP, 2)
REUSE_INT_PRICE = round(REUSE_INT_CAPEX * PRICE_MARKUP, 2)
SLA_PRICE_INT = 2400           # годовая подписка на техподдержку, BYN/перекрёсток
CITY_EXTRA = 3000              # разовые административные/интеграционные затраты города
SLA_COST_INT = 500             # связь+питание+профилактика в год на перекрёсток (валовая)
CENTRAL_OPEX_BASE = 14200      # ЦУП/поддержка: 0.3×ЗП инженера (3 500×(1.35 по 12 мес)) + облако 1 600
CENTRAL_OPEX_PER_INT = 350     # рост поддержки при масштабировании (на перекрёсток в год)

# --- Типы перекрёстков -------------------------------------------------------
@dataclass(frozen=True)
class IntersectionType:
    key: str
    veh_day: int
    delay_s: float
    active_share: float
    reduction: float
    label: str

TYPES = {
    "periphery": IntersectionType("periphery", 12000, 25, 0.50, 0.10, "Периферия (МВП)"),
    "medium": IntersectionType("medium", 18000, 35, 0.55, 0.11, "Среднезагруженный"),
    "arterial": IntersectionType("arterial", 22000, 40, 0.60, 0.12, "Коридор «Зеленогурская — Ленина»"),
}

# Пилотные наборы: (тип, есть ли камеры уже)
PILOTS = {
    "mvp": {
        "label": "МВП — периферийные перекрёстки",
        "note": "4 перекрёстка вне центра, поток умеренный; 2 уже с камерами, 2 без",
        "ints": [
            ("periphery", True),
            ("periphery", True),
            ("periphery", False),
            ("periphery", False),
        ],
    },
    "corridor": {
        "label": "Коридор «Зеленогурская — Ленина»",
        "note": "4 перекрёстка основной артерии; 2 уже с камерами, 2 без",
        "ints": [
            ("arterial", True),
            ("arterial", True),
            ("arterial", False),
            ("arterial", False),
        ],
    },
}

# Масштабирование вендора (новых подключений по годам)
VENDOR_INSTALLS = {1: 4, 2: 8, 3: 15, 4: 25, 5: 40}
VENDOR_NEW_CAM_SHARE = 0.5     # доля внедрений без существующих камер

# --- Грантовый запрос (доработка до пилота, TRL 4->7) ------------------------
RND = {
    "months": 6,
    "net_fot_month": 7400,     # dev 3 000 + CV/ML 3 200 + тест/PM 1 200 (нетто)
    "payroll_factor": 1.35,   # ФСЗН и отчисления работодателя
    "integration": 6000,       # сертификация/интеграция со СМЭП, ПНР
    "docs_training": 2000,
    "lab_hw": 5000,
    "contingency": 0.08,
}


# --- Расчётные функции --------------------------------------------------------
def money(x: float) -> str:
    return f"{round(x):,}".replace(",", " ")


def money_usd(x: float) -> str:
    return f"{round(x / ASSUMPTIONS['usd_rate']['v']):,}".replace(",", " ")


def rnd_request() -> float:
    gross_month = RND["net_fot_month"] * RND["payroll_factor"]
    fot = gross_month * RND["months"]
    base = fot + RND["integration"] + RND["docs_training"] + RND["lab_hw"]
    return base * (1 + RND["contingency"])


def benefit_per_int_year(t: IntersectionType, realization: float, wtp: float) -> dict:
    hours_saved_year = t.veh_day * t.delay_s / 3600 * t.active_share * t.reduction * 365
    fuel_gross = hours_saved_year * ASSUMPTIONS["fuel_consumption_delay"]["v"] * ASSUMPTIONS["fuel_price"]["v"]
    time_gross = hours_saved_year * ASSUMPTIONS["value_of_time"]["v"]
    fuel = fuel_gross * realization
    time = time_gross * realization * wtp
    return {"hours": hours_saved_year, "fuel_gross": fuel_gross, "time_gross": time_gross, "fuel": fuel, "time": time, "total": fuel + time}


def deploy_cost(pilot: dict) -> float:
    total = 0.0
    for _, has_cam in pilot["ints"]:
        total += REUSE_INT_PRICE if has_cam else NEW_INT_PRICE
    return total + CITY_EXTRA


def deploy_vendor_cost(pilot: dict) -> float:
    total = 0.0
    for _, has_cam in pilot["ints"]:
        total += REUSE_INT_CAPEX if has_cam else NEW_INT_CAPEX
    return total


def public_cashflows(pilot: dict, realization: float, wtp: float, sla: float) -> list[float]:
    y0 = -deploy_cost(pilot)
    benefit = sum(benefit_per_int_year(TYPES[k], realization, wtp)["total"] for k, _ in pilot["ints"])
    cfs = [y0]
    for _ in range(1, ASSUMPTIONS["horizon"]["v"] + 1):
        cfs.append(benefit - sla * len(pilot["ints"]))
    return cfs


def npv(cfs: list[float], rate: float) -> float:
    return sum(f / (1 + rate) ** t for t, f in enumerate(cfs))


def irr(cfs: list[float]) -> float | str:
    lo, hi = -0.99, 20.0
    f_lo = npv(cfs, lo)
    f_hi = npv(cfs, hi)
    if f_lo * f_hi > 0 or (f_lo > 0 and f_hi > 0):
        if f_hi > 0:
            return ">2000%"
        if f_lo < 0:
            return "<-99%"
    for _ in range(200):
        mid = (lo + hi) / 2
        f = npv(cfs, mid)
        if abs(f) < 1e-9:
            break
        if (f > 0) == (f_lo > 0):
            lo, f_lo = mid, f
        else:
            hi = mid
    return (lo + hi) / 2


def payback(cfs: list[float], rate: float) -> tuple[float, float]:
    cum = 0.0
    simple = None
    for t, f in enumerate(cfs):
        cum += f
        if simple is None and cum >= 0:
            prev = cum - f
            simple = t - prev / f if f else t
            break
    cum = 0.0
    disc = None
    for t, f in enumerate(cfs):
        cum += f / (1 + rate) ** t
        if disc is None and cum >= 0:
            prev = cum - f / (1 + rate) ** t
            disc = t - (prev / (f / (1 + rate) ** t)) if f else t
            break
    return (simple if simple is not None else float("nan"), disc if disc is not None else float("nan"))


def vendor_cfs() -> list[float]:
    cfs = [-rnd_request()]
    cum = 0
    for year in range(1, ASSUMPTIONS["horizon"]["v"] + 1):
        installs = VENDOR_INSTALLS[year]
        cum += installs
        avg_price = REUSE_INT_PRICE * VENDOR_NEW_CAM_SHARE + NEW_INT_PRICE * (1 - VENDOR_NEW_CAM_SHARE)
        avg_cost = REUSE_INT_CAPEX * VENDOR_NEW_CAM_SHARE + NEW_INT_CAPEX * (1 - VENDOR_NEW_CAM_SHARE)
        revenue = installs * avg_price + SLA_PRICE_INT * cum
        cost = installs * avg_cost + CENTRAL_OPEX_BASE + CENTRAL_OPEX_PER_INT * cum + SLA_COST_INT * cum
        cfs.append(revenue - cost)
    return cfs


def pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"


# --- Вывод --------------------------------------------------------------------
def fmt_pct(t: IntersectionType) -> str:
    return pct(t.reduction)


def print_report() -> None:
    r = ASSUMPTIONS["discount_rate"]["v"]
    out: list[str] = []
    out.append("# Экономическая модель — результаты\n")
    out.append("Все суммы в BYN, в USD по курсу НБРБ " + str(ASSUMPTIONS["usd_rate"]["v"]) + ".\n")

    out.append("## 1. Допущения и источники\n")
    out.append("| Параметр | Значение | Источник |")
    out.append("|---|---|---|")
    for k, d in ASSUMPTIONS.items():
        val = pct(d["v"], 2) if k == "refin_rate" else (pct(d["v"]) if k == "discount_rate" else f"{d['v']} {d['unit']}".strip())
        out.append(f"| {k} | {val} | {d['note']} |")
    out.append("")

    out.append("## 2. CAPEX и OPEX на перекрёсток\n")
    out.append("| Статья | Перекрёсток без камер | Перекрёсток с имеющимися камерами |")
    out.append("|---|---|---|")
    out.append(f"| Камеры-детекторы ({EQUIPMENT['cameras_per_int']}×{EQUIPMENT['camera_unit']}) | {money(CAM_CAPEX)} | 0 (переиспользуем) |")
    out.append(f"| Edge-компьютер (GPU/YOLO) | {money(EQUIPMENT['edge_compute'])} | {money(EQUIPMENT['edge_compute'])} |")
    out.append(f"| Шлюз к контроллеру | {money(EQUIPMENT['controller_interface'])} | {money(EQUIPMENT['controller_interface'])} |")
    out.append(f"| Сеть/монтаж камер | {money(EQUIPMENT['comms_install'])} | 0 |")
    out.append(f"| Монтаж и ПНР | {money(EQUIPMENT['install_labor'])} | {money(EQUIPMENT['install_labor'])} |")
    out.append(f"| Доля сервера ЦУП | {money(EQUIPMENT['central_server_share'])} | {money(EQUIPMENT['central_server_share'])} |")
    out.append(f"| **CAPEX вендора** | **{money(NEW_INT_CAPEX)}** | **{money(REUSE_INT_CAPEX)}** |")
    out.append(f"| Цена внедрения для города (×{PRICE_MARKUP}) | {money(NEW_INT_PRICE)} | {money(REUSE_INT_PRICE)} |")
    out.append(f"| Годовая подписка SLA | {money(SLA_PRICE_INT)} | {money(SLA_PRICE_INT)} |")
    out.append(
        f"| Референт | $8 000–$116 000 CAPEX/перекр-к, ~$10 000/год O&M (US DOT ITS JPO) | наш вариант — ретрофит, без замены светофоров |"
    )
    out.append("")

    out.append("## 3. Рынок TAM/SAM/SOM\n")
    out.append("| Уровень | Значение |")
    out.append("|---|---|")
    out.append(f"| TAM — светофорные объекты РБ | {money(ASSUMPTIONS['tam_belarus']['v'])} ({ASSUMPTIONS['tam_belarus']['note']}) |")
    out.append(f"| SAM — область (СМЭП) | {money(ASSUMPTIONS['traffic_objects_oblast']['v'])} |")
    out.append(f"| Город (база) | {money(ASSUMPTIONS['traffic_objects_vitebsk']['v'])} |")
    out.append(f"| SOM — план вендора за 5 лет | {money(sum(VENDOR_INSTALLS.values()))} ({pct(sum(VENDOR_INSTALLS.values()) / ASSUMPTIONS['traffic_objects_oblast']['v'], 0)} области) |")
    out.append("")

    out.append("## 4. Экономический эффект против фиксированного таймера (на 1 перекрёсток/год)\n")
    out.append("| Тип | Поток, авто/сут | Задержка, с | Активное время | Сниж. задержки | Сэкономлено, ч/год | Топливо (брутто) | Время (брутто) | Эффект (с учётом 0.6×0.5) |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for t in TYPES.values():
        b = benefit_per_int_year(t, ASSUMPTIONS["realization"]["v"], ASSUMPTIONS["willingness_time"]["v"])
        out.append(
            f"| {t.label} | {t.veh_day:,} | {t.delay_s:.0f} | {pct(t.active_share, 0)} | {fmt_pct(t)} | {b['hours']:,.0f} | {money(b['fuel_gross'])} | {money(b['time_gross'])} | **{money(b['total'])}** |"
        )
    out.append("")

    out.append("## 5. Общественная (городская) экономика пилота\n")
    metrics = {"deploy": "Внедрение (г. 0)", "ben": "Эффект в год", "sla": "Подписка SLA/год", "npv": "NPV (r=12%, 5 лет)", "irr": "IRR"}
    vals: dict[str, dict[str, str]] = {}
    for key, pilot in PILOTS.items():
        dc = deploy_cost(pilot)
        cf = public_cashflows(pilot, ASSUMPTIONS["realization"]["v"], ASSUMPTIONS["willingness_time"]["v"], SLA_PRICE_INT)
        n = npv(cf, r)
        ir = irr(cf)
        ben = sum(benefit_per_int_year(TYPES[k], ASSUMPTIONS["realization"]["v"], ASSUMPTIONS["willingness_time"]["v"])["total"] for k, _ in pilot["ints"])
        vals[key] = {
            "deploy": f"{money(dc)} ({money_usd(dc)} USD)",
            "ben": f"{money(ben)} ({money_usd(ben)} USD)",
            "sla": f"{money(SLA_PRICE_INT * len(pilot['ints']))} ({money_usd(SLA_PRICE_INT * len(pilot['ints']))} USD)",
            "npv": f"**{money(n)}** ({money_usd(n)} USD)",
            "irr": pct(ir) if isinstance(ir, float) else ir,
        }
    out.append(f"| Показатель | {PILOTS['mvp']['label']} | {PILOTS['corridor']['label']} |")
    out.append("|---|---|---|")
    for m, label in metrics.items():
        out.append(f"| {label} | {vals['mvp'][m]} | {vals['corridor'][m]} |")
    out.append("")
    for key, pilot in PILOTS.items():
        dc = deploy_cost(pilot)
        cf = public_cashflows(pilot, ASSUMPTIONS["realization"]["v"], ASSUMPTIONS["willingness_time"]["v"], SLA_PRICE_INT)
        n = npv(cf, r)
        ir = irr(cf)
        sp, dp = payback(cf, r)
        out.append(f"**{PILOTS[key]['label']}** — {PILOTS[key]['note']}:")
        out.append("| Показатель | Значение (BYN) | Значение (USD) |")
        out.append("|---|---|---|")
        out.append(f"| Внедрение (г. 0) | {money(dc)} | {money_usd(dc)} |")
        ben = sum(benefit_per_int_year(TYPES[k], ASSUMPTIONS["realization"]["v"], ASSUMPTIONS["willingness_time"]["v"])["total"] for k, _ in pilot["ints"])
        out.append(f"| Эффект в год | {money(ben)} | {money_usd(ben)} |")
        out.append(f"| Подписка SLA в год | {money(SLA_PRICE_INT * len(pilot['ints']))} | {money_usd(SLA_PRICE_INT * len(pilot['ints']))} |")
        out.append(f"| NPV (r=12%, 5 лет) | **{money(n)}** | **{money_usd(n)}** |")
        out.append(f"| IRR | {pct(ir) if isinstance(ir, float) else ir} | — |")
        out.append(f"| Окупаемость простая / дисконт. | {sp:.1f} / {dp:.1f} лет | — |")
        out.append("")

    out.append("## 6. Вендорская экономика (грант на доработку + масштабирование)\n")
    out.append(f"**Грантовый запрос (TRL 4→7): {money(rnd_request())} BYN ({money_usd(rnd_request())} USD)**\n")
    out.append("| Статья | BYN | USD |")
    out.append("|---|---|---|")
    fot = RND["net_fot_month"] * RND["payroll_factor"] * RND["months"]
    base = fot + RND["integration"] + RND["docs_training"] + RND["lab_hw"]
    out.append(f"| ФОТ, {RND['months']} мес (нетто {money(RND['net_fot_month'])} × ФСЗН {RND['payroll_factor']}) | {money(fot)} | {money_usd(fot)} |")
    out.append(f"| Интеграция/сертификация/ПНР | {money(RND['integration'])} | {money_usd(RND['integration'])} |")
    out.append(f"| Документация, обучение | {money(RND['docs_training'])} | {money_usd(RND['docs_training'])} |")
    out.append(f"| Лаб. оборудование | {money(RND['lab_hw'])} | {money_usd(RND['lab_hw'])} |")
    out.append(f"| Резерв {pct(RND['contingency'], 0)} | {money(base * RND['contingency'])} | {money_usd(base * RND['contingency'])} |")
    out.append("")

    vc = vendor_cfs()
    out.append("| Год | Новых перекр. | Накопленно | Выручка | Затраты | Прибыль | Кумулятив |")
    out.append("|---|---|---|---|---|---|---|")
    cum = -rnd_request()
    for yr in range(1, ASSUMPTIONS["horizon"]["v"] + 1):
        inst = VENDOR_INSTALLS[yr]
        cum_i = sum(VENDOR_INSTALLS[y] for y in range(1, yr + 1))
        avg_price = REUSE_INT_PRICE * VENDOR_NEW_CAM_SHARE + NEW_INT_PRICE * (1 - VENDOR_NEW_CAM_SHARE)
        avg_cost = REUSE_INT_CAPEX * VENDOR_NEW_CAM_SHARE + NEW_INT_CAPEX * (1 - VENDOR_NEW_CAM_SHARE)
        rev = inst * avg_price + SLA_PRICE_INT * cum_i
        cost = inst * avg_cost + CENTRAL_OPEX_BASE + CENTRAL_OPEX_PER_INT * cum_i + SLA_COST_INT * cum_i
        prof = rev - cost
        cum += prof
        out.append(f"| 20{26 + yr} | {inst} | {cum_i} | {money(rev)} | {money(cost)} | {money(prof)} | {money(cum)} |")
    vn = npv(vc, r)
    vir = irr(vc)
    vsp, vdp = payback(vc, r)
    out.append(f"\n| Показатель (вендор) | Значение |")
    out.append("|---|---|")
    out.append(f"| NPV (r=12%, 5 лет) | **{money(vn)} BYN** ({money_usd(vn)} USD) |")
    out.append(f"| IRR | {pct(vir) if isinstance(vir, float) else vir} |")
    out.append(f"| Окупаемость простая / дисконт. | {vsp:.1f} / {vdp:.1f} лет |")
    out.append(f"| Точка безубыточности | в пределах года 3 (кумулятив становится ≥0) |")
    out.append("")

    out.append("## 7. Unit-экономика на перекрёсток\n")
    out.append("| Показатель | Без камер | С камерами |")
    out.append("|---|---|---|")
    out.append(f"| Цена внедрения (город) | {money(NEW_INT_PRICE)} | {money(REUSE_INT_PRICE)} |")
    out.append(f"| Себестоимость внедрения | {money(NEW_INT_CAPEX)} | {money(REUSE_INT_CAPEX)} |")
    out.append(f"| Маржинальная прибыль | {money(NEW_INT_PRICE - NEW_INT_CAPEX)} ({pct((NEW_INT_PRICE - NEW_INT_CAPEX) / NEW_INT_PRICE)}) | {money(REUSE_INT_PRICE - REUSE_INT_CAPEX)} ({pct((REUSE_INT_PRICE - REUSE_INT_CAPEX) / REUSE_INT_PRICE)}) |")
    out.append(f"| Годовая SLA (город) | {money(SLA_PRICE_INT)} | {money(SLA_PRICE_INT)} |")
    out.append("")

    out.append("## 8. Чувствительность (NPV общественной задачи, коридор)\n")
    cf_base = public_cashflows(PILOTS["corridor"], ASSUMPTIONS["realization"]["v"], ASSUMPTIONS["willingness_time"]["v"], SLA_PRICE_INT)
    npv_base = npv(cf_base, r)
    out.append(f"Базовый NPV = **{money(npv_base)} BYN** при снижении задержки 12%.\n")
    out.append("| Сценарий | NPV, BYN | Примечание |")
    out.append("|---|---|---|")
    for lbl, rf, cfx in [
        ("Эффект −25% (снижение 9%)", 0.75, "пессимист"),
        ("Базовый (12%)", 1.0, "база"),
        ("Эффект +25% (снижение 15%)", 1.25, "оптимист"),
        ("Поток −30%", 0.7, "периферия"),
        ("Поток +30%", 1.3, "рост города"),
        ("Дисконт 9%", 1.0, "гос. финансирование"),
        ("Дисконт 16%", 1.0, "высокий риск"),
    ]:
        if "Эффект" in lbl:
            cfx_ = public_cashflows(PILOTS["corridor"], ASSUMPTIONS["realization"]["v"] * rf, ASSUMPTIONS["willingness_time"]["v"], SLA_PRICE_INT)
            rr = r
        elif "Дисконт" in lbl:
            rr = 0.09 if "9%" in lbl else 0.16
            cfx_ = cf_base
        else:
            cfx_ = public_cashflows(
                PILOTS["corridor"],
                ASSUMPTIONS["realization"]["v"],
                ASSUMPTIONS["willingness_time"]["v"],
                SLA_PRICE_INT,
            )
            cfx_ = [cfx_[0]] + [f * rf for f in cfx_[1:]]
        out.append(f"| {lbl} | {money(npv(cfx_, rr))} | {cfx} |")
    out.append("")

    out.append("## 9. Что НЕ учтено в деньгах\n")
    for item in [
        "Безопасность: снижение ДТП на перекрёстках (оценивается +3–10% к эффекту, качественно)",
        "Экология: меньше выбросов CO₂/NOx в режиме остановок",
        "Приоритет общественного транспорта и «умная» пешеходная фаза (доп. выгода города)",
        "Инфляция и рост зарплат (оценка в текущих ценах)",
        "Административные издержки города и закупочные процедуры (госзакупка ПД)",
        "Риск: нормативная база (ТКП по ИТС, единый оператор) и ЛПО АСУДД «АГАТ»",
    ]:
        out.append(f"- {item}")
    out.append("")

    out.append("__Источники:__ Белстат (ЗП, авг. 2026); НБРБ (курс 12.10.2026, ставка рефин.); ГАИ УВД Витебского облисполкома и СМЭП (285 объектов); US DOT ITS JPO и PHOEBE/ASCT (референтные $8–116k CAPEX, ~$10k/год O&M); рынок видеонаблюдения РБ (Hikvision/BEWARD, цены собств. каталогов).")
    print("\n".join(out))


if __name__ == "__main__":
    print_report()