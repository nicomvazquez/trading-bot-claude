import time

import pytest

from app import auth
from app.config import settings


@pytest.fixture(autouse=True)
def _reset_lockout():
    auth._failed_attempts.clear()
    yield
    auth._failed_attempts.clear()


def test_hash_and_verify_password_roundtrip() -> None:
    stored = auth.hash_password("una-contraseña-segura")
    assert auth.verify_password("una-contraseña-segura", stored)
    assert not auth.verify_password("otra-cosa", stored)


def test_verify_password_rejects_a_malformed_stored_value() -> None:
    assert not auth.verify_password("cualquiera", "esto-no-tiene-el-separador")


def test_hash_password_uses_a_random_salt_each_time() -> None:
    a, b = auth.hash_password("misma-contraseña"), auth.hash_password("misma-contraseña")
    assert a != b  # salts distintos -> hashes distintos aunque la contraseña sea igual
    assert auth.verify_password("misma-contraseña", a)
    assert auth.verify_password("misma-contraseña", b)


def test_check_credentials_matches_user_and_password(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_user", "admin")
    monkeypatch.setattr(settings, "app_password_hash", auth.hash_password("correcta123"))
    assert auth.check_credentials("admin", "correcta123")
    assert not auth.check_credentials("admin", "incorrecta")
    assert not auth.check_credentials("otro", "correcta123")


def test_check_credentials_fails_closed_without_a_configured_hash(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_password_hash", "")
    assert not auth.check_credentials(settings.app_user, "cualquiera")


def test_lockout_triggers_after_max_attempts_and_expires() -> None:
    for _ in range(auth.MAX_ATTEMPTS - 1):
        auth._failed_attempts.append(time.time())
    locked, _ = auth.is_locked_out()
    assert not locked

    auth._failed_attempts.append(time.time())
    locked, remaining = auth.is_locked_out()
    assert locked and remaining > 0

    auth._failed_attempts[0] -= auth.LOCKOUT_SECONDS  # simula que el primer intento ya vencio
    locked, _ = auth.is_locked_out()
    assert not locked


def test_register_successful_login_clears_the_lockout() -> None:
    auth._failed_attempts.extend([time.time()] * auth.MAX_ATTEMPTS)
    auth.register_successful_login()
    locked, _ = auth.is_locked_out()
    assert not locked


@pytest.mark.asyncio
async def test_register_failed_attempt_records_it_without_a_real_alert(monkeypatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(settings, "telegram_chat_id", "")
    await auth.register_failed_attempt()
    assert len(auth._failed_attempts) == 1
