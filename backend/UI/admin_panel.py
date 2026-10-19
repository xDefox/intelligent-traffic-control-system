import flet as ft

from backend.UI.ui.panels import TrafficUIFactory

if __name__ == "__main__":
    ui_factory = TrafficUIFactory()
    ft.run(ui_factory.build_ui)