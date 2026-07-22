import flet as ft
import flet.canvas as cv

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