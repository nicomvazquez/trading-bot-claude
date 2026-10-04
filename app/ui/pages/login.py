from nicegui import app as nicegui_app, ui

from app.auth import check_credentials, is_locked_out, register_failed_attempt, register_successful_login


@ui.page("/login")
def login_page() -> None:
    if nicegui_app.storage.user.get("authenticated", False):
        ui.navigate.to("/")
        return

    with ui.column().classes("w-full h-screen items-center justify-center gap-4"):
        with ui.card().classes("w-[380px] max-w-full p-6 gap-3"):
            with ui.row().classes("items-center gap-2 mb-1"):
                ui.icon("candlestick_chart", size="24px").classes("text-[#2a78d6]")
                ui.label("Bot Trading").classes("text-lg font-semibold")
            ui.label("Ingresá para ver y controlar el dashboard.").classes("text-sm text-gray-500 -mt-2")

            user = ui.input("Usuario").props("outlined dense").classes("w-full")
            password = ui.input("Contraseña", password=True, password_toggle_button=True).props(
                "outlined dense").classes("w-full")
            error_label = ui.label("").classes("text-sm text-red-600")

            async def try_login() -> None:
                locked, remaining = is_locked_out()
                if locked:
                    error_label.text = f"Demasiados intentos fallidos. Esperá {remaining // 60 + 1} minuto(s)."
                    return
                if check_credentials(user.value or "", password.value or ""):
                    register_successful_login()
                    nicegui_app.storage.user["authenticated"] = True
                    ui.navigate.to(nicegui_app.storage.user.pop("redirect_to", "/"))
                else:
                    await register_failed_attempt()
                    error_label.text = "Usuario o contraseña incorrectos."
                    password.value = ""

            password.on("keydown.enter", try_login)
            ui.button("Ingresar", icon="login", on_click=try_login).props("unelevated no-caps").classes("w-full mt-1")
