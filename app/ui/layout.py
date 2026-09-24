"""Estructura comun de todas las paginas: menu lateral, encabezado, estilos globales
y el contenedor de contenido. Es el unico lugar donde se definen colores y tipografia
de la interfaz; los graficos usan los mismos valores (ver backtest_charts)."""

import datetime as dt
import logging
from contextlib import contextmanager

from nicegui import ui

from app.config import settings
from app.live.queries import load_bot_status
from app.timeutil import fmt, label as tz_label

logger = logging.getLogger(__name__)

PRIMARY = "#2a78d6"
POSITIVE = "#0b7a3b"
NEGATIVE = "#d03b3b"
WARNING = "#b7791f"

NAV = [
    ("/", "Resumen", "space_dashboard"),
    ("/operaciones", "Operaciones", "receipt_long"),
    ("/estrategias", "Estrategias", "smart_toy"),
    ("/backtesting", "Backtesting", "science"),
    ("/configuracion", "Configuración", "tune"),
    ("/ayuda", "Ayuda", "menu_book"),
]

_CSS = """
:root {
  --ink: #14161a; --ink-2: #4b5160; --muted: #7b8190; --line: #e3e6ec; --surface: #ffffff; --page: #f4f5f8;
}
body, .q-app, .q-layout { font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif !important; color: var(--ink); }
body { background: var(--page) !important; -webkit-font-smoothing: antialiased; }
.q-btn { text-transform: none !important; letter-spacing: 0 !important; font-weight: 500; border-radius: 8px; }
.q-card { border-radius: 12px !important; box-shadow: none !important; }
.q-card--bordered { border: 1px solid var(--line) !important; background: var(--surface); }
.q-field--outlined .q-field__control { border-radius: 8px; }
.q-tabs { border-bottom: 1px solid var(--line); }
.q-tab { text-transform: none !important; font-weight: 500; letter-spacing: 0; }
.q-tab--active { color: #2a78d6; }
.q-table { font-size: 13px; }
.q-table thead tr th { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); font-weight: 600; background: #fafbfc; }
.q-table tbody td { color: var(--ink); }
.q-table tbody tr:hover td { background: #f6f8fc; }
.q-table__card { background: transparent !important; }
.q-drawer { background: var(--surface) !important; }
.q-expansion-item .q-item { border-radius: 8px; }
.q-badge { font-weight: 600; }
.num { font-variant-numeric: tabular-nums; }
.nav-link { display:flex; align-items:center; gap:12px; padding:9px 12px; border-radius:8px; color:var(--ink-2); text-decoration:none !important; font-weight:500; font-size:14px; }
.nav-link:hover { background:#f1f3f7; color:var(--ink); }
.nav-link.active { background:#e8f0fc; color:#1f5fb4; }
.pill { display:inline-flex; align-items:center; gap:6px; padding:3px 10px; border-radius:999px; font-size:12px; font-weight:600; }
.pill .dot { width:7px; height:7px; border-radius:50%; background:currentColor; }
.pill-good { background:#e6f4ec; color:#0b7a3b; }
.pill-warn { background:#fdf3e1; color:#8a5a0b; }
.pill-bad  { background:#fbe9e9; color:#b02a2a; }
.pill-idle { background:#eef0f4; color:#5a6070; }
.side-long { color:#0b7a3b; font-weight:600; }
.side-short { color:#b02a2a; font-weight:600; }
@media (max-width: 640px) { .q-table { font-size: 12px; } }
.manual { font-size: 15px; line-height: 1.7; color: var(--ink); max-width: 100%; }
.manual h1 { font-size: 28px; font-weight: 700; letter-spacing: -.01em; margin: 0 0 12px; }
.manual h2 { font-size: 21px; font-weight: 650; margin: 36px 0 10px; padding-top: 18px; border-top: 1px solid var(--line); }
.manual h3 { font-size: 17px; font-weight: 650; margin: 24px 0 8px; }
.manual p { margin: 8px 0; }
.manual ul, .manual ol { margin: 8px 0 8px 22px; }
.manual li { margin: 3px 0; }
.manual code { background: #eef1f6; padding: 1px 6px; border-radius: 5px; font-size: 13px; }
.manual pre { background: #14161a; color: #e8eaf0; padding: 14px 16px; border-radius: 10px; overflow-x: auto; margin: 12px 0; }
.manual pre code { background: transparent; color: inherit; padding: 0; }
.manual blockquote { margin: 14px 0; padding: 10px 16px; background: #fff7e6; border-left: 4px solid #d9a441; border-radius: 6px; color: #5b4310; }
.manual blockquote p { margin: 4px 0; }
.manual table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 14px; display: block; overflow-x: auto; }
.manual th { background: #f3f5f9; text-align: left; font-weight: 600; }
.manual th, .manual td { border: 1px solid var(--line); padding: 8px 12px; vertical-align: top; }
.manual tr:nth-child(even) td { background: #fafbfd; }
.manual hr { border: 0; border-top: 1px solid var(--line); margin: 24px 0; }
"""

ui.add_head_html(
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">'
    f"<style>{_CSS}</style>",
    shared=True,
)


def pill(text: str, kind: str = "idle", icon_dot: bool = True) -> ui.html:
    """Etiqueta de estado (good | warn | bad | idle)."""
    dot = '<span class="dot"></span>' if icon_dot else ""
    return ui.html(f'<span class="pill pill-{kind}">{dot}{text}</span>', sanitize=False)


def render_nav(active_path: str) -> None:
    demo = settings.bybit_demo
    ui.colors(primary=PRIMARY, secondary="#5b6b8c", accent="#7a5af8", positive=POSITIVE, negative=NEGATIVE, warning=WARNING, info="#2a78d6")

    with ui.left_drawer(top_corner=True, bottom_corner=True).props("show-if-above width=236 breakpoint=1024 bordered").classes("p-0") as drawer:
        with ui.column().classes("h-full w-full justify-between no-wrap p-4 gap-6"):
            with ui.column().classes("w-full gap-6"):
                with ui.row().classes("items-center gap-3 px-1 no-wrap"):
                    with ui.element("div").classes("w-9 h-9 rounded-lg flex items-center justify-center").style(f"background:{PRIMARY}"):
                        ui.icon("candlestick_chart", size="22px").classes("text-white")
                    with ui.column().classes("gap-0"):
                        ui.label("Bot Trading").classes("font-semibold text-[15px] leading-tight")
                        ui.label("Bybit Futures").classes("text-xs text-gray-500 leading-tight")
                with ui.column().classes("w-full gap-1"):
                    for path, label, icon in NAV:
                        with ui.link(target=path).classes("nav-link" + (" active" if path == active_path else "")):
                            ui.icon(icon, size="20px")
                            ui.label(label)

            with ui.column().classes("w-full gap-2 rounded-xl p-3").style("background:#f6f7fa"):
                env = ui.html(
                    f'<span class="pill pill-{"warn" if demo else "bad"}"><span class="dot"></span>'
                    f'{"Demo Trading" if demo else "MAINNET · dinero real"}</span>',
                    sanitize=False,
                )
                status_label = ui.label("").classes("text-xs text-gray-600")
                clock_label = ui.label("").classes("text-xs text-gray-500")
                kill_label = ui.label("").classes("text-xs font-semibold text-[#b02a2a]")

    async def update_status() -> None:
        try:
            status = await load_bot_status()
        except Exception:  # noqa: BLE001 - el menu no debe romper la pagina
            logger.exception("No se pudo leer el estado del bot")
            return
        status_label.text = f"{status['running']} de {status['total']} instancias corriendo"
        clock_label.text = f"{fmt(dt.datetime.now(dt.timezone.utc), '%H:%M')} · {tz_label()}"
        kill_label.text = "⛔ Kill-switch activado" if status["kill_switch"] else ""

    ui.timer(0.2, update_status, once=True)
    ui.timer(20, update_status)

    sample_mode = not settings.live_enabled
    if sample_mode:
        header_classes = "bg-[#5b3fb5] text-white items-center justify-center gap-2 h-10"
    elif demo:
        header_classes = "bg-white text-gray-900 border-b items-center gap-2 h-12 px-3 lg:hidden"
    else:
        header_classes = "bg-[#d03b3b] text-white items-center justify-center gap-2 h-10"
    with ui.header(elevated=False).classes(header_classes):
        if sample_mode:
            ui.button(icon="menu", on_click=drawer.toggle).props("flat dense round color=white").classes("lg:hidden absolute left-2")
            ui.icon("science", size="20px")
            ui.label("MODO DEMOSTRACIÓN: datos de ejemplo, no opera en el exchange").classes("font-semibold text-sm")
        elif demo:
            ui.button(icon="menu", on_click=drawer.toggle).props("flat dense round")
            ui.label("Bot Trading").classes("font-semibold")
        else:
            ui.button(icon="menu", on_click=drawer.toggle).props("flat dense round color=white").classes("lg:hidden absolute left-2")
            ui.icon("warning", size="20px")
            ui.label("MAINNET: las operaciones usan dinero real").classes("font-semibold text-sm")


@contextmanager
def page_content(title: str, subtitle: str | None = None, width: str = "max-w-6xl", actions=None):
    """Contenedor estandar de una pagina: titulo, subtitulo y el ancho maximo del contenido."""
    with ui.column().classes(f"w-full {width} mx-auto p-4 md:p-8 gap-6"):
        with ui.row().classes("w-full items-end justify-between gap-3"):
            with ui.column().classes("gap-1"):
                ui.label(title).classes("text-2xl font-semibold tracking-tight")
                if subtitle:
                    ui.label(subtitle).classes("text-sm text-gray-500 max-w-3xl")
            if actions:
                with ui.row().classes("items-center gap-2"):
                    actions()
        yield
