import json
from unittest.mock import patch

import pytest

from app.config import settings
from app.live.alerts import alerts_configured, send_alert


@pytest.fixture(autouse=True)
def telegram_configured(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "123:ABC")
    monkeypatch.setattr(settings, "telegram_chat_id", "999")


def _fake_response(status: int = 200):
    class Resp:
        def __enter__(self):
            self.status = status
            return self

        def __exit__(self, *exc):
            return False

    return Resp()


def test_alerts_are_not_configured_without_both_values(monkeypatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    assert not alerts_configured()
    monkeypatch.setattr(settings, "telegram_bot_token", "123:ABC")
    monkeypatch.setattr(settings, "telegram_chat_id", "")
    assert not alerts_configured()


def test_alerts_are_configured_with_both_values() -> None:
    assert alerts_configured()


@pytest.mark.asyncio
async def test_send_alert_does_nothing_and_returns_false_when_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    with patch("urllib.request.urlopen") as mock_urlopen:
        assert await send_alert("hola") is False
        mock_urlopen.assert_not_called()


@pytest.mark.asyncio
async def test_send_alert_posts_the_token_chat_id_and_text_to_telegrams_api() -> None:
    with patch("urllib.request.urlopen", return_value=_fake_response(200)) as mock_urlopen:
        ok = await send_alert("¡Se activó el kill-switch!")
    assert ok is True
    request = mock_urlopen.call_args[0][0]
    assert request.full_url == "https://api.telegram.org/bot123:ABC/sendMessage"
    body = json.loads(request.data)
    assert body == {"chat_id": "999", "text": "¡Se activó el kill-switch!"}
    assert request.get_header("Content-type") == "application/json"


@pytest.mark.asyncio
async def test_send_alert_never_raises_when_telegram_fails() -> None:
    with patch("urllib.request.urlopen", side_effect=OSError("sin red")):
        assert await send_alert("hola") is False  # no lanza, solo devuelve False


@pytest.mark.asyncio
async def test_send_alert_treats_a_non_200_response_as_a_failure() -> None:
    with patch("urllib.request.urlopen", return_value=_fake_response(403)):
        assert await send_alert("hola") is False
