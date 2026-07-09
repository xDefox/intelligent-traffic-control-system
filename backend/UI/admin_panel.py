# admin_ui.py
import flet as ft
import asyncio
import json
import websockets


class TrafficUIFactory:
    def __init__(self):
        self.page = None
        self.lane_cards = {}  # Ключ теперь: "interID_laneID"
        self.grid = ft.Column(spacing=15)
        self.status_text = ft.Text("ОЖИДАНИЕ СЕРВЕРА...", color="yellow", weight=ft.FontWeight.BOLD)
        self.intersection_headers = {}  # Хранилище заголовков для блоков

    def build_ui(self, page: ft.Page):
        self.page = page
        page.title = "UTC-UX Network Fusion Engine"
        page.theme_mode = ft.ThemeMode.DARK
        page.padding = 20

        page.add(
            ft.Row([
                ft.Text("📊 Распределенный UTC-UX Мониторинг", size=28, weight=ft.FontWeight.BOLD),
                self.status_text
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Divider(),
            self.grid
        )
        page.run_task(self.connect_to_backend)

    def _create_lane_card(self, inter_id: str, lane_id: str):
        count_text = ft.Text("Машин в очереди: 0", size=14)
        speed_text = ft.Text("Скорость: 0.0 км/ч", size=14)
        light_indicator = ft.Container(width=20, height=20, border_radius=10, bgcolor="red")

        card = ft.Container(
            content=ft.Column([
                ft.Text(f"🛣️ {lane_id}", size=16, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                ft.Row([
                    ft.Text("Светофор:"),
                    light_indicator
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                count_text,
                speed_text
            ]),
            bgcolor="surfacevariant",
            padding=12,
            border_radius=10,
            expand=True
        )
        return card, count_text, speed_text, light_indicator

    async def connect_to_backend(self):
        uri = "ws://127.0.0.1:8050/ws/monitor"
        while True:
            try:
                async with websockets.connect(uri) as websocket:
                    self.status_text.value = "СЕТЬ UTC АКТИВНА"
                    self.status_text.color = "greenaccent"
                    self.page.update()

                    async for message in websocket:
                        data = json.loads(message)
                        self.update_ui_state(data)
            except Exception:
                self.status_text.value = "ПОТЕРЯ СВЯЗИ С UTC БЭКЕНДОМ..."
                self.status_text.color = "red"
                self.page.update()
                await asyncio.sleep(2)

    def update_ui_state(self, data: dict):
        inter_id = data.get("intersection_id", "unknown")
        current_phase = data.get("current_phase", "UNKNOWN")
        lanes = data.get("lanes", [])

        structure_changed = False

        # Если этот перекрёсток зашёл в сеть впервые — создаём для него заголовок
        if inter_id not in self.intersection_headers:
            self.intersection_headers[inter_id] = ft.Text(
                f"🛑 Перекрёсток: {inter_id.upper()} | Фаза: {current_phase}",
                size=18, color="blueaccent", weight=ft.FontWeight.BOLD
            )
            structure_changed = True
        else:
            self.intersection_headers[inter_id].value = f"🛑 Перекрёсток: {inter_id.upper()} | Фаза: {current_phase}"

        for lane in lanes:
            lane_id = lane["lane_id"]
            # Уникальный составной ключ
            global_key = f"{inter_id}_{lane_id}"

            if global_key not in self.lane_cards:
                card, count_ref, speed_ref, light_ref = self._create_lane_card(inter_id, lane_id)
                self.lane_cards[global_key] = {
                    "inter_id": inter_id,
                    "card": card,
                    "count_text": count_ref,
                    "speed_text": speed_ref,
                    "light": light_ref
                }
                structure_changed = True

            # Обновляем внутренности карточек
            self.lane_cards[global_key]["count_text"].value = f"Машин: {lane['car_count']}"
            self.lane_cards[global_key]["speed_text"].value = f"Скорость: {lane['avg_speed']} км/ч"
            self.lane_cards[global_key]["light"].bgcolor = lane["light"]

        # Если появились новые узлы или полосы — полностью перестраиваем дерево разметки
        if structure_changed:
            self.grid.controls.clear()

            # Группируем карточки по их перекрёсткам
            for id_inter, header_component in self.intersection_headers.items():
                self.grid.controls.append(header_component)

                # Собираем все карточки, принадлежащие текущему перекрёстку
                belonging_cards = [info["card"] for info in self.lane_cards.values() if info["inter_id"] == id_inter]

                # Пакуем по 2 штуки в ряд
                for i in range(0, len(belonging_cards), 2):
                    self.grid.controls.append(ft.Row(belonging_cards[i:i + 2], spacing=15))

                self.grid.controls.append(ft.Divider())

        self.page.update()


if __name__ == "__main__":
    ui_factory = TrafficUIFactory()
    ft.run(ui_factory.build_ui)