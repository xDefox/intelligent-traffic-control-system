# backend/UI/admin_panel.py
import flet as ft
import asyncio
import json
import websockets


class TrafficUIFactory:
    def __init__(self):
        self.page = None
        self.lane_cards = {}  # {lane_id: {компоненты}}
        self.grid = ft.Column(spacing=15)
        self.total_cars_text = ft.Text("Всего машин в кадре: 0", size=16)
        self.status_text = ft.Text("ОЖИДАНИЕ СЕРВЕРА...", color="yellow", weight=ft.FontWeight.BOLD)
        self.phase_text = ft.Text("Текущая фаза: Ожидание данных", size=18, weight=ft.FontWeight.BOLD)

    def build_ui(self, page: ft.Page):
        self.page = page
        page.title = "UTC-UX Fusion - Мониторинг перекрестков"
        page.theme_mode = ft.ThemeMode.DARK
        page.padding = 20

        page.add(
            ft.Row([
                ft.Text("📊 Мониторинг систем UTC-UX", size=28, weight=ft.FontWeight.BOLD),
                self.status_text
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Divider(),

            ft.Row([
                ft.Card(content=ft.Container(content=self.total_cars_text, padding=15)),
            ]),

            ft.Card(content=ft.Container(content=self.phase_text, padding=15), bgcolor="surfacevariant"),
            ft.Text("Активные направления движения (Динамическая фабрика):", size=20, weight=ft.FontWeight.BOLD),

            self.grid
        )

        # Запускаем асинхронную задачу прослушивания сокета в контексте Flet
        page.run_task(self.connect_to_backend)

    def _create_lane_card(self, lane_id: str):
        """Фабрика: собирает UI-карточку для полосы на лету"""
        count_text = ft.Text("Машин в очереди: 0", size=14)
        speed_text = ft.Text("Скорость: 0.0 км/ч", size=14)
        light_indicator = ft.Container(width=20, height=20, border_radius=10, bgcolor="red")

        card = ft.Container(
            content=ft.Column([
                ft.Text(f"🛣️ {lane_id}", size=18, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                ft.Row([
                    ft.Text("Светофор:"),
                    light_indicator
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                count_text,
                speed_text
            ]),
            bgcolor="surfacevariant",
            padding=15,
            border_radius=10,
            expand=True
        )
        return card, count_text, speed_text, light_indicator

    async def connect_to_backend(self):
        """Асинхронный клиент веб-сокета"""
        uri = "ws://127.0.0.1:8050/ws/monitor"  # Порт твоего FastAPI (8050 судя по Unity)

        while True:
            try:
                async with websockets.connect(uri) as websocket:
                    self.status_text.value = "ПОДКЛЮЧЕНО К СЕРВЕРУ"
                    self.status_text.color = "greenaccent"
                    self.page.update()

                    async for message in websocket:
                        data = json.loads(message)
                        self.update_ui_state(data)
            except Exception:
                self.status_text.value = "ПОТЕРЯ СВЯЗИ. ПЕРЕПОДКЛЮЧЕНИЕ..."
                self.status_text.color = "red"
                self.page.update()
                await asyncio.sleep(2)  # Спим перед попыткой реконнекта

    def update_ui_state(self, data: dict):
        """Обновление интерфейса на основе честного JSON от сервера"""
        current_phase = data.get("current_phase", "UNKNOWN")
        inter_id = data.get("intersection_id", "unknown")

        self.phase_text.value = f"🤖 [ИИ РЕШЕНИЕ | {inter_id}]: Текущая фаза -> {current_phase}"

        lanes = data.get("lanes", [])
        total_cars = 0
        structure_changed = False

        for lane in lanes:
            lane_id = lane["lane_id"]
            cars = lane["car_count"]
            speed = lane["avg_speed"]
            light_color = lane["light"]

            total_cars += cars

            # Если прилетела новая полоса, которой не было — фабрика её собирает
            if lane_id not in self.lane_cards:
                card, count_ref, speed_ref, light_ref = self._create_lane_card(lane_id)
                self.lane_cards[lane_id] = {
                    "card": card,
                    "count_text": count_ref,
                    "speed_text": speed_ref,
                    "light": light_ref
                }
                structure_changed = True

            # Пишем реальные данные в компоненты
            self.lane_cards[lane_id]["count_text"].value = f"Машин в очереди: {cars}"
            self.lane_cards[lane_id]["speed_text"].value = f"Средняя скорость: {speed} км/ч"
            self.lane_cards[lane_id]["light"].bgcolor = light_color

        self.total_cars_text.value = f"Всего машин на перекрестке: {total_cars}"

        # Перестраиваем сетку, только если изменилось количество полос
        if structure_changed:
            self.grid.controls.clear()
            all_cards = [info["card"] for info in self.lane_cards.values()]
            for i in range(0, len(all_cards), 2):
                self.grid.controls.append(ft.Row(all_cards[i:i + 2], spacing=15))

        self.page.update()


if __name__ == "__main__":
    ui_factory = TrafficUIFactory()
    ft.run(ui_factory.build_ui)