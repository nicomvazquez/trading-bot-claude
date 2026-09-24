from typing import Callable

from nicegui import ui
from pydantic import BaseModel
from pydantic_core import PydanticUndefined


def render_param_form(
    model_cls: type[BaseModel], initial: dict | None = None
) -> Callable[[], BaseModel]:
    """Crea inputs de NiceGUI a partir del schema pydantic de una estrategia
    (uno por parametro configurable) y devuelve una funcion que arma una
    instancia validada del modelo con los valores actuales del formulario.

    Debe llamarse dentro de un bloque `with` de un contenedor de NiceGUI."""
    initial = initial or {}
    values: dict[str, object] = {}

    for field_name, field in model_cls.model_fields.items():
        default = initial.get(
            field_name,
            None if field.default is PydanticUndefined else field.default,
        )
        label = field.description or field_name
        values[field_name] = default

        def make_handler(name: str):
            def handler(e) -> None:
                values[name] = e.value

            return handler

        if field.annotation is bool:
            ui.switch(label, value=bool(default)).on_value_change(make_handler(field_name))
        elif field.annotation in (int, float):
            step = 1 if field.annotation is int else 0.1
            ui.number(label, value=default, step=step).props("outlined dense").classes("w-full").on_value_change(
                make_handler(field_name)
            )
        else:
            ui.input(label, value=str(default) if default is not None else "").props(
                "outlined dense"
            ).classes("w-full").on_value_change(make_handler(field_name))

    def get_model() -> BaseModel:
        return model_cls(**values)

    return get_model
