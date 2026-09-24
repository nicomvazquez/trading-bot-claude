import asyncio
import datetime as dt
import logging
import threading
import time

import pandas as pd
from pybit import _helpers as pybit_helpers
from pybit.exceptions import InvalidRequestError
from pybit.unified_trading import HTTP

from app.config import settings

_INTERVAL_MINUTES = {
    "1": 1, "3": 3, "5": 5, "15": 15, "30": 30,
    "60": 60, "120": 120, "240": 240, "360": 360, "720": 720,
    "D": 1440, "W": 10080,
}


logger = logging.getLogger(__name__)

_CLOCK_REFRESH_SECONDS = 600
_clock = {"offset_ms": 0, "synced_at": 0.0, "lock": threading.Lock(), "probe": None}
_probing = threading.local()


def _install_clock_sync(probe_http: HTTP) -> None:
    """Bybit rechaza los pedidos firmados cuyo timestamp adelanta mas de ~1 s a su
    reloj (error 10002), y ampliar recv_window no lo evita. Si el reloj local esta
    desfasado (tipico en Docker sobre Windows/WSL), se mide el desfase contra el
    servidor y se compensa en cada firma. Se re-mide cada 10 minutos."""
    _clock["probe"] = probe_http
    original = pybit_helpers.generate_timestamp

    def synced_timestamp() -> int:
        # el propio pedido de medicion vuelve a pasar por aca: no debe medir de nuevo ni esperar
        if not getattr(_probing, "active", False) and time.time() - _clock["synced_at"] > _CLOCK_REFRESH_SECONDS:
            with _clock["lock"]:  # los demas hilos esperan a que termine la medicion en vez de firmar sin corregir
                if time.time() - _clock["synced_at"] > _CLOCK_REFRESH_SECONDS:
                    _probing.active = True
                    try:
                        _clock["synced_at"] = time.time()  # tambien ante fallo, para no reintentar en cada pedido
                        before = time.time() * 1000
                        server_ms = int(_clock["probe"].get_server_time()["time"])
                        _clock["offset_ms"] = int(server_ms - (before + time.time() * 1000) / 2)
                        if abs(_clock["offset_ms"]) > 500:
                            logger.warning("Reloj local desfasado %+d ms respecto de Bybit: se compensa en las firmas", -_clock["offset_ms"])
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("No se pudo medir el desfase de reloj con Bybit: %s", exc)
                    finally:
                        _probing.active = False
        return original() + _clock["offset_ms"]

    pybit_helpers.generate_timestamp = synced_timestamp


class BybitClient:
    """Wrapper fino sobre pybit. Los metodos de pybit son sincronicos (usan
    requests), por eso los corremos en un thread aparte para no bloquear el
    event loop de asyncio."""

    def __init__(self) -> None:
        # Demo Trading (no testnet): corre sobre el dominio de mainnet con
        # fondos virtuales, asi que ve el mismo precio y la misma liquidez
        # reales que produccion — a diferencia del testnet clasico de Bybit,
        # que es un mercado aparte con muy poca profundidad.
        self._http = HTTP(
            demo=settings.bybit_demo,
            api_key=settings.bybit_api_key or None,
            api_secret=settings.bybit_api_secret or None,
        )
        # Los datos de velas historicas se piden siempre a mainnet real (sin
        # demo=True): es informacion publica, no requiere credenciales, y
        # es la misma serie de precios que ya usa el propio Demo Trading.
        self._public_http = HTTP(testnet=False)
        self._instrument_cache: dict[str, dict] = {}
        _install_clock_sync(self._public_http)

    async def check_connection(self) -> dict:
        """Llama a un endpoint autenticado liviano para validar las
        credenciales. Lanza excepcion si Bybit responde con error."""

        def _call() -> dict:
            resp = self._http.get_wallet_balance(accountType="UNIFIED")
            if resp.get("retCode") != 0:
                raise RuntimeError(resp.get("retMsg", "Error desconocido de Bybit"))
            return resp["result"]

        return await asyncio.to_thread(_call)

    async def request_demo_funds(self) -> dict:
        """Acredita un monto fijo de fondos virtuales en la cuenta de Demo
        Trading (lo decide Bybit, no se puede elegir cuanto). Solo funciona
        con demo=True, y tiene un cooldown propio entre pedidos."""
        if not settings.bybit_demo:
            raise RuntimeError("Solo se puede pedir fondos demo cuando BYBIT_ENV=demo")

        def _call() -> dict:
            resp = self._http.request_demo_trading_funds()
            if resp.get("retCode") != 0:
                raise RuntimeError(resp.get("retMsg", "Error desconocido de Bybit"))
            return resp["result"]

        return await asyncio.to_thread(_call)

    async def get_klines(
        self, symbol: str, interval: str, limit: int = 200
    ) -> list[list[str]]:
        def _call() -> list[list[str]]:
            resp = self._http.get_kline(
                category="linear", symbol=symbol, interval=interval, limit=limit
            )
            if resp.get("retCode") != 0:
                raise RuntimeError(resp.get("retMsg", "Error desconocido de Bybit"))
            return resp["result"]["list"]

        return await asyncio.to_thread(_call)

    async def get_candles_df(self, symbol: str, interval: str, limit: int = 1000) -> pd.DataFrame:
        """Velas recientes del entorno de TRADING configurado (demo o
        mainnet segun BYBIT_ENV) — a diferencia de get_historical_klines,
        que siempre usa mainnet para backtesting. Para operar en vivo hay
        que decidir y ejecutar sobre el mismo precio."""
        rows = await self.get_klines(symbol, interval, limit=limit)
        if not rows:
            return pd.DataFrame()
        records = [
            {
                "timestamp": dt.datetime.fromtimestamp(int(row[0]) / 1000, tz=dt.timezone.utc),
                "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
                "close": float(row[4]), "volume": float(row[5]),
            }
            for row in rows
        ]
        df = pd.DataFrame(records).sort_values("timestamp").set_index("timestamp")
        return df

    async def get_historical_klines(
        self, symbol: str, interval: str, start: dt.datetime, end: dt.datetime
    ) -> list[dict]:
        """Trae velas historicas (mainnet, publico) paginando hacia atras
        hasta cubrir todo el rango [start, end]. Bybit devuelve maximo 1000
        velas por request, mas recientes primero."""
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        interval_ms = _INTERVAL_MINUTES[interval] * 60_000

        def _call(end_cursor: int) -> list[list[str]]:
            resp = self._public_http.get_kline(
                category="linear",
                symbol=symbol,
                interval=interval,
                end=end_cursor,
                limit=1000,
            )
            if resp.get("retCode") != 0:
                raise RuntimeError(resp.get("retMsg", "Error desconocido de Bybit"))
            return resp["result"]["list"]

        rows_by_ts: dict[int, list[str]] = {}
        cursor = end_ms
        while True:
            batch = await asyncio.to_thread(_call, cursor)
            if not batch:
                break
            for row in batch:
                rows_by_ts[int(row[0])] = row
            oldest_ts = min(int(row[0]) for row in batch)
            if oldest_ts <= start_ms or len(batch) < 1000:
                break
            cursor = oldest_ts - interval_ms
            await asyncio.sleep(0.1)  # no saturar el rate limit publico

        candles = [
            {
                "timestamp": dt.datetime.fromtimestamp(ts / 1000, tz=dt.timezone.utc),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
            for ts, row in sorted(rows_by_ts.items())
            if start_ms <= ts <= end_ms
        ]
        return candles

    async def get_funding_history(self, symbol: str, start: dt.datetime, end: dt.datetime) -> list[dict]:
        """Funding historico (mainnet, publico), paginando hacia atras. Bybit
        devuelve hasta 200 registros por pedido, del mas reciente al mas viejo."""
        start_ms = int(start.timestamp() * 1000)
        cursor_end = int(end.timestamp() * 1000)

        def _call(end_ms: int) -> list[dict]:
            resp = self._public_http.get_funding_rate_history(
                category="linear", symbol=symbol, startTime=start_ms, endTime=end_ms, limit=200
            )
            if resp.get("retCode") != 0:
                raise RuntimeError(resp.get("retMsg", "Error desconocido de Bybit"))
            return resp["result"]["list"]

        by_ts: dict[int, float] = {}
        while True:
            batch = await asyncio.to_thread(_call, cursor_end)
            if not batch:
                break
            for item in batch:
                by_ts[int(item["fundingRateTimestamp"])] = float(item["fundingRate"])
            oldest = min(int(item["fundingRateTimestamp"]) for item in batch)
            if oldest <= start_ms or len(batch) < 200:
                break
            cursor_end = oldest - 1
            await asyncio.sleep(0.1)

        return [
            {"timestamp": dt.datetime.fromtimestamp(ts / 1000, tz=dt.timezone.utc), "rate": rate}
            for ts, rate in sorted(by_ts.items())
            if start_ms <= ts
        ]

    async def get_instrument_info(self, symbol: str) -> dict:
        """qtyStep/minOrderQty/tickSize del simbolo, para redondear cantidades
        y precios como Bybit los exige. Se cachea: no cambia en caliente."""
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]

        def _call() -> dict:
            resp = self._http.get_instruments_info(category="linear", symbol=symbol)
            if resp.get("retCode") != 0:
                raise RuntimeError(resp.get("retMsg", "Error desconocido de Bybit"))
            items = resp["result"]["list"]
            if not items:
                raise RuntimeError(f"Bybit no devolvio informacion del instrumento {symbol}")
            item = items[0]
            return {
                "qty_step": float(item["lotSizeFilter"]["qtyStep"]),
                "min_qty": float(item["lotSizeFilter"]["minOrderQty"]),
                "tick_size": float(item["priceFilter"]["tickSize"]),
            }

        info = await asyncio.to_thread(_call)
        self._instrument_cache[symbol] = info
        return info

    async def round_qty(self, symbol: str, qty: float) -> float:
        info = await self.get_instrument_info(symbol)
        step = info["qty_step"]
        rounded = (qty // step) * step
        return round(rounded, 10) if rounded >= info["min_qty"] else 0.0

    async def set_leverage(self, symbol: str, leverage: float) -> None:
        def _call() -> None:
            try:
                self._http.set_leverage(
                    category="linear", symbol=symbol,
                    buyLeverage=str(leverage), sellLeverage=str(leverage),
                )
            except InvalidRequestError as exc:
                # 110043 = "leverage not modified": ya estaba en ese valor, no es error.
                # pybit no devuelve esto como respuesta normal, lo lanza como excepcion.
                if exc.status_code != 110043:
                    raise

        await asyncio.to_thread(_call)

    async def place_market_order(
        self,
        symbol: str,
        side: str,  # "Buy" | "Sell"
        qty: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        reduce_only: bool = False,
    ) -> dict:
        def _call() -> dict:
            kwargs = dict(
                category="linear", symbol=symbol, side=side, orderType="Market",
                qty=str(qty), reduceOnly=reduce_only,
            )
            if stop_loss is not None:
                kwargs["stopLoss"] = str(stop_loss)
            if take_profit is not None:
                kwargs["takeProfit"] = str(take_profit)
            resp = self._http.place_order(**kwargs)
            if resp.get("retCode") != 0:
                raise RuntimeError(resp.get("retMsg", "Error desconocido de Bybit"))
            return resp["result"]

        return await asyncio.to_thread(_call)

    async def get_open_position(self, symbol: str) -> dict | None:
        def _call() -> dict | None:
            resp = self._http.get_positions(category="linear", symbol=symbol)
            if resp.get("retCode") != 0:
                raise RuntimeError(resp.get("retMsg", "Error desconocido de Bybit"))
            for pos in resp["result"]["list"]:
                if float(pos.get("size") or 0) > 0:
                    return {
                        "side": "long" if pos["side"] == "Buy" else "short",
                        "qty": float(pos["size"]),
                        "entry_price": float(pos["avgPrice"]),
                        "unrealised_pnl": float(pos["unrealisedPnl"]),
                    }
            return None

        return await asyncio.to_thread(_call)

    async def get_open_interest_history(
        self, symbol: str, interval: str, start: dt.datetime, end: dt.datetime
    ) -> list[dict]:
        """Open interest historico (publico). `interval`: 5min, 15min, 30min, 1h, 4h o 1d.
        Bybit devuelve hasta 200 registros por pedido, del mas reciente al mas viejo."""
        start_ms = int(start.timestamp() * 1000)
        cursor_end = int(end.timestamp() * 1000)

        def _call(end_ms: int) -> list[dict]:
            resp = self._public_http.get_open_interest(
                category="linear", symbol=symbol, intervalTime=interval, startTime=start_ms, endTime=end_ms, limit=200
            )
            if resp.get("retCode") != 0:
                raise RuntimeError(resp.get("retMsg", "Error desconocido de Bybit"))
            return resp["result"]["list"]

        by_ts: dict[int, float] = {}
        while True:
            batch = await asyncio.to_thread(_call, cursor_end)
            if not batch:
                break
            for item in batch:
                by_ts[int(item["timestamp"])] = float(item["openInterest"])
            oldest = min(int(item["timestamp"]) for item in batch)
            if oldest <= start_ms or len(batch) < 200:
                break
            cursor_end = oldest - 1
            await asyncio.sleep(0.1)

        return [
            {"timestamp": dt.datetime.fromtimestamp(ts / 1000, tz=dt.timezone.utc), "value": value}
            for ts, value in sorted(by_ts.items())
            if start_ms <= ts
        ]

    async def get_wallet_balance(self) -> dict:
        """Equity total de la cuenta unificada y PnL no realizado en USDT."""
        def _call() -> dict:
            resp = self._http.get_wallet_balance(accountType="UNIFIED")
            if resp.get("retCode") != 0:
                raise RuntimeError(resp.get("retMsg", "Error desconocido de Bybit"))
            account = resp["result"]["list"][0]
            usdt = next((c for c in account.get("coin", []) if c.get("coin") == "USDT"), {})
            return {
                "equity": float(account.get("totalEquity") or 0),  # incluye BTC/ETH regalados en Demo
                "margin_balance": float(account.get("totalMarginBalance") or 0),  # solo lo que respalda el margen (USDT/USDC)
                "available": float(account.get("totalAvailableBalance") or 0),
                "unrealised_pnl": float(usdt.get("unrealisedPnl") or 0),
            }

        return await asyncio.to_thread(_call)

    async def get_closed_pnl_records(self, symbol: str, limit: int = 50) -> list[dict]:
        """Ultimos cierres registrados por Bybit para el simbolo (del mas reciente al mas viejo)."""
        def _call() -> list[dict]:
            resp = self._http.get_closed_pnl(category="linear", symbol=symbol, limit=limit)
            if resp.get("retCode") != 0:
                raise RuntimeError(resp.get("retMsg", "Error desconocido de Bybit"))
            return [
                {
                    "avg_exit_price": float(item["avgExitPrice"]),
                    "closed_pnl": float(item["closedPnl"]),
                    "updated_time": dt.datetime.fromtimestamp(int(item["updatedTime"]) / 1000, tz=dt.timezone.utc),
                }
                for item in resp["result"]["list"]
            ]

        return await asyncio.to_thread(_call)


bybit_client = BybitClient()
