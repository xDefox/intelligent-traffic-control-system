import math

import flet as ft
import flet.canvas as cv

# ============ КОНФИГ КАРТЫ ============
CV_WIDTH = 760
CV_HEIGHT = 520
NODE_R = 14          # базовый радиус узла
NODE_R_MAX = 30      # радиус при максимальной загрузке
MARGIN = 90

# Цвета состояний
COLOR_FREE = "#44ff44"
COLOR_MED = "#ffaa00"
COLOR_JAM = "#ff4444"
COLOR_LINK = "#666666"
COLOR_REDUCE = "#ff8800"   # каскад metering (урезаем влив)
COLOR_WAVE = "#33ccff"     # зелёная волна
COLOR_EMERGENCY = "#ff2222"


def _congestion_color(c: float) -> str:
    if c > 0.7:
        return COLOR_JAM
    if c > 0.4:
        return COLOR_MED
    return COLOR_FREE


class TrafficMap:
    """Динамическая карта состояния дорожной сети.

    Узлы — перекрёстки (цвет/размер по загруженности), связи — дороги,
    стрелки — активные каскадные команды (metering / зелёная волна /
    emergency). Строится ДИНАМИЧЕСКИ из телеметрии (без хардкода).
    Клик по узлу отдаёт его id в `on_node_click`.
    """

    def __init__(self, on_node_click=None):
        self.on_node_click = on_node_click
        self.canvas = cv.Canvas(width=CV_WIDTH, height=CV_HEIGHT)

        self._nodes = {}          # {inter_id: (px, py)}
        self._links = []          # [(src, dst, x1, y1, x2, y2)]
        self._road_count = {}
        self._known_intersections = None

        # Живые данные
        self._congestion = {}     # {inter_id: avg_congestion 0..1}
        self._cars = {}           # {inter_id: total_cars}
        self._phases = {}         # {inter_id: phase}
        self._cascade_commands = []
        self._green_wave_active = False

        self._label_controls = []
        self._click_controls = []

        self.container = ft.Container(alignment=ft.Alignment.CENTER)
        self._build()
        self.container.content = ft.Column(
            [self._legend(), self._stack()], spacing=8
        )

    # ============ СБОРКА ============

    def _build(self):
        self._sync_topology()
        self._redraw()

    def _stack(self) -> ft.Stack:
        return ft.Stack(
            width=CV_WIDTH,
            height=CV_HEIGHT,
            controls=[self.canvas] + self._label_controls + self._click_controls,
        )

    def _legend(self) -> ft.Control:
        def item(color: str, text: str, shape: str = "dot"):
            marker = ft.Container(
                width=14 if shape == "dot" else 22,
                height=14 if shape == "dot" else 4,
                border_radius=7,
                bgcolor=color,
            )
            return ft.Row([marker, ft.Text(text, size=11, color="#bbb")], spacing=5)

        return ft.Container(
            content=ft.Row(
                [
                    ft.Text("🗺 Карта состояния сети", size=18, weight=ft.FontWeight.BOLD),
                    ft.Container(width=20),
                    item(COLOR_FREE, "свободно"),
                    item(COLOR_MED, "средне"),
                    item(COLOR_JAM, "загружено"),
                    ft.Container(width=12),
                    item(COLOR_REDUCE, "каскад: режем влив", "line"),
                    item(COLOR_WAVE, "зелёная волна", "line"),
                    item(COLOR_EMERGENCY, "emergency", "dot"),
                ],
                spacing=10, wrap=True,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            bgcolor="#1e1e2e", border_radius=10,
        )

    def _sync_topology(self):
        """Пересобрать геометрию, если изменился набор перекрёстков/дорог."""
        from backend.services.graph_manager import traffic_network

        topo = traffic_network.get_topology_for_ui()
        intersections = topo["intersections"]
        links = topo["links"]

        current_ids = frozenset(intersections.keys())
        if current_ids == self._known_intersections and self._nodes:
            return
        self._known_intersections = current_ids
        self._build_geometry(intersections, links)

    def _build_geometry(self, intersections: dict, links: list):
        self._nodes = {}
        self._links = []
        self._road_count = {}
        self._label_controls = []
        self._click_controls = []

        if not intersections:
            return

        xs = [p["position"].get("x", 0) for p in intersections.values()]
        zs = [p["position"].get("z", 0) for p in intersections.values()]
        min_x, max_x = min(xs), max(xs)
        min_z, max_z = min(zs), max(zs)
        range_x = (max_x - min_x) or 1
        range_z = (max_z - min_z) or 1

        for inter_id, info in intersections.items():
            pos = info["position"]
            px = MARGIN + (pos.get("x", 0) - min_x) / range_x * (CV_WIDTH - 2 * MARGIN)
            py = MARGIN + (pos.get("z", 0) - min_z) / range_z * (CV_HEIGHT - 2 * MARGIN)
            self._nodes[inter_id] = (px, py)
            self._road_count[inter_id] = info.get("num_roads", 0)

        for src, dst in links:
            if src in self._nodes and dst in self._nodes:
                x1, y1 = self._nodes[src]
                x2, y2 = self._nodes[dst]
                self._links.append((src, dst, x1, y1, x2, y2))

        # Кликабельные оверлеи и подписи (в Stack поверх canvas).
        for inter_id, (px, py) in self._nodes.items():
            roads = self._road_count.get(inter_id, 0)
            label = ft.Text(
                f"{inter_id}\n({roads} дор.)",
                color="white", size=10, weight=ft.FontWeight.BOLD,
                text_align=ft.TextAlign.CENTER,
            )
            self._label_controls.append(
                ft.Container(content=label, left=px - 60, top=py - NODE_R - 44,
                             width=120, height=36)
            )
            self._click_controls.append(
                ft.GestureDetector(
                    on_tap=lambda e, iid=inter_id: self._handle_click(iid),
                    content=ft.Container(
                        left=px - NODE_R_MAX, top=py - NODE_R_MAX,
                        width=2 * NODE_R_MAX, height=2 * NODE_R_MAX,
                        bgcolor=ft.Colors.TRANSPARENT,
                        tooltip=f"{inter_id} — нажмите для деталей",
                    ),
                )
            )

    def _handle_click(self, inter_id: str):
        if self.on_node_click:
            self.on_node_click(inter_id)

    # ============ ОТРИСОВКА ============

    def _node_radius(self, inter_id: str) -> float:
        c = min(1.0, max(0.0, self._congestion.get(inter_id, 0.0)))
        return NODE_R + (NODE_R_MAX - NODE_R) * c

    def _redraw(self):
        shapes = [cv.Circle(x=CV_WIDTH // 2, y=CV_HEIGHT // 2, radius=2000,
                            paint=ft.Paint(color="#141420", style=ft.PaintingStyle.FILL))]

        # Связи: цвет — по средней загруженности концов.
        for src, dst, x1, y1, x2, y2 in self._links:
            src_c = self._congestion.get(src, 0.0)
            dst_c = self._congestion.get(dst, 0.0)
            color = _congestion_color((src_c + dst_c) / 2) if (src_c or dst_c) else COLOR_LINK
            shapes.append(cv.Line(x1, y1, x2, y2, paint=ft.Paint(
                color=color, stroke_width=4, style=ft.PaintingStyle.STROKE)))

        # Стрелки каскада/волны/emergency (поверх связей, под узлами).
        shapes.extend(self._cascade_shapes())

        # Узлы.
        for inter_id, (px, py) in self._nodes.items():
            r = self._node_radius(inter_id)
            shapes.append(cv.Circle(x=px, y=py, radius=r + 3,
                                    paint=ft.Paint(color="#000000", style=ft.PaintingStyle.FILL)))
            shapes.append(cv.Circle(x=px, y=py, radius=r, paint=ft.Paint(
                color=_congestion_color(self._congestion.get(inter_id, 0.0)),
                style=ft.PaintingStyle.FILL)))
            shapes.append(cv.Circle(x=px, y=py, radius=r, paint=ft.Paint(
                color="white", stroke_width=2, style=ft.PaintingStyle.STROKE)))
            if self._emergency_on(inter_id):
                shapes.append(cv.Circle(x=px, y=py, radius=r + 8, paint=ft.Paint(
                    color=COLOR_EMERGENCY, stroke_width=3, style=ft.PaintingStyle.STROKE)))

        self.canvas.shapes = shapes

    def _emergency_on(self, inter_id: str) -> bool:
        return any(
            c.get("action") == "EMERGENCY_GREEN" and c.get("target_intersection") == inter_id
            for c in self._cascade_commands
        )

    def _cascade_shapes(self) -> list:
        shapes = []
        for cmd in self._cascade_commands:
            action = cmd.get("action")
            if action == "REDUCE_GREEN":
                src = cmd.get("target_intersection")
                dst = cmd.get("trigger_intersection")
                if src in self._nodes and dst in self._nodes:
                    shapes.extend(self._arrow(*self._nodes[src], *self._nodes[dst], COLOR_REDUCE))
            elif action in ("GREEN_WAVE", "GREEN_WAVE_SYNC"):
                corridor = cmd.get("corridor") or []
                if len(corridor) >= 2:
                    pairs = list(zip(corridor, corridor[1:]))
                else:
                    src = cmd.get("target_intersection")
                    dst = cmd.get("trigger_intersection")
                    pairs = [(src, dst)] if src and dst else []
                for a, b in pairs:
                    if a in self._nodes and b in self._nodes:
                        shapes.extend(self._arrow(*self._nodes[a], *self._nodes[b], COLOR_WAVE))
        return shapes

    def _arrow(self, x1, y1, x2, y2, color: str) -> list:
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        sx, sy = x1 + ux * (NODE_R + 4), y1 + uy * (NODE_R + 4)
        ex, ey = x2 - ux * (NODE_R + 8), y2 - uy * (NODE_R + 8)

        head = 10.0
        px, py = -uy, ux  # перпендикуляр
        tip = (ex, ey)
        left = (ex - ux * head + px * head * 0.6, ey - uy * head + py * head * 0.6)
        right = (ex - ux * head - px * head * 0.6, ey - uy * head - py * head * 0.6)

        return [
            cv.Line(sx, sy, ex - ux * head * 0.5, ey - uy * head * 0.5,
                    paint=ft.Paint(color=color, stroke_width=3, style=ft.PaintingStyle.STROKE)),
            cv.Path(
                elements=[cv.Path.MoveTo(*tip), cv.Path.LineTo(*left),
                          cv.Path.LineTo(*right), cv.Path.Close()],
                paint=ft.Paint(color=color, style=ft.PaintingStyle.FILL),
            ),
        ]

    # ============ ОБНОВЛЕНИЕ ДАННЫХ ============

    def refresh(self):
        self._sync_topology()
        self._redraw()

    def update_lane_update(self, data: dict):
        inter_id = data.get("intersection_id", "")
        phase = data.get("current_phase", "")
        if inter_id and phase:
            self._phases[inter_id] = phase

    def update_cloud_state(self, data: dict):
        for inter_id, summary in data.get("intersections_summary", {}).items():
            self._congestion[inter_id] = summary.get("avg_congestion", 0.0)
            self._cars[inter_id] = summary.get("total_cars", 0)
        self._cascade_commands = data.get("cascade_commands", []) or []
        self._green_wave_active = data.get("green_wave_active", False)
