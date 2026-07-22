from backend.UI.ui.panels import TrafficUIFactory
import flet as ft

if __name__ == "__main__":
    ui_factory = TrafficUIFactory()
    ft.run(ui_factory.build_ui)