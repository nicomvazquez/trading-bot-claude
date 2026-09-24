"""Las guias de app/docs no deben quedar desactualizadas respecto del codigo: cada estrategia, cada parametro,
su valor por defecto y su rango tienen que coincidir con lo documentado."""

import pathlib
import re

import pytest
from pydantic_core import PydanticUndefined

from app.strategies import registry

DOCS = pathlib.Path(__file__).resolve().parent.parent / "app" / "docs"
STRATEGY_DOC = (DOCS / "estrategias.md").read_text(encoding="utf-8")


def _section(key: str) -> str:
    """Texto de la seccion de una estrategia (desde su encabezado con `key` hasta el siguiente ## )."""
    match = re.search(rf"^## .*`{re.escape(key)}`.*?(?=^## |\Z)", STRATEGY_DOC, re.MULTILINE | re.DOTALL)
    assert match, f"la guia no tiene una seccion para la estrategia `{key}`"
    return match.group(0)


def _bounds(field) -> tuple[float | None, float | None]:
    lo = hi = None
    for meta in field.metadata:
        lo = getattr(meta, "ge", lo)
        hi = getattr(meta, "le", hi)
    return lo, hi


def _num(text: str) -> float:
    return float(text.replace(",", ".").strip())


@pytest.mark.parametrize("key", sorted(registry.get_all()))
def test_every_registered_strategy_is_documented_with_its_real_parameters(key: str) -> None:
    cls = registry.get(key)
    section = _section(key)
    assert cls.display_name.split(" (")[0].split(":")[0] and len(section) > 300
    rows = {}
    for line in section.splitlines():
        if line.startswith("| `"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            for name in re.findall(r"`(\w+)`", cells[0]):
                rows[name] = cells

    for name, field in cls.params_model.model_fields.items():
        assert name in rows, f"{key}: el parametro `{name}` no esta en la tabla de la guia"
        cells = rows[name]
        default = field.default
        if isinstance(default, bool):
            assert cells[1] == ("Sí" if default else "No"), f"{key}.{name}: por defecto documentado {cells[1]!r}, real {default}"
            continue
        if " / " in cells[0]:  # fila que agrupa varios parametros (p. ej. london_start / london_end): "2 / 5"
            names = re.findall(r"`(\w+)`", cells[0])
            documented = [_num(x) for x in cells[1].split("/")]
            assert documented[names.index(name)] == pytest.approx(default), f"{key}.{name}: por defecto distinto"
            continue
        assert _num(cells[1]) == pytest.approx(default), f"{key}.{name}: por defecto documentado {cells[1]}, real {default}"
        lo, hi = _bounds(field)
        if lo is not None and hi is not None:
            documented_lo, documented_hi = (_num(x) for x in re.split(r"[–-]", cells[2]))
            assert (documented_lo, documented_hi) == (pytest.approx(lo), pytest.approx(hi)), (
                f"{key}.{name}: rango documentado {cells[2]}, real {lo}–{hi}"
            )


def test_all_guides_exist_and_cross_references_point_to_real_sections() -> None:
    for name in ("manual_de_uso.md", "estrategias.md", "backtester.md"):
        text = (DOCS / name).read_text(encoding="utf-8")
        assert len(text) > 5000, name
        assert "asesoramiento" in text.lower() or name != "manual_de_uso.md"
        headings = {int(m.group(1)) for m in re.finditer(r"^## (\d+)\.", text, re.MULTILINE)}
        for ref in re.findall(r"\(ver sección (\d+)\)|Ver sección (\d+)\.", text):
            number = int(next(x for x in ref if x))
            assert number in headings, f"{name}: referencia a la seccion {number}, que no existe"


def test_documented_default_execution_costs_match_the_code() -> None:
    from app.backtest.config import ExecutionConfig, RiskConfig, ValidationConfig

    text = (DOCS / "backtester.md").read_text(encoding="utf-8")
    assert f"{ExecutionConfig().taker_fee_pct:.3f}".replace(".", ",").rstrip("0") in text  # 0,055
    assert PydanticUndefined is not None  # (import usado por el helper de arriba)
    assert str(RiskConfig().max_leverage).rstrip("0").rstrip(".") in text
    assert str(ValidationConfig().mc_sims) in text
