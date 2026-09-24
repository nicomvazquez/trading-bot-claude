"""Selector de uno o dos parametros numericos con su rango (min / max / paso).
Lo usan la sensibilidad a parametros y la re-optimizacion del walk-forward."""

from typing import Callable

from nicegui import ui
from pydantic.fields import FieldInfo

from app.backtest.optimizer import MAX_COMBINATIONS, expand_range
from app.backtest.robustness import numeric_params, param_bounds
from app.ui.backtest_config_panel import ConfigPanel
from app.ui.backtest_format import NEG_CLASS

NONE = "none"


def default_range(field: FieldInfo, current, points: int = 7) -> tuple[float, float, float]:
    """Rango inicial centrado en el valor actual del parametro (respetando sus limites)."""
    is_int = field.annotation is int
    current = field.default if current is None else current
    if is_int:
        step = max(1, int(round(abs(current) * 0.1))) if abs(current) >= 10 else 1
    else:
        step = max(round(abs(current) * 0.1, 6), 0.01) if current else 0.1
    half = (points // 2) * step
    low, high = current - half, current + half
    lo_b, hi_b = param_bounds(field)
    if lo_b is not None:
        low = max(low, lo_b)
    if hi_b is not None:
        high = min(high, hi_b)
    cast = (lambda v: int(round(v))) if is_int else (lambda v: round(v, 6))
    return cast(low), cast(high), step


def within_bounds(values: list, field: FieldInfo) -> list:
    lo_b, hi_b = param_bounds(field)
    return [v for v in values if (lo_b is None or v >= lo_b) and (hi_b is None or v <= hi_b)]


def _outlined(element):
    return element.props("outlined dense").classes("w-full")


class AxesSelector:
    """Se construye dentro del contenedor donde se quiere mostrar. `allow_second`
    agrega el segundo parametro (opcional)."""

    def __init__(self, panel: ConfigPanel, allow_second: bool = True, on_change: Callable[[], None] | None = None) -> None:
        self.panel = panel
        self.allow_second = allow_second
        self.on_change = on_change
        self._busy = False
        self._axes: dict[str, dict] = {}
        keys = ("x", "y") if allow_second else ("x",)
        with ui.element("div").classes("grid grid-cols-[minmax(0,2fr)_1fr_1fr_1fr] gap-x-3 gap-y-3 w-full items-center"):
            for header in ("Parámetro", "Mínimo", "Máximo", "Paso"):
                ui.label(header).classes("text-xs uppercase tracking-wide text-gray-500")
            for axis in keys:
                label = "Parámetro 1" if axis == "x" else "Parámetro 2 (opcional)"
                widgets = {"select": _outlined(ui.select({}, label=label))}
                widgets["min"], widgets["max"], widgets["step"] = (_outlined(ui.number()) for _ in range(3))
                self._axes[axis] = widgets
        self.rebuild()
        for axis, widgets in self._axes.items():
            widgets["select"].on_value_change(self._selected(axis))
            for key in ("min", "max", "step"):
                widgets[key].on_value_change(lambda _: self._changed())
        panel.on_strategy_change(self.rebuild)

    # ------------------------------------------------------------- internals

    def _names(self, exclude: str | None = None) -> dict:
        fields = numeric_params(self.panel.strategy_cls)
        return {n: f.description or n for n, f in fields.items() if n != exclude}

    def _current(self) -> dict:
        try:
            return self.panel.get_params().model_dump()
        except Exception:  # noqa: BLE001
            return {}

    def _fill_defaults(self, axis: str) -> None:
        widgets = self._axes[axis]
        name = widgets["select"].value
        fields = numeric_params(self.panel.strategy_cls)
        if name in (None, NONE) or name not in fields:
            return
        lo, hi, step = default_range(fields[name], self._current().get(name))
        widgets["min"].value, widgets["max"].value, widgets["step"].value = lo, hi, step

    def _selected(self, axis: str):
        def handler(_):
            if self._busy:
                return
            if axis == "x":
                self.rebuild()
            else:
                self._fill_defaults("y")
                self._changed()
        return handler

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    def rebuild(self) -> None:
        self._busy = True  # set_options dispara on_value_change: evita la recursion
        try:
            x_select = self._axes["x"]["select"]
            x_options = self._names()
            x_value = x_select.value if x_select.value in x_options else next(iter(x_options), None)
            x_select.set_options(x_options, value=x_value)
            if self.allow_second:
                y_select = self._axes["y"]["select"]
                y_options = {NONE: "— Ninguno (solo un parámetro) —", **self._names(exclude=x_value)}
                y_select.set_options(y_options, value=y_select.value if y_select.value in y_options else NONE)
            for axis in self._axes:
                self._fill_defaults(axis)
        finally:
            self._busy = False
        self._changed()

    # ----------------------------------------------------------------- public

    def axis_values(self, axis: str) -> tuple[str, list] | None:
        if axis not in self._axes:
            return None
        widgets = self._axes[axis]
        name = widgets["select"].value
        if name in (None, NONE):
            return None
        field = numeric_params(self.panel.strategy_cls)[name]
        raw = expand_range(widgets["min"].value, widgets["max"].value, widgets["step"].value, field.annotation is int)
        return name, within_bounds(raw, field)

    def axes(self) -> list[tuple[str, list]]:
        return [spec for spec in (self.axis_values("x"), self.axis_values("y")) if spec]

    def count(self) -> tuple[int, str]:
        total, parts = 1, []
        for name, values in self.axes():
            total *= max(len(values), 1)
            parts.append(f"{name}: {len(values)}")
        return total, " × ".join(parts) or "—"

    def count_label(self) -> tuple[str, bool]:
        total, parts = self.count()
        return f"{total:,} combinaciones ({parts}) · máximo {MAX_COMBINATIONS}", total > MAX_COMBINATIONS

    @staticmethod
    def label_classes(too_many: bool) -> str:
        return f"text-sm font-medium {NEG_CLASS if too_many else 'text-gray-700'}"
