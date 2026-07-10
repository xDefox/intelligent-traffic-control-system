import flet as ft
import asyncio
import json
import websockets
import traceback  # Нужно для отладки внутренних скрытых ошибок UI


class TrafficUIFactory:
    def __init__(self):
        self.page = None
        self.lane_cards = {}  # Ключ: "interID_laneID"

        # Главная вертикальная колонка для размещения контейнеров перекрестков
        self.grid = ft.Column(spacing=20, scroll=ft.ScrollMode.AUTO, expand=True)

        self.status_text = ft.Text("ОЖИДАНИЕ СЕРВЕРА...", color="yellow", weight=ft.FontWeight.BOLD)

        # Хранилище монолитных контейнеров перекрестков
        self.intersection_containers = {}  # inter_id -> ft.Container
        self.intersection_headers = {}  # inter_id -> ft.Text
        self.intersection_lanes_layout = {}  # inter_id -> ft.Column (внутренний слой для карточек)

        # Выпадающий список для фильтрации
        self.filter_dropdown = ft.Dropdown(
            label="Фильтр перекрёстков",
            options=[ft.dropdown.Option("Все перекрёстки")],
            value="Все перекрёстки",
            width=300
        )
        self.filter_dropdown.on_change = self.on_filter_change

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
            self.filter_dropdown,
            ft.Divider(),
            self.grid
        )
        page.run_task(self.connect_to_backend)

    def _create_lane_card(self, inter_id: str, lane_id: str):
        count_text = ft.Text("Машин в очереди: 0", size=14)
        load_text = ft.Text("Нагрузка: 0%", size=14)
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
                load_text  # Скорость убрали, добавили процент нагрузки
            ]),
            bgcolor="surfacevariant",
            padding=12,
            border_radius=10,
            expand=True
        )
        return card, count_text, load_text, light_indicator

    async def connect_to_backend(self):
        uri = "ws://127.0.0.1:8050/ws/monitor"
        while True:
            try:
                async with websockets.connect(uri) as websocket:
                    self.status_text.value = "СЕТЬ UTC АКТИВНА"
                    self.status_text.color = "greenaccent"
                    if self.page:
                        self.page.update()

                    async for message in websocket:
                        data = json.loads(message)

                        # КРИТИЧЕСКИ ВАЖНО: Изолируем ошибки UI, чтобы сокет не падал
                        try:
                            self.update_ui_state(data)
                        except Exception as ui_err:
                            print("❌ Ошибка отрисовки внутри update_ui_state:")
                            traceback.print_exc()

            except Exception as net_err:
                print(f"📡 Ошибка сети Веб-сокета: {net_err}")
                self.status_text.value = "ПОТЕРЯ СВЯЗИ С UTC БЭКЕНДОМ..."
                self.status_text.color = "red"
                if self.page:
                    self.page.update()
                await asyncio.sleep(2)

    def on_filter_change(self, e):
        self.filter_dropdown.value = e.control.value
        self.apply_filter()

    def update_ui_state(self, data: dict):
        inter_id = data.get("intersection_id", "unknown")
        current_phase = data.get("current_phase", "UNKNOWN")
        lanes = data.get("lanes", [])

        structure_changed = False

        # 1. Если перекрёсток зашел впервые — собираем монолитный контейнер-блок
        if inter_id not in self.intersection_containers:
            self.intersection_headers[inter_id] = ft.Text(
                f"🛑 Перекрёсток: {inter_id.upper()} | Текущая фаза: {current_phase}",
                size=18, color="blueaccent", weight=ft.FontWeight.BOLD
            )

            lanes_layout = ft.Column(spacing=15)
            self.intersection_lanes_layout[inter_id] = lanes_layout

            inter_container = ft.Container(
                content=ft.Column([
                    self.intersection_headers[inter_id],
                    ft.Divider(),
                    lanes_layout
                ]),
                bgcolor="#23272A",
                padding=20,
                border_radius=12,
                border=ft.border.Border(
                    top=ft.border.BorderSide(1, "blueaccent"),
                    bottom=ft.border.BorderSide(1, "blueaccent"),
                    left=ft.border.BorderSide(1, "blueaccent"),
                    right=ft.border.BorderSide(1, "blueaccent")
                ),
            )

            self.intersection_containers[inter_id] = inter_container
            self.grid.controls.append(inter_container)
            structure_changed = True

            # Добавляем новый перекрёсток в выпадающий список (не сбрасывая выбор пользователя)
            current_val = self.filter_dropdown.value
            self.filter_dropdown.options.append(ft.dropdown.Option(inter_id))
            self.filter_dropdown.value = current_val
        else:
            self.intersection_headers[
                inter_id].value = f"🛑 Перекрёсток: {inter_id.upper()} | Текущая фаза: {current_phase}"

        # 2. Обрабатываем полосы внутри этого перекрёстка
        cards_to_rebuild = False
        for lane in lanes:
            lane_id = lane["lane_id"]
            global_key = f"{inter_id}_{lane_id}"

            # Расчёт процента нагрузки (условный лимит полосы — 15 машин)
            car_count = lane.get("car_count", 0)
            max_capacity = 15
            load_percentage = min(100, int((car_count / max_capacity) * 100))

            if global_key not in self.lane_cards:
                card, count_ref, load_ref, light_ref = self._create_lane_card(inter_id, lane_id)
                self.lane_cards[global_key] = {
                    "inter_id": inter_id,
                    "card": card,
                    "count_text": count_ref,
                    "load_text": load_ref,
                    "light": light_ref
                }
                cards_to_rebuild = True

            # Спокойно обновляем значения виджетов в памяти
            self.lane_cards[global_key]["count_text"].value = f"Машин в очереди: {car_count}"
            self.lane_cards[global_key]["load_text"].value = f"Нагрузка: {load_percentage}%"
            self.lane_cards[global_key]["light"].bgcolor = lane["light"]

        # 3. Если у перекрёстка появились новые полосы — перестраиваем внутренний слой контейнера
        if cards_to_rebuild:
            lanes_layout = self.intersection_lanes_layout[inter_id]
            lanes_layout.controls.clear()

            belonging_cards = [info["card"] for info in self.lane_cards.values() if info["inter_id"] == inter_id]

            # Пакуем полосы строго по 2 штуки в один горизонтальный ряд Row
            for i in range(0, len(belonging_cards), 2):
                lanes_layout.controls.append(ft.Row(belonging_cards[i:i + 2], spacing=15))
            structure_changed = True

        # 4. Применяем фильтр (без пересборки options — они обновляются только при появлении нового перекрёстка)
        self.apply_filter()

    def apply_filter(self):
        selected_filter = self.filter_dropdown.value
        if not selected_filter:
            selected_filter = "Все перекрёстки"

        # Включаем/выключаем видимость целых контейнеров перекрестков
        for inter_id, container in self.intersection_containers.items():
            if selected_filter == "Все перекрёстки" or selected_filter.strip().lower() == inter_id.strip().lower():
                container.visible = True
            else:
                container.visible = False

        if self.page:
            self.page.update()


if __name__ == "__main__":
    ui_factory = TrafficUIFactory()
    ft.run(ui_factory.build_ui)