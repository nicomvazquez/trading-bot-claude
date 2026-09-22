from nicegui import ui
from sqlalchemy import select

from app.db.base import async_session
from app.db.models import StrategyInstance
from app.live.orchestrator import orchestrator
from app.strategies import registry
from app.ui.layout import render_nav
from app.ui.param_form import render_param_form


async def _load_instances() -> list[StrategyInstance]:
    async with async_session() as session:
        result = await session.execute(select(StrategyInstance))
        return list(result.scalars().all())


async def _save_instance(
    strategy_key: str,
    name: str,
    symbol: str,
    timeframe: str,
    initial_capital: float,
    params: dict,
) -> None:
    async with async_session() as session:
        session.add(
            StrategyInstance(
                name=name,
                strategy_key=strategy_key,
                symbol=symbol,
                timeframe=timeframe,
                params=params,
                initial_capital=initial_capital,
                is_active=False,
            )
        )
        await session.commit()


async def _toggle_active(instance_id: int, is_active: bool) -> None:
    async with async_session() as session:
        instance = await session.get(StrategyInstance, instance_id)
        if instance:
            instance.is_active = is_active
            await session.commit()
    if is_active:
        orchestrator.activate(instance_id)
    else:
        await orchestrator.deactivate(instance_id)


def _open_create_dialog(strategy_key: str, refresh) -> None:
    strategy_cls = registry.get(strategy_key)

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label(f"Nueva instancia: {strategy_cls.display_name}").classes("text-lg font-bold")
        name_input = ui.input("Nombre de la instancia", value=f"{strategy_key}-1")
        symbol_input = ui.input("Simbolo", value="BTCUSDT")
        timeframe_input = ui.select(
            ["1", "5", "15", "60", "240", "D"], label="Timeframe (minutos, D=diario)", value="15"
        )
        capital_input = ui.number("Capital asignado (USD)", value=100.0, min=1.0)
        ui.label(
            "Es un capital virtual: no reserva fondos en Bybit, solo se usa para "
            "calcular el tamano de cada operacion de esta instancia."
        ).classes("text-xs text-gray-500")

        ui.separator()
        ui.label("Parametros de la estrategia")
        get_params = render_param_form(strategy_cls.params_model)

        async def on_save() -> None:
            await _save_instance(
                strategy_key=strategy_key,
                name=name_input.value,
                symbol=symbol_input.value,
                timeframe=timeframe_input.value,
                initial_capital=capital_input.value,
                params=get_params().model_dump(),
            )
            dialog.close()
            await refresh()

        with ui.row():
            ui.button("Guardar", on_click=on_save)
            ui.button("Cancelar", on_click=dialog.close).props("flat")

    dialog.open()


@ui.page("/estrategias")
async def estrategias_page() -> None:
    render_nav("/estrategias")

    with ui.column().classes("w-full p-4 gap-4"):
        ui.label("Estrategias disponibles").classes("text-xl font-bold")
        with ui.row().classes("gap-4"):
            for key, strategy_cls in registry.get_all().items():
                with ui.card():
                    ui.label(strategy_cls.display_name).classes("font-bold")
                    ui.label(f"key: {key}").classes("text-xs text-gray-500")
                    ui.button(
                        "Crear instancia",
                        on_click=lambda k=key: _open_create_dialog(k, refresh_instances),
                    )

        ui.separator()
        ui.label("Instancias configuradas").classes("text-xl font-bold")
        instances_container = ui.column().classes("w-full gap-2")

        async def refresh_instances() -> None:
            instances_container.clear()
            instances = await _load_instances()
            with instances_container:
                if not instances:
                    ui.label("Todavia no creaste ninguna instancia.").classes("text-gray-500")
                for inst in instances:
                    with ui.card().classes("w-full"):
                        with ui.row().classes("items-center justify-between w-full"):
                            with ui.column().classes("gap-0"):
                                ui.label(f"{inst.name} ({inst.strategy_key})").classes("font-bold")
                                ui.label(
                                    f"{inst.symbol} · {inst.timeframe} · "
                                    f"USD {inst.initial_capital} · params={inst.params}"
                                ).classes("text-xs text-gray-500")
                            with ui.row().classes("items-center gap-2"):
                                if orchestrator.is_running(inst.id):
                                    ui.icon("bolt", color="green").tooltip("Corriendo")
                                ui.switch(
                                    "Activa",
                                    value=inst.is_active,
                                    on_change=lambda e, i=inst.id: _toggle_active(i, e.value),
                                )

        await refresh_instances()
