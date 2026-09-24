import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from app.backtest.engine import BacktestResult, TradeRecord
from app.ui.backtest_format import EXIT_REASON_LABELS

BLUE = "#2a78d6"
BLUE_SOFT = "#86b6ef"
ORANGE = "#eb6834"
RED = "#e34948"
WIN = "#0ca30c"
LOSS = "#d03b3b"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#ffffff"
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"
MAX_POINTS = 6000


def _base_layout(**overrides) -> dict:
    layout = dict(
        font=dict(family=FONT, size=12, color=INK_2),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel=dict(bgcolor=SURFACE, bordercolor=GRID, font=dict(family=FONT, size=12, color=INK)),
    )
    layout.update(overrides)
    return layout


def _decimate(index: pd.DatetimeIndex, values: np.ndarray, max_points: int = MAX_POINTS):
    """Reduce la cantidad de puntos a graficar (los datos y las metricas no se tocan)."""
    n = len(values)
    if n <= max_points:
        return index, values
    step = int(np.ceil(n / max_points))
    keep = np.unique(np.concatenate((np.arange(0, n, step), [n - 1])))
    return index[keep], values[keep]


def _py(ts):
    """datetime nativo (el JSON de NiceGUI no acepta subclases como pd.Timestamp)."""
    return ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts


def _detail(t: TradeRecord, kind: str) -> str:
    side = "Long" if t.side == "long" else "Short"
    if kind == "entry":
        return f"#{t.id} · {side} · entrada a {t.entry_price:,.2f}"
    reason = EXIT_REASON_LABELS.get(t.exit_reason, t.exit_reason)
    return f"#{t.id} · {side} · salida a {t.exit_price:,.2f} · {'+' if t.pnl >= 0 else '-'}${abs(t.pnl):,.2f} ({reason})"


def _marker_traces(trades: list[TradeRecord], y_entry, y_exit) -> list[go.Scatter]:
    """Marcadores de entrada (por lado) y de salida (por resultado). Cada punto
    lleva el id de la operacion en `customdata` para poder seleccionarla."""
    closed = [t for t in trades if t.exit_time is not None and t.pnl is not None]
    specs = [
        ("Entrada Long", [t for t in closed if t.side == "long"], "entry", "triangle-up", BLUE, y_entry),
        ("Entrada Short", [t for t in closed if t.side == "short"], "entry", "triangle-down", ORANGE, y_entry),
        ("Salida ganadora", [t for t in closed if t.pnl > 0], "exit", "circle", WIN, y_exit),
        ("Salida perdedora", [t for t in closed if t.pnl <= 0], "exit", "x", LOSS, y_exit),
    ]
    traces = []
    for name, subset, kind, symbol, color, y_of in specs:
        if not subset:
            continue
        when = [_py(t.entry_time if kind == "entry" else t.exit_time) for t in subset]
        marker = dict(size=9, color=color, symbol=symbol, line=dict(width=2, color=SURFACE if symbol != "x" else color))
        traces.append(
            go.Scatter(
                x=when, y=[y_of(t) for t in subset], mode="markers", name=name,
                text=[_detail(t, kind) for t in subset], customdata=[t.id for t in subset],
                marker=marker, hovertemplate="%{text}<extra></extra>",
            )
        )
    return traces


def equity_drawdown_chart(result: BacktestResult, highlight: TradeRecord | None = None) -> go.Figure:
    """Dos paneles con el mismo eje de tiempo: capital arriba, drawdown abajo.
    Cada panel tiene una sola escala (nada de doble eje)."""
    equity = result.equity_curve
    initial = result.initial_capital
    running_max = np.maximum.accumulate(np.concatenate(([initial], equity.to_numpy())))[1:]
    drawdown = (equity.to_numpy() / running_max - 1.0) * 100
    # pandas Timestamp es subclase de datetime y el serializador JSON de
    # NiceGUI (orjson) no la acepta: hay que pasar datetime nativo.
    x_e, y_e = _decimate(equity.index, equity.to_numpy())
    x_d, y_d = _decimate(equity.index, drawdown)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.09, row_heights=[0.7, 0.3])
    fig.add_trace(
        go.Scatter(
            x=x_e.to_pydatetime(), y=y_e, mode="lines", name="Capital", line=dict(color=BLUE, width=2),
            hovertemplate="Capital: $%{y:,.2f}<extra></extra>",
        ),
        row=1, col=1,
    )
    for trace in _marker_traces(
        result.trades,
        lambda t: float(equity.asof(t.entry_time)),
        lambda t: float(equity.asof(t.exit_time)),
    ):
        fig.add_trace(trace, row=1, col=1)

    fig.add_hline(
        y=initial, row=1, col=1, line=dict(color=MUTED, width=1, dash="dot"),
        annotation_text="Capital inicial", annotation_position="bottom right",
        annotation_font=dict(size=11, color=MUTED),
    )
    fig.add_trace(
        go.Scatter(
            x=x_d.to_pydatetime(), y=y_d, mode="lines", name="Drawdown",
            line=dict(color=RED, width=1.5), fill="tozeroy", fillcolor="rgba(227,73,72,0.12)",
            hovertemplate="Drawdown: %{y:.2f}%<extra></extra>",
        ),
        row=2, col=1,
    )

    dd_min = float(drawdown.min())
    dd_low = min(dd_min * 1.15, -0.5)
    fig.update_xaxes(showgrid=False, showline=True, linecolor=BASELINE, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, gridwidth=1, zeroline=False, showline=False, tickfont=dict(color=MUTED))
    fig.update_yaxes(tickprefix="$", tickformat=",.0f", row=1, col=1)
    fig.update_yaxes(
        ticksuffix="%", tickformat=".1f", range=[dd_low, max(abs(dd_low) * 0.05, 0.05)],
        zeroline=True, zerolinecolor=BASELINE, zerolinewidth=1, row=2, col=1,
    )
    if highlight is not None and highlight.exit_time is not None:
        fig.add_vrect(
            x0=_py(highlight.entry_time), x1=_py(highlight.exit_time),
            fillcolor="rgba(42,120,214,0.10)", line_width=0, layer="below",
        )

    top, bottom = fig.layout.yaxis.domain, fig.layout.yaxis2.domain
    for text, domain in (("Capital (USD)", top), ("Drawdown (% desde el máximo previo)", bottom)):
        fig.add_annotation(
            text=text, xref="paper", yref="paper", x=0, y=domain[1] + 0.02, xanchor="left", yanchor="bottom",
            showarrow=False, font=dict(size=12, color=INK_2),
        )
    fig.update_layout(
        **_base_layout(
            height=600, margin=dict(l=64, r=16, t=64, b=36), hovermode="closest", clickmode="event",
            legend=dict(orientation="h", x=1, xanchor="right", y=top[1] + 0.02, yanchor="bottom"),
        )
    )
    return fig


def price_trades_chart(result: BacktestResult, candles: pd.DataFrame, highlight: TradeRecord | None = None) -> go.Figure:
    """Precio de cierre con cada operacion: donde entro, donde salio y como termino."""
    window = candles.loc[result.equity_curve.index[0]: result.equity_curve.index[-1]]
    x, y = _decimate(window.index, window["close"].to_numpy())
    fig = go.Figure(
        go.Scatter(
            x=x.to_pydatetime(), y=y, mode="lines", name="Precio (cierre)",
            line=dict(color=MUTED, width=1.5), hovertemplate="Precio: %{y:,.2f}<extra></extra>",
        )
    )
    closed = [t for t in result.trades if t.exit_time is not None and t.pnl is not None]
    for label, color, subset in (
        ("Trayecto ganador", WIN, [t for t in closed if t.pnl > 0]),
        ("Trayecto perdedor", LOSS, [t for t in closed if t.pnl <= 0]),
    ):
        if not subset:
            continue
        xs, ys = [], []
        for t in subset:
            xs += [_py(t.entry_time), _py(t.exit_time), None]
            ys += [t.entry_price, t.exit_price, None]
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", name=label, line=dict(color=color, width=1.5, dash="dot"), hoverinfo="skip",
        ))
    for trace in _marker_traces(result.trades, lambda t: t.entry_price, lambda t: t.exit_price):
        fig.add_trace(trace)
    if highlight is not None and highlight.exit_time is not None:
        fig.add_vrect(
            x0=_py(highlight.entry_time), x1=_py(highlight.exit_time),
            fillcolor="rgba(42,120,214,0.10)", line_width=0, layer="below",
        )
    fig.update_layout(**_base_layout(
        height=420, margin=dict(l=64, r=16, t=40, b=36), hovermode="closest", clickmode="event",
        legend=dict(orientation="h", x=1, xanchor="right", y=1.02, yanchor="bottom"),
    ))
    fig.update_xaxes(showgrid=False, showline=True, linecolor=BASELINE, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, tickprefix="$", tickformat=",.0f", tickfont=dict(color=MUTED))
    return fig


def histogram(
    values: list[float], color: str, marks: list[tuple[float, str, str]] | None = None,
    x_prefix: str = "", x_suffix: str = "%", unit: str = "simulaciones", y_title: str | None = "Simulaciones",
) -> go.Figure:
    """marks: (valor, etiqueta, posicion de la etiqueta) para las lineas verticales."""
    fig = go.Figure(
        go.Histogram(
            x=values, nbinsx=30, marker=dict(color=color, line=dict(color=SURFACE, width=2)),
            hovertemplate=f"{x_prefix}%{{x:.2f}}{x_suffix}: %{{y}} {unit}<extra></extra>",
        )
    )
    for value, label, position in marks or []:
        fig.add_vline(
            x=value, line=dict(color=INK_2, width=1), annotation_text=label, annotation_position=position,
            annotation_font=dict(size=11, color=INK_2),
        )
    fig.update_layout(**_base_layout(height=320, margin=dict(l=48, r=16, t=28, b=40), showlegend=False, bargap=0.02))
    fig.update_xaxes(
        tickprefix=x_prefix, ticksuffix=x_suffix, showgrid=False, showline=True, linecolor=BASELINE,
        tickfont=dict(color=MUTED),
    )
    fig.update_yaxes(
        gridcolor=GRID, zeroline=False, tickfont=dict(color=MUTED),
        title_text=y_title, title_font=dict(size=11, color=MUTED),
    )
    return fig


def optimization_bars(labels: list[str], values: list[float], metric_label: str) -> go.Figure:
    """Barras horizontales (las etiquetas de parametros son largas)."""
    fig = go.Figure(
        go.Bar(
            x=values, y=labels, orientation="h", width=0.6, marker=dict(color=BLUE_SOFT),
            text=[f"{v:,.2f}" for v in values], textposition="outside", cliponaxis=False,
            textfont=dict(color=INK_2, size=11),
            hovertemplate="%{y}<br>" + metric_label + ": %{x:,.2f}<extra></extra>",
        )
    )
    fig.update_layout(**_base_layout(
        height=max(200, 34 * len(labels) + 70), margin=dict(l=8, r=56, t=8, b=32), showlegend=False, bargap=0.4,
    ))
    fig.update_yaxes(autorange="reversed", showgrid=False, automargin=True, tickfont=dict(color=INK_2))
    fig.update_xaxes(gridcolor=GRID, zeroline=True, zerolinecolor=BASELINE, tickfont=dict(color=MUTED))
    return fig


def sensitivity_lines(result, labels: dict[str, str], neutral: dict[str, float | None]) -> go.Figure:
    """Un grafico chico por metrica (small multiples): cada metrica tiene su
    propia escala. Linea vertical punteada = valor actual del parametro."""
    metrics = list(labels)
    cols = 3
    rows = int(np.ceil(len(metrics) / cols))
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=[labels[m] for m in metrics],
                        vertical_spacing=0.16, horizontal_spacing=0.07)
    name = result.names[0]
    xs = [p["params"][name] for p in result.points]
    base = result.base_params.get(name)
    for i, metric in enumerate(metrics):
        row, col = i // cols + 1, i % cols + 1
        ys = [p.get(metric) for p in result.points]
        fig.add_trace(
            go.Scatter(
                x=xs, y=ys, mode="lines+markers", name=labels[metric],
                line=dict(color=BLUE, width=2),
                marker=dict(size=8, color=BLUE, line=dict(width=2, color=SURFACE)),
                hovertemplate=f"{name}=%{{x}}<br>{labels[metric]}: %{{y:.2f}}<extra></extra>",
            ),
            row=row, col=col,
        )
        ref = neutral.get(metric)
        if ref is not None:
            fig.add_hline(y=ref, row=row, col=col, line=dict(color=BASELINE, width=1))
        if base in xs:
            fig.add_vline(x=base, row=row, col=col, line=dict(color=MUTED, width=1, dash="dot"))
    fig.update_xaxes(showgrid=False, showline=True, linecolor=BASELINE, tickfont=dict(color=MUTED, size=11))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, tickfont=dict(color=MUTED, size=11))
    fig.update_annotations(font=dict(size=12, color=INK_2))
    fig.update_layout(**_base_layout(height=260 * rows, margin=dict(l=48, r=16, t=40, b=36), showlegend=False))
    return fig


def sensitivity_heatmap(
    xs: list, ys: list, z: list[list[float | None]], x_name: str, y_name: str, metric_label: str,
    neutral: float | None, base: tuple | None = None,
) -> go.Figure:
    """Heatmap con colores divergentes (rojo negativo, azul positivo, gris neutro
    en el valor de referencia); para el drawdown, una rampa secuencial. Celdas
    categoricas: todas del mismo tamano aunque el paso no sea uniforme."""
    if neutral is None:
        colorscale, extra = [[0.0, "#e34948"], [1.0, "#f0efec"]], {}
    else:
        colorscale, extra = [[0.0, "#e34948"], [0.5, "#f0efec"], [1.0, "#2a78d6"]], {"zmid": neutral}
    fig = go.Figure(
        go.Heatmap(
            z=z, x=[str(x) for x in xs], y=[str(y) for y in ys], colorscale=colorscale, xgap=2, ygap=2,
            texttemplate="%{z:.2f}" if len(xs) * len(ys) <= 150 else None, textfont=dict(size=11, color=INK),
            colorbar=dict(title=dict(text=metric_label, font=dict(size=11)), thickness=12, outlinewidth=0),
            hovertemplate=f"{x_name}=%{{x}}<br>{y_name}=%{{y}}<br>{metric_label}: %{{z:.2f}}<extra></extra>",
            **extra,
        )
    )
    if base is not None and base[0] in xs and base[1] in ys:
        i, j = xs.index(base[0]), ys.index(base[1])
        fig.add_shape(type="rect", x0=i - 0.5, x1=i + 0.5, y0=j - 0.5, y1=j + 0.5, line=dict(color=INK, width=2))
    fig.update_layout(**_base_layout(
        height=max(320, 46 * len(ys) + 120), margin=dict(l=72, r=16, t=16, b=56), showlegend=False,
    ))
    fig.update_xaxes(type="category", title_text=x_name, title_font=dict(size=12), showgrid=False, tickfont=dict(color=INK_2))
    fig.update_yaxes(type="category", title_text=y_name, title_font=dict(size=12), showgrid=False, tickfont=dict(color=INK_2))
    return fig


def oos_equity_chart(in_result: BacktestResult, out_result: BacktestResult, cut) -> go.Figure:
    """Curva de capital in-sample y out-of-sample. El tramo out-of-sample arranca
    de cero, pero se dibuja continuando el capital final del in-sample para poder
    leer las dos curvas juntas; la linea vertical marca el corte."""
    e_in, e_out = in_result.equity_curve, out_result.equity_curve
    scale = float(e_in.iloc[-1]) / out_result.initial_capital
    x_in, y_in = _decimate(e_in.index, e_in.to_numpy())
    x_out, y_out = _decimate(e_out.index, e_out.to_numpy() * scale)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x_in.to_pydatetime(), y=y_in, mode="lines", name="In-sample", line=dict(color=BLUE, width=2),
        hovertemplate="In-sample: $%{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=x_out.to_pydatetime(), y=y_out, mode="lines", name="Out-of-sample (re-escalado)",
        line=dict(color=ORANGE, width=2), hovertemplate="Out-of-sample: $%{y:,.2f}<extra></extra>",
    ))
    fig.add_vline(
        x=_py(cut), line=dict(color=MUTED, width=1, dash="dot"), annotation_text="Corte",
        annotation_position="top", annotation_font=dict(size=11, color=MUTED),
    )
    fig.add_hline(y=in_result.initial_capital, line=dict(color=BASELINE, width=1))
    fig.update_layout(**_base_layout(
        height=380, margin=dict(l=64, r=16, t=48, b=36), hovermode="x unified",
        legend=dict(orientation="h", x=1, xanchor="right", y=1.02, yanchor="bottom"),
    ))
    fig.update_xaxes(showgrid=False, showline=True, linecolor=BASELINE, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, tickprefix="$", tickformat=",.0f", tickfont=dict(color=MUTED))
    return fig


def walkforward_bars(labels: list[str], train: list[float | None], test: list[float | None], hover: list[str]) -> go.Figure:
    """Retorno de cada ventana: entrenamiento (suave) vs test (intenso)."""
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=train, name="Entrenamiento", marker=dict(color=BLUE_SOFT), text=hover, hovertemplate="%{text}<br>Entrenamiento: %{y:.2f}%<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=labels, y=test, name="Test (fuera de muestra)", marker=dict(color=BLUE), text=hover, hovertemplate="%{text}<br>Test: %{y:.2f}%<extra></extra>",
    ))
    fig.add_hline(y=0, line=dict(color=BASELINE, width=1))
    fig.update_layout(**_base_layout(
        height=340, margin=dict(l=56, r=16, t=40, b=48), barmode="group", bargap=0.35,
        legend=dict(orientation="h", x=1, xanchor="right", y=1.02, yanchor="bottom"),
    ))
    fig.update_xaxes(showgrid=False, showline=True, linecolor=BASELINE, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, ticksuffix="%", tickfont=dict(color=MUTED))
    return fig
