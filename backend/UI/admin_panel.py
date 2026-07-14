import flet as ft
import flet.canvas as cv
import asyncio
import json
import websockets
import traceback


# ============ КОНФИГ ГРАФА ============
CV_WIDTH = 700
CV_HEIGHT = 500
NODE_R = 14


class TrafficMap:
    """Граф дорожной сети: точки (перекрёстки) + линии (дороги между ними)."""

    def __init__(self):
        self.canvas = cv.Canvas(width=CV_WIDTH, height=CV_HEIGHT)
        self._labels = {}

        self._nodes = {}
        self._links = []
        self._phases = {}
        self._congestion = {}

        self._parse()
        self._build()

    def _parse(self):
        from backend.core.road_config import ROADS

        xs = [cfg.get("position", {}).get("x", 0) for iid, cfg in ROADS.items() if iid != "links"]
        zs = [cfg.get("position", {}).get("z", 0) for iid, cfg in ROADS.items() if iid != "links"]
        min_x, max_x = min(xs), max(xs) if xs else (0, 0)
        min_z, max_z = min(zs), max(zs) if zs else (0, 0)
        range_x = max_x - min_x if max_x != min_x else 1
        range_z = max_z - min_z if max_z != min_z else 1

        margin = 80
        for inter_id, config in ROADS.items():
            if inter_id == "links":
                continue
            pos = config.get("position", {"x": 0, "z": 0})
            px = margin + (pos["x"] - min_x) / range_x * (CV_WIDTH - 2 * margin)
            py = margin + (pos["z"] - min_z) / range_z * (CV_HEIGHT - 2 * margin)
            self._nodes[inter_id] = (px, py)

        for link_str in ROADS.get("links", []):
            parts = link_str.split("->")
            if len(parts) == 2:
                src_parts = parts[0].strip().split("_")
                dst_parts = parts[1].strip().split("_")
                src = "_".join(src_parts[1:3])
                dst = "_".join(dst_parts[1:3])
                if src in self._nodes and dst in self._nodes:
                    x1, y1 = self._nodes[src]
                    x2, y2 = self._nodes[dst]
                    self._links.append((src, dst, x1, y1, x2, y2))

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

    def _build(self):
        shapes = []

        shapes.append(cv.Circle(
            x=CV_WIDTH // 2, y=CV_HEIGHT // 2, radius=1000,
            paint=ft.Paint(color="#1a1a2e", style=ft.PaintingStyle.FILL),
        ))

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
        for inter_id, (px, py) in self._nodes.items():
            lbl = ft.Text(
                inter_id.replace("_", " ").title(),
                color="white", size=10, weight=ft.FontWeight.BOLD,
                text_align=ft.TextAlign.CENTER,
            )
            self._labels[inter_id] = lbl
            label_controls.append(
                ft.Container(
                    content=lbl,
                    left=px - 50,
                    top=py - NODE_R - 28,
                    width=100,
                    height=20,
                )
            )

        self.stack = ft.Stack(
            width=CV_WIDTH,
            height=CV_HEIGHT,
            controls=[self.canvas] + label_controls,
        )

    def refresh(self):
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

        # ===== Вкладка "Граф" =====
        graph_tab = ft.Container(
            alignment=ft.Alignment.TOP_LEFT,
            content=ft.Column([
                ft.Row([
                    ft.Text("🗺️ Граф дорожной сети", size=24, weight=ft.FontWeight.BOLD),
                    ft.Text("Дороги: зелёные = активная фаза, серые = неактивная | Круги: загрузка",
                            size=12, color="grey"),
                ]),
                ft.Divider(),
                ft.Container(content=self.map.stack, alignment=ft.Alignment.CENTER),
            ], scroll=ft.ScrollMode.AUTO, expand=True),
        )

        # ===== Табы =====
        self.tab_bar = ft.TabBar(
            tab_alignment=ft.TabAlignment.START,
            tabs=[
                ft.Tab(label=ft.Text("📋 Список")),
                ft.Tab(label=ft.Text("🗺️ Граф")),
            ],
        )

        self.tab_bar_view = ft.TabBarView(
            expand=True,
            controls=[list_tab, graph_tab],
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
        phase_text = ft.Text("Фаза: -", size=12, color="grey")
        count_text = ft.Text("Машин в очереди: 0", size=14)
        capacity_text = ft.Text("Вместимость: -", size=12, color="grey")
        load_bar = ft.ProgressBar(value=0.0, width=150, color="green", bgcolor="#333333")
        light_indicator = ft.Container(width=20, height=20, border_radius=10, bgcolor="red")

        card = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text(f"🛣️ {lane_id}", size=16, weight=ft.FontWeight.BOLD, expand=True),
                    phase_text,
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Divider(),
                ft.Row([ft.Text("Светофор:"), light_indicator], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                count_text,
                capacity_text,
                ft.Row([ft.Text("Загрузка:", size=12), load_bar], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ]),
            bgcolor="surfacevariant", padding=12, border_radius=10, expand=True,
        )
        return card, phase_text, count_text, capacity_text, load_bar, light_indicator

    @staticmethod
    def _light_to_color(command: str) -> str:
        return {"GREEN": "green", "RED": "red"}.get(command, "grey")

    async def _flush_loop(self):
        while True:
            await asyncio.sleep(1.0)
            try:
                self._flush_updates()
            except Exception:
                pass

    def _flush_updates(self):
        if not self.page:
            return
        changed = False

        for data in self._pending_cloud_states:
            self._apply_cloud_state(data)
            self.map.update_cloud_state(data)
            changed = True
        self._pending_cloud_states.clear()

        latest_by_inter = {}
        for data in self._pending_lane_updates:
            inter_id = data.get("intersection_id", "unknown")
            latest_by_inter[inter_id] = data
        self._pending_lane_updates.clear()

        for data in latest_by_inter.values():
            self._apply_lane_update(data)
            self.map.update_lane_update(data)
            changed = True

        if changed:
            self.map.refresh()
            self.apply_filter()

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
                        data = json.loads(message)
                        try:
                            msg_type = data.get("type", "")
                            if msg_type == "cloud_state":
                                self._pending_cloud_states.append(data)
                            else:
                                self._pending_lane_updates.append(data)
                        except Exception:
                            pass

            except Exception:
                self.status_text.value = "ПОТЕРЯ СВЯЗИ С UTC БЭКЕНДОМ..."
                self.status_text.color = "red"
                if self.page:
                    self.page.update()
                await asyncio.sleep(2)

    def _apply_cloud_state(self, data: dict):
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
            lane_id = lane["lane_id"]
            global_key = f"{inter_id}_{lane_id}"

            car_count = lane.get("car_count", 0)
            load_pct = lane.get("load_pct", 0)
            light_cmd = lane.get("light", "RED")
            phase_name = lane.get("phase_name", "UNKNOWN")
            max_capacity = lane.get("max_capacity", 10)

            if global_key not in self.lane_cards:
                card, phase_ref, count_ref, cap_ref, load_bar, light_ref = self._create_lane_card(inter_id, lane_id)
                self.lane_cards[global_key] = {
                    "inter_id": inter_id,
                    "card": card,
                    "phase_text": phase_ref,
                    "count_text": count_ref,
                    "capacity_text": cap_ref,
                    "load_bar": load_bar,
                    "light": light_ref,
                }
                cards_to_rebuild = True

            card_data = self.lane_cards[global_key]
            card_data["phase_text"].value = f"Фаза: {phase_name}"
            card_data["count_text"].value = f"Машин в очереди: {car_count}"
            card_data["capacity_text"].value = f"Вместимость: {max_capacity} машин"
            card_data["load_bar"].value = load_pct / 100.0
            if load_pct > 70:
                card_data["load_bar"].color = "red"
            elif load_pct > 40:
                card_data["load_bar"].color = "orange"
            else:
                card_data["load_bar"].color = "green"
            card_data["light"].bgcolor = self._light_to_color(light_cmd)

        if cards_to_rebuild:
            lanes_layout = self.intersection_lanes_layout[inter_id]
            lanes_layout.controls.clear()
            belonging_cards = [info["card"] for info in self.lane_cards.values() if info["inter_id"] == inter_id]
            for i in range(0, len(belonging_cards), 2):
                lanes_layout.controls.append(ft.Row(belonging_cards[i:i + 2], spacing=15))

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