import flet as ft
import flet.canvas as cv
import asyncio
import json
import time
import websockets
import traceback


# ============ КОНФИГ ГРАФА ============
CV_WIDTH = 700
CV_HEIGHT = 500
NODE_R = 14


class TrafficMap:
    """Граф дорожной сети: точки (перекрёстки) + линии (дороги между ними).

    Строится ДИНАМИЧЕСКИ из данных телеметрии (traffic_network), без хардкода.
    Количество дорог у каждого перекрёстка берётся из данных (1-4 камеры).
    """

    def __init__(self):
        self.canvas = cv.Canvas(width=CV_WIDTH, height=CV_HEIGHT)
        self._labels = {}

        self._nodes = {}
        self._links = []
        self._phases = {}
        self._congestion = {}
        self._road_count = {}
        self._known_intersections = set()

        # Контейнер, который кладётся во вкладку "Граф" (обновляется при пересборке).
        self.container = ft.Container(alignment=ft.Alignment.CENTER)
        self._build()
        self.container.content = self.stack

    def _sync_topology(self):
        """Получить топологию из traffic_network и пересобрать граф при изменениях."""
        from backend.services.graph_manager import traffic_network

        topo = traffic_network.get_topology_for_ui()
        intersections = topo["intersections"]
        links = topo["links"]

        current_ids = set(intersections.keys())
        # Пересобираем только если набор перекрёстков изменился (появились новые из данных).
        if current_ids != self._known_intersections or not self._nodes:
            self._known_intersections = current_ids
            self._build_shapes(intersections, links)

    def _build_shapes(self, intersections: dict, links: list):
        if not intersections:
            self._nodes = {}
            self._links = []
            self.stack = ft.Stack(width=CV_WIDTH, height=CV_HEIGHT, controls=[self.canvas])
            self.container.content = self.stack
            return

        xs = [p["position"].get("x", 0) for p in intersections.values()]
        zs = [p["position"].get("z", 0) for p in intersections.values()]
        min_x, max_x = min(xs), max(xs)
        min_z, max_z = min(zs), max(zs)
        range_x = (max_x - min_x) or 1
        range_z = (max_z - min_z) or 1
        margin = 80

        self._nodes = {}
        for inter_id, info in intersections.items():
            pos = info["position"]
            px = margin + (pos.get("x", 0) - min_x) / range_x * (CV_WIDTH - 2 * margin)
            py = margin + (pos.get("z", 0) - min_z) / range_z * (CV_HEIGHT - 2 * margin)
            self._nodes[inter_id] = (px, py)
            self._road_count[inter_id] = info.get("num_roads", 0)

        self._links = []
        for src, dst in links:
            if src in self._nodes and dst in self._nodes:
                x1, y1 = self._nodes[src]
                x2, y2 = self._nodes[dst]
                self._links.append((src, dst, x1, y1, x2, y2))

        shapes = [cv.Circle(
            x=CV_WIDTH // 2, y=CV_HEIGHT // 2, radius=1000,
            paint=ft.Paint(color="#1a1a2e", style=ft.PaintingStyle.FILL),
        )]

        for src, dst, x1, y1, x2, y2 in self._links:
            shapes.append(cv.Line(
                x1, y1, x2, y2,
                paint=ft.Paint(color="#555555", stroke_width=4, style=ft.PaintingStyle.STROKE),
            ))

        for inter_id, (px, py) in self._nodes.items():
            shapes.append(cv.Circle(
                x=px, y=py, radius=NODE_R + 3,
                paint=ft.Paint(color="#000000", style=ft.PaintingStyle.FILL),
            ))
            shapes.append(cv.Circle(
                x=px, y=py, radius=NODE_R,
                paint=ft.Paint(color=self._node_color(inter_id), style=ft.PaintingStyle.FILL),
            ))
            shapes.append(cv.Circle(
                x=px, y=py, radius=NODE_R,
                paint=ft.Paint(color="white", style=ft.PaintingStyle.STROKE, stroke_width=2),
            ))

        self.canvas.shapes = shapes

        label_controls = []
        self._labels = {}
        for inter_id, (px, py) in self._nodes.items():
            roads = self._road_count.get(inter_id, 0)
            lbl = ft.Text(
                f"{inter_id}\n({roads} дор.)",
                color="white", size=10, weight=ft.FontWeight.BOLD,
                text_align=ft.TextAlign.CENTER,
            )
            self._labels[inter_id] = lbl
            label_controls.append(
                ft.Container(
                    content=lbl,
                    left=px - 50,
                    top=py - NODE_R - 40,
                    width=100,
                    height=34,
                )
            )

        self.stack = ft.Stack(
            width=CV_WIDTH,
            height=CV_HEIGHT,
            controls=[self.canvas] + label_controls,
        )
        self.container.content = self.stack

    def _build(self):
        self._sync_topology()

    def _node_color(self, inter_id: str) -> str:
        c = self._congestion.get(inter_id, 0.0)
        if c > 0.7:
            return "#ff4444"
        elif c > 0.4:
            return "#ffaa00"
        return "#44ff44"

    def _road_color(self, src: str, dst: str) -> str:
        phase_src = self._phases.get(src, "")
        phase_dst = self._phases.get(dst, "")
        if phase_src or phase_dst:
            return "#4CAF50"
        return "#555555"

    def refresh(self):
        # Подхватываем новые перекрёстки/дороги, появившиеся в данных.
        self._sync_topology()

        shapes = self.canvas.shapes
        if not shapes:
            return

        link_idx = 1
        for src, dst, x1, y1, x2, y2 in self._links:
            if link_idx < len(shapes):
                color = self._road_color(src, dst)
                shapes[link_idx].paint = ft.Paint(color=color, stroke_width=4, style=ft.PaintingStyle.STROKE)
            link_idx += 1

        node_idx = link_idx
        for inter_id in self._nodes:
            if node_idx + 1 < len(shapes):
                shapes[node_idx + 1].paint = ft.Paint(
                    color=self._node_color(inter_id), style=ft.PaintingStyle.FILL
                )
            node_idx += 3

    def update_lane_update(self, data: dict):
        inter_id = data.get("intersection_id", "")
        phase = data.get("current_phase", "")
        if inter_id and phase:
            self._phases[inter_id] = phase

    def update_cloud_state(self, data: dict):
        changed = False
        for inter_id, summary in data.get("intersections_summary", {}).items():
            congestion = summary.get("avg_congestion", 0)
            if self._congestion.get(inter_id) != congestion:
                self._congestion[inter_id] = congestion
                changed = True


# ============ UI ФАБРИКА ============

class TrafficUIFactory:
    def __init__(self):
        self.page = None
        self.lane_cards = {}

        self.grid = ft.Column(spacing=20, scroll=ft.ScrollMode.AUTO, expand=True)

        self.status_text = ft.Text("ОЖИДАНИЕ СЕРВЕРА...", color="yellow", weight=ft.FontWeight.BOLD)
        self.total_cars_text = ft.Text("Машин в сети: 0", size=14)
        self.cascade_text = ft.Text("Каскадных команд: 0", size=14)
        self.green_wave_text = ft.Text("Зелёная волна: ❌", size=14, color="red")

        self.intersection_containers = {}
        self.intersection_headers = {}
        self.intersection_lanes_layout = {}

        self._inter_phase = {}
        self._inter_congestion = {}

        self.map = TrafficMap()

        self._pending_lane_updates = []
        self._pending_cloud_states = []
        self._flush_task = None
        self._last_statistics = {}  # Статистика, полученная через WebSocket

        self.filter_dropdown = ft.Dropdown(
            label="Фильтр перекрёстков",
            options=[ft.dropdown.Option("Все перекрёстки")],
            value="Все перекрёстки",
            width=300,
        )
        self.filter_dropdown.on_change = self.on_filter_change

    def build_ui(self, page: ft.Page):
        self.page = page
        page.title = "UTC-UX Network Fusion Engine"
        page.theme_mode = ft.ThemeMode.DARK
        page.padding = 20

# ===== Вкладка "Список" =====
        list_tab = ft.Container(
            alignment=ft.Alignment.TOP_LEFT,
            content=ft.Column([
                ft.Row(
                    [
                        ft.Text("📊 Распределенный UTC-UX Мониторинг", size=28, weight=ft.FontWeight.BOLD),
                        ft.Column(
                            [self.status_text, self.total_cars_text, self.cascade_text, self.green_wave_text],
                            spacing=4,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Divider(),
                self.filter_dropdown,
                ft.Divider(),
                self.grid,
            ], scroll=ft.ScrollMode.AUTO, expand=True),
        )

        # ===== Вкладка "Статистика" =====
        # --- Header метрики ---
        self.uptime_text = ft.Text("Uptime: -", size=14, weight=ft.FontWeight.BOLD, color="grey")
        self.network_load_text = ft.Text("Загрузка сети: -", size=16, weight=ft.FontWeight.BOLD, color="yellow")
        self.total_switches_text = ft.Text("Переключений фаз: -", size=14)
        self.emergency_count_text = ft.Text("🚨 Emergency: 0", size=14, color="red")
        self.anomaly_count_text = ft.Text("⚠️ Аномалий: 0", size=14, color="orange")
        self.gw_count_text = ft.Text("🟢 Зелёных волн: 0", size=14, color="green")
        
        # --- Рейтинг загруженности ---
        self.ranking_container = ft.Column(spacing=6)
        ranking_section = ft.Container(
            content=ft.Column([
                ft.Text("🏆 Рейтинг загруженности", size=18, weight=ft.FontWeight.BOLD),
                ft.Divider(height=4),
                self.ranking_container,
            ]),
            bgcolor="#1e1e2e", padding=15, border_radius=10,
        )
        
        # --- График загрузки (кастомный canvas) ---
        self.congestion_canvas = cv.Canvas(width=680, height=200)
        self.congestion_canvas_container = ft.Container(
            content=ft.Column([
                ft.Text("📈 Congestion time series (5 мин)", size=16, weight=ft.FontWeight.BOLD),
                self.congestion_canvas,
            ]),
            bgcolor="#1e1e2e", padding=15, border_radius=10,
        )
        
        # --- Тренды ---
        self.trend_container = ft.Column(spacing=6)
        trend_section = ft.Container(
            content=ft.Column([
                ft.Text("📊 Тренды загрузки", size=18, weight=ft.FontWeight.BOLD),
                ft.Divider(height=4),
                self.trend_container,
            ]),
            bgcolor="#1e1e2e", padding=15, border_radius=10,
        )
        
        # --- Emergency лог ---
        self.emergency_log_container = ft.Column(spacing=4)
        emergency_section = ft.Container(
            content=ft.Column([
                ft.Text("🚨 Emergency лог", size=18, weight=ft.FontWeight.BOLD),
                ft.Divider(height=4),
                self.emergency_log_container,
            ]),
            bgcolor="#1e1e2e", padding=15, border_radius=10,
        )
        
        # --- Green Wave лог ---
        self.green_wave_log_container = ft.Column(spacing=4)
        green_wave_section = ft.Container(
            content=ft.Column([
                ft.Text("🟢 Зелёные волны", size=18, weight=ft.FontWeight.BOLD),
                ft.Divider(height=4),
                self.green_wave_log_container,
            ]),
            bgcolor="#1e1e2e", padding=15, border_radius=10,
        )
        
        # --- Аномалии ---
        self.anomaly_log_container = ft.Column(spacing=4)
        anomaly_section = ft.Container(
            content=ft.Column([
                ft.Text("⚠️ Аномалии и превышения", size=18, weight=ft.FontWeight.BOLD),
                ft.Divider(height=4),
                self.anomaly_log_container,
            ]),
            bgcolor="#1e1e2e", padding=15, border_radius=10,
        )
        
        stats_tab = ft.Container(
            alignment=ft.Alignment.TOP_LEFT,
            content=ft.Column([
                ft.Text("📈 Аналитика трафика", size=24, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                ft.Row([
                    self.uptime_text,
                    self.network_load_text,
                    self.total_switches_text,
                    self.emergency_count_text,
                    self.anomaly_count_text,
                    self.gw_count_text,
                ], spacing=16, wrap=True),
                ft.Divider(),
                ft.Column([
                    ranking_section,
                    ft.Divider(height=8, color="transparent"),
                    self.congestion_canvas_container,
                    ft.Divider(height=8, color="transparent"),
                    trend_section,
                    ft.Divider(height=8, color="transparent"),
                    emergency_section,
                    ft.Divider(height=8, color="transparent"),
                    green_wave_section,
                    ft.Divider(height=8, color="transparent"),
                    anomaly_section,
                ], scroll=ft.ScrollMode.AUTO, expand=True),
            ], scroll=ft.ScrollMode.AUTO, expand=True),
        )

        # ===== Табы =====
        self.tab_bar = ft.TabBar(
            tab_alignment=ft.TabAlignment.START,
            tabs=[
                ft.Tab(label=ft.Text("📋 Список")),
                ft.Tab(label=ft.Text("📈 Статистика")),
            ],
        )

        self.tab_bar_view = ft.TabBarView(
            expand=True,
            controls=[list_tab, stats_tab],
        )

        self.tabs = ft.Tabs(
            length=2,
            selected_index=0,
            expand=True,
            content=ft.Column(
                expand=True,
                controls=[self.tab_bar, self.tab_bar_view],
            ),
        )

        page.add(ft.SafeArea(expand=True, content=self.tabs))
        self._flush_task = asyncio.create_task(self._flush_loop())
        page.run_task(self.connect_to_backend)

    def _refresh_header(self, inter_id: str):
        if inter_id not in self.intersection_headers:
            return
        phase = self._inter_phase.get(inter_id, "?")
        congestion = self._inter_congestion.get(inter_id, "")
        self.intersection_headers[inter_id].value = (
            f"🛑 Перекрёсток: {inter_id.upper()} | Фаза: {phase}"
            + (f" | Загрузка: {congestion}" if congestion else "")
        )

    def _create_lane_card(self, inter_id: str, lane_id: str):
        """Карточка ОДНОЙ ДОРОГИ: цветной кружок (светофор) + название + шкала загрузки.
        Каждая дорога — отдельная строка (для X-перекрёстка с 4 камерами будет
        4 строки, 4 кружка, 4 шкалы)."""
        phase_text = ft.Text("Фаза: -", size=11, color="grey")
        count_text = ft.Text("0 маш.", size=13, weight=ft.FontWeight.BOLD)
        load_text = ft.Text("0/0", size=10, color="grey")
        load_bar = ft.ProgressBar(value=0.0, width=170, color="green", bgcolor="#333333")
        light_indicator = ft.Container(width=22, height=22, border_radius=11, bgcolor="red")

        card = ft.Container(
            content=ft.Row([
                light_indicator,
                ft.Column([
                    ft.Text(f"🛣️ {lane_id}", size=14, weight=ft.FontWeight.BOLD),
                    ft.Row([count_text, phase_text], spacing=10),
                ], spacing=3, expand=True),
                ft.Column([
                    ft.Row([ft.Text("Загрузка", size=10, color="grey"), load_text], spacing=5),
                    load_bar,
                ], spacing=3),
            ], alignment=ft.MainAxisAlignment.CENTER, spacing=14),
            bgcolor="surfacevariant", padding=10, border_radius=10, expand=True,
        )
        return card, phase_text, count_text, load_text, load_bar, light_indicator

    @staticmethod
    def _light_to_color(command: str) -> str:
        return {"GREEN": "green", "RED": "red"}.get(command, "grey")

    async def _flush_loop(self):
        while True:
            await asyncio.sleep(1.0)
            try:
                self._flush_updates()
            except Exception as e:
                print(f"[DEBUG _flush_loop] ERROR: {e}")
                traceback.print_exc()

    def _flush_updates(self):
        if not self.page:
            print("[DEBUG _flush_updates] no page")
            return

        print(
            f"[DEBUG _flush_updates] pending_cloud={len(self._pending_cloud_states)} pending_lane={len(self._pending_lane_updates)} last_stats={'YES' if self._last_statistics else 'NO'}")

        changed = False

        for data in self._pending_cloud_states:
            print(f"[DEBUG] cloud_state keys: {list(data.keys())}")
            self._apply_cloud_state(data)
            self.map.update_cloud_state(data)
            changed = True
        self._pending_cloud_states.clear()

        # Сливаем ВСЕ lane_update одного перекрёстка в ОДИН:
        # каждая камера несёт свои полосы, поэтому собираем их все,
        # чтобы в контейнере перекрёстка было столько карточек (показаний + индикаторов),
        # сколько реально полос (поведение "старой версии").
        merged_by_inter = {}
        for data in self._pending_lane_updates:
            inter_id = data.get("intersection_id", "unknown")
            if inter_id not in merged_by_inter:
                merged_by_inter[inter_id] = {
                    "type": "lane_update",
                    "intersection_id": inter_id,
                    "current_phase": data.get("current_phase", "UNKNOWN"),
                    "green_wave": data.get("green_wave"),
                    "lanes": [],
                }
            merged_by_inter[inter_id]["lanes"].extend(data.get("lanes", []))
            # Фаза/зелёная волна — берём актуальные (с последнего сообщения по этому перекрёстку)
            if data.get("current_phase"):
                merged_by_inter[inter_id]["current_phase"] = data.get("current_phase")
            if data.get("green_wave") is not None:
                merged_by_inter[inter_id]["green_wave"] = data.get("green_wave")

        self._pending_lane_updates.clear()

        for data in merged_by_inter.values():
            self._apply_lane_update(data)
            self.map.update_lane_update(data)
            changed = True

        # Обновляем статистику
        self._update_statistics()

        if changed:
            self.map.refresh()
            self.apply_filter()
        
        # Всегда обновляем страницу (статистика могла пропустить update)
        if self.page:
            self.page.update()

    def _update_statistics(self):
        """Обновить аналитику в UI.

        Приоритет: WebSocket-статистика (актуальна при активном окне).
        Fallback: прямой импорт traffic_stats (работает даже в фоне,
        т.к. бэкенд и UI в одном процессе при разработке).
        """
        stats = self._last_statistics

        # Fallback: если WebSocket не прислал статистику (окно в фоне / Flet заморозил loop),
        # читаем напрямую из бэкенда. traffic_stats — синглтон, наполняется оркестратором.
        if not stats:
            try:
                from backend.services.statistics import traffic_stats
                stats = traffic_stats.get_full_statistics()
            except Exception:
                stats = {}

        if not stats:
            if self.page:
                self.page.update()
            return

        summary = stats.get("network_summary", {})

        # --- Header метрики ---
        self.uptime_text.value = f"⏱ Uptime: {summary.get('uptime_display', '-')}"
        self.network_load_text.value = f"📊 Загрузка сети: {summary.get('network_avg_congestion', 0) * 100:.0f}%"
        self.total_switches_text.value = f"🔄 Переключений фаз: {summary.get('total_phase_switches', 0)}"
        self.emergency_count_text.value = f"🚨 Emergency: {summary.get('total_emergency_events', 0)}"
        self.anomaly_count_text.value = f"⚠️ Аномалий: {summary.get('intersections_with_anomalies', 0)}"
        self.gw_count_text.value = f"🟢 Зелёных волн: {summary.get('total_green_wave_events', 0)}"

        # --- Рейтинг загруженности ---
        self.ranking_container.controls.clear()
        ranking = stats.get("congestion_ranking", [])
        for i, r in enumerate(ranking[:10]):
            congestion_pct = r["avg_congestion"] * 100
            color = "red" if congestion_pct > 70 else ("orange" if congestion_pct > 40 else "green")
            trend_symbol = r.get("trend", "")
            trend_color = "red" if "rising" in trend_symbol else "green"

            rank_card = ft.Container(
                content=ft.Row([
                    ft.Text(f"#{i + 1}", size=14, weight=ft.FontWeight.BOLD, color="grey"),
                    ft.Text(f"🛑 {r['intersection_id']}", size=14, weight=ft.FontWeight.BOLD, expand=True),
                    ft.Text(f"{congestion_pct:.0f}%", size=14, weight=ft.FontWeight.BOLD, color=color),
                    ft.ProgressBar(value=r["avg_congestion"], width=100, color=color, bgcolor="#333"),
                    ft.Text(f"🚗 {r['total_cars']}", size=11, color="grey"),
                    ft.Text(f"{trend_symbol}", size=14, color=trend_color),
                    ft.Text(f"🔄 {r['phase_switches']}", size=11, color="grey"),
                ], spacing=8),
                bgcolor="#2a2a3e", padding=8, border_radius=6,
            )
            self.ranking_container.controls.append(rank_card)

        # --- График загрузки ---
        self._draw_congestion_chart(stats)

        # --- Тренды ---
        self.trend_container.controls.clear()
        for r in ranking:
            slope = r.get("trend_slope", 0)
            direction = r.get("trend", "→ stable")
            trend_card = ft.Container(
                content=ft.Row([
                    ft.Text(f"🛑 {r['intersection_id']}", size=13, weight=ft.FontWeight.BOLD, expand=True),
                    ft.Text(direction, size=13, color="cyan"),
                    ft.Text(f"({slope:+.5f}/s)", size=11, color="grey"),
                ]),
                bgcolor="#2a2a3e", padding=6, border_radius=4,
            )
            self.trend_container.controls.append(trend_card)

        # --- Emergency лог ---
        self.emergency_log_container.controls.clear()
        for ev in stats.get("emergency_log", []):
            ev_time = time.strftime("%H:%M:%S", time.localtime(ev["timestamp"]))
            cascade_str = f" → {len(ev.get('cascade', []))} upstream" if ev.get("cascade") else ""
            ev_card = ft.Container(
                content=ft.Row([
                    ft.Text(f"🚨", size=16),
                    ft.Text(f"[{ev_time}]", size=11, color="grey"),
                    ft.Text(f"{ev['intersection_id']}/{ev['approach']}", size=13, weight=ft.FontWeight.BOLD,
                            expand=True),
                    ft.Text(f"phase: {ev['phase']}", size=12, color="yellow"),
                    ft.Text(f"{ev['duration']:.1f}s", size=12, color="red"),
                    ft.Text(cascade_str, size=11, color="orange"),
                ], spacing=6),
                bgcolor="#2a1a1a", padding=6, border_radius=4,
            )
            self.emergency_log_container.controls.append(ev_card)
        if not stats.get("emergency_log"):
            self.emergency_log_container.controls.append(
                ft.Text("Нет emergency-событий", size=12, color="grey", italic=True)
            )

        # --- Green Wave лог ---
        self.green_wave_log_container.controls.clear()
        for gw in stats.get("green_wave_log", []):
            gw_time = time.strftime("%H:%M:%S", time.localtime(gw["timestamp"]))
            corridor_str = " → ".join(gw.get("corridor", []))
            gw_card = ft.Container(
                content=ft.Row([
                    ft.Text(f"🟢", size=16),
                    ft.Text(f"[{gw_time}]", size=11, color="grey"),
                    ft.Text(f"{corridor_str}", size=13, weight=ft.FontWeight.BOLD, expand=True),
                    ft.Text(f"phase: {gw['phase']}", size=12, color="yellow"),
                    ft.Text(f"{gw['duration']:.1f}s", size=12, color="green"),
                ], spacing=6),
                bgcolor="#1a2a1a", padding=6, border_radius=4,
            )
            self.green_wave_log_container.controls.append(gw_card)
        if not stats.get("green_wave_log"):
            self.green_wave_log_container.controls.append(
                ft.Text("Нет зелёных волн", size=12, color="grey", italic=True)
            )

        # --- Аномалии ---
        self.anomaly_log_container.controls.clear()
        for an in stats.get("anomaly_log", []):
            an_time = time.strftime("%H:%M:%S", time.localtime(an["timestamp"]))
            severity_icon = "🔴" if an["severity"] == "critical" else "⚠️"
            sev_color = "red" if an["severity"] == "critical" else "orange"
            an_card = ft.Container(
                content=ft.Row([
                    ft.Text(severity_icon, size=14),
                    ft.Text(f"[{an_time}]", size=11, color="grey"),
                    ft.Text(f"{an['intersection_id']}", size=13, weight=ft.FontWeight.BOLD, expand=True),
                    ft.Text(an['message'], size=12, color=sev_color),
                ], spacing=6),
                bgcolor="#2a1a1a" if an["severity"] == "critical" else "#2a2a1a",
                padding=6, border_radius=4,
            )
            self.anomaly_log_container.controls.append(an_card)
        if not stats.get("anomaly_log"):
            self.anomaly_log_container.controls.append(
                ft.Text("Нет аномалий", size=12, color="grey", italic=True)
            )

        if self.page:
            self.page.update()

    def _draw_congestion_chart(self, stats: dict):
        """Гистограмма congestion через обычные ProgressBar (совместимо с любой версией Flet)."""
        ranking = stats.get("congestion_ranking", [])

        chart_controls = []
        chart_controls.append(
            ft.Text("📈 Congestion time series (5 мин)", size=16, weight=ft.FontWeight.BOLD)
        )

        if not ranking:
            chart_controls.append(ft.Text("Нет данных", color="grey", italic=True))
            self.congestion_canvas_container.content = ft.Column(chart_controls, spacing=8)
            return

        # Топ-8 перекрёстков
        top = ranking[:8]
        for r in top:
            congestion_pct = r["avg_congestion"] * 100
            color = "red" if congestion_pct > 70 else ("orange" if congestion_pct > 40 else "green")
            short_name = r['intersection_id'].replace("intersection_", "i")

            bar_row = ft.Row([
                ft.Text(short_name, size=11, width=60, color="#aaa"),
                ft.ProgressBar(
                    value=r["avg_congestion"],
                    width=400,
                    color=color,
                    bgcolor="#333",
                ),
                ft.Text(f"{congestion_pct:.0f}%", size=11, color=color, width=50),
            ], spacing=8)
            chart_controls.append(bar_row)

        self.congestion_canvas_container.content = ft.Column(chart_controls, spacing=6)

    async def connect_to_backend(self):
        uri = "ws://127.0.0.1:8050/ws/monitor"
        while True:
            try:
                async with websockets.connect(uri) as websocket:
                    self.status_text.value = "СЕТЬ UTC АКТИВНА"
                    self.status_text.color = "green"
                    if self.page:
                        self.page.update()

                    async for message in websocket:
                        try:
                            data = json.loads(message)
                            msg_type = data.get("type", "")
                            if msg_type == "cloud_state":
                                self._pending_cloud_states.append(data)
                            elif msg_type == "batch_lane_update":
                                # Обрабатываем batch-обновление: разворачиваем cameras в отдельные lane_update
                                for cam_data in data.get("cameras", []):
                                    lane_update = {
                                        "type": "lane_update",
                                        "intersection_id": data.get("intersection_id"),
                                        "lane_id": cam_data.get("lane_id"),
                                        "command": cam_data.get("command"),
                                        "current_phase": cam_data.get("current_phase"),
                                        "green_duration": cam_data.get("green_duration"),
                                        "phase_elapsed": cam_data.get("phase_elapsed"),
                                        "lanes": cam_data.get("lanes", []),
                                        "green_wave": cam_data.get("green_wave"),
                                    }
                                    self._pending_lane_updates.append(lane_update)
                            elif msg_type == "lane_update":
                                self._pending_lane_updates.append(data)
                        except Exception as e:
                            if self.page and self.page.update:
                                pass  # Игнорируем битые сообщения

            except Exception:
                self.status_text.value = "ПОТЕРЯ СВЯЗИ С UTC БЭКЕНДОМ..."
                self.status_text.color = "red"
                if self.page:
                    self.page.update()
                await asyncio.sleep(2)

    def _apply_cloud_state(self, data: dict):
        print(f"[DEBUG _apply_cloud_state] has statistics: {'statistics' in data}")
        if "statistics" in data:
            print(f"[DEBUG] statistics type: {type(data['statistics'])}, keys: {list(data['statistics'].keys()) if isinstance(data['statistics'], dict) else 'N/A'}")
            self._last_statistics = data["statistics"]
        self.total_cars_text.value = f"Машин в сети: {data.get('total_cars_on_network', 0)}"
        commands = data.get("cascade_commands", [])
        self.cascade_text.value = f"Каскадных команд: {len(commands)}"
        green_wave = data.get("green_wave_active", False)
        self.green_wave_text.value = f"Зелёная волна: {'✅' if green_wave else '❌'}"
        self.green_wave_text.color = "green" if green_wave else "red"

        for inter_id, summary in data.get("intersections_summary", {}).items():
            congestion_pct = int(summary.get("avg_congestion", 0) * 100)
            lanes_count = summary.get("total_lanes", 0)
            self._inter_congestion[inter_id] = f"{congestion_pct}% | Полос: {lanes_count}"
            self._refresh_header(inter_id)
        
        # Сохраняем полную статистику из бэкенда (там же наполняются time series и т.д.)
        if "statistics" in data:
            self._last_statistics = data["statistics"]

    def _apply_lane_update(self, data: dict):
        inter_id = data.get("intersection_id", "unknown")
        current_phase = data.get("current_phase", "UNKNOWN")
        lanes = data.get("lanes", [])

        self._inter_phase[inter_id] = current_phase

        if inter_id not in self.intersection_containers:
            self.intersection_headers[inter_id] = ft.Text(
                f"🛑 Перекрёсток: {inter_id.upper()} | Фаза: {current_phase}",
                size=18, color="blue", weight=ft.FontWeight.BOLD,
            )
            lanes_layout = ft.Column(spacing=15)
            self.intersection_lanes_layout[inter_id] = lanes_layout

            inter_container = ft.Container(
                content=ft.Column([
                    self.intersection_headers[inter_id],
                    ft.Divider(),
                    lanes_layout,
                ]),
                bgcolor="#23272A", padding=20, border_radius=12,
                border=ft.border.Border(
                    top=ft.border.BorderSide(1, "blue"),
                    bottom=ft.border.BorderSide(1, "blue"),
                    left=ft.border.BorderSide(1, "blue"),
                    right=ft.border.BorderSide(1, "blue"),
                ),
            )
            self.intersection_containers[inter_id] = inter_container
            self.grid.controls.append(inter_container)

            current_val = self.filter_dropdown.value
            self.filter_dropdown.options.append(ft.dropdown.Option(inter_id))
            self.filter_dropdown.value = current_val
        else:
            self._refresh_header(inter_id)

        cards_to_rebuild = False
        for lane in lanes:
            # Нормализуем lane_id: убираем префикс "lane_" для отображения
            raw_lane_id = lane["lane_id"]
            display_lane_id = raw_lane_id.replace("lane_", "") if raw_lane_id.startswith("lane_") else raw_lane_id
            global_key = f"{inter_id}_{raw_lane_id}"  # Для кэша используем полный ID

            car_count = lane.get("car_count", 0)
            light_cmd = lane.get("light", "RED")
            phase_name = lane.get("phase_name", "UNKNOWN")
            # max_capacity — максимум машин, когда-либо увиденный камерой.
            # Он и есть 100% загрузки (механика: сколько видит камера — то и 100%).
            max_capacity = lane.get("max_capacity", 1) or 1

            if global_key not in self.lane_cards:
                card, phase_ref, count_ref, load_ref, load_bar, light_ref = self._create_lane_card(inter_id, display_lane_id)
                self.lane_cards[global_key] = {
                    "inter_id": inter_id,
                    "card": card,
                    "phase_text": phase_ref,
                    "count_text": count_ref,
                    "load_text": load_ref,
                    "load_bar": load_bar,
                    "light": light_ref,
                }
                cards_to_rebuild = True

            # Загрузка = текущие машины / макс. когда-либо увиденные (0..1)
            load_ratio = min(1.0, car_count / max_capacity)

            card_data = self.lane_cards[global_key]
            card_data["phase_text"].value = f"Фаза: {phase_name}"
            card_data["count_text"].value = f"{car_count} маш."
            # Тот же знаменатель (max_capacity), что и у бара: "3/5"
            card_data["load_text"].value = f"{car_count}/{max_capacity}"
            card_data["load_bar"].value = load_ratio
            if load_ratio > 0.7:
                card_data["load_bar"].color = "red"
            elif load_ratio > 0.4:
                card_data["load_bar"].color = "orange"
            else:
                card_data["load_bar"].color = "green"
            card_data["light"].bgcolor = self._light_to_color(light_cmd)

        if cards_to_rebuild:
            # Каждая дорога — отдельная строка (для 4 камер будет 4 строки,
            # 4 кружка и 4 шкалы заполненности).
            lanes_layout = self.intersection_lanes_layout[inter_id]
            lanes_layout.controls.clear()
            belonging_cards = [info["card"] for info in self.lane_cards.values() if info["inter_id"] == inter_id]
            for card in belonging_cards:
                lanes_layout.controls.append(card)

    def on_filter_change(self, e):
        self.filter_dropdown.value = e.control.value
        self.apply_filter()

    def apply_filter(self):
        selected_filter = self.filter_dropdown.value or "Все перекрёстки"
        for inter_id, container in self.intersection_containers.items():
            visible = (
                selected_filter == "Все перекрёстки"
                or selected_filter.strip().lower() == inter_id.strip().lower()
            )
            container.visible = visible
        if self.page:
            self.page.update()


if __name__ == "__main__":
    ui_factory = TrafficUIFactory()
    ft.run(ui_factory.build_ui)