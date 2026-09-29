"""Alertas por Telegram para lo que amerita atencion ya: errores de la operativa en vivo y el kill-switch.

Se configuran con TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env:
1. Hablale a @BotFather en Telegram, /newbot, y copia el token que te da.
2. Escribile cualquier mensaje a tu bot nuevo (Telegram exige esto antes de que pueda escribirte el).
3. Conseguí tu chat_id (por ejemplo, hablandole a @userinfobot) y ponelo en TELEGRAM_CHAT_ID.

Sin esos dos valores, send_alert no hace nada: no rompe la operativa si no se configuran."""

import asyncio
import json
import logging
import urllib.error
import urllib.request

from app.config import settings

logger = logging.getLogger(__name__)
_TIMEOUT = 10


def alerts_configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def _post(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310 - URL fija de la API de Telegram
        if response.status != 200:
            raise RuntimeError(f"Telegram respondió {response.status}")


async def send_alert(text: str) -> bool:
    """Manda `text` por Telegram. Nunca lanza: una alerta que falla no debe frenar la operativa en vivo.
    Devuelve False si las alertas no estan configuradas o si el envio fallo."""
    if not alerts_configured():
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        await asyncio.to_thread(_post, url, {"chat_id": settings.telegram_chat_id, "text": text})
        return True
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo enviar la alerta de Telegram")
        return False
