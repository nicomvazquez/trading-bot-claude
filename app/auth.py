"""Login del dashboard: usuario/contrasena fijos por .env (un solo operador), sesion via cookie
firmada (NiceGUI storage_secret) y bloqueo temporal tras varios intentos fallidos seguidos.

El bloqueo es GLOBAL, no por IP: es mas simple, no depende de confiar en el header de origen
(que un proxy/tunel puede no reenviar bien) y es mas estricto, lo cual tiene sentido para un
sistema de un solo operador donde nunca hay intentos legitimos concurrentes desde IPs distintas."""

import hashlib
import hmac
import logging
import os
import time

from nicegui import Client, app as nicegui_app
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.config import settings

logger = logging.getLogger(__name__)

UNRESTRICTED_PAGE_ROUTES = {"/login"}
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300
_PBKDF2_ITERATIONS = 200_000

_failed_attempts: list[float] = []  # timestamps de intentos fallidos recientes (globales)


def hash_password(password: str) -> str:
    """Genera "salt_hex:hash_hex" listo para pegar en APP_PASSWORD_HASH. Nunca se guarda la
    contrasena en texto plano, ni siquiera en .env. Separador ":" a proposito: docker compose
    interpreta un "$" suelto en un .env como sustitucion de variable y corrompe el valor."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    expected = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return hmac.compare_digest(expected.hex(), digest_hex)


def check_credentials(user: str, password: str) -> bool:
    if not settings.app_password_hash:
        return False
    # compare_digest en el usuario tambien: evita filtrar por timing si en algun momento
    # el usuario se vuelve un dato menos trivial de adivinar que "admin".
    return hmac.compare_digest(user.strip(), settings.app_user) and verify_password(password, settings.app_password_hash)


def is_locked_out() -> tuple[bool, int]:
    """(bloqueado, segundos que faltan). De paso, descarta intentos ya fuera de la ventana."""
    now = time.time()
    while _failed_attempts and now - _failed_attempts[0] >= LOCKOUT_SECONDS:
        _failed_attempts.pop(0)
    if len(_failed_attempts) >= MAX_ATTEMPTS:
        return True, int(LOCKOUT_SECONDS - (now - _failed_attempts[0]))
    return False, 0


async def register_failed_attempt() -> None:
    from app.live.alerts import send_alert  # import diferido: evita un ciclo con app.config en tests puros

    _failed_attempts.append(time.time())
    logger.warning("Intento de login fallido (%d en la ventana actual)", len(_failed_attempts))
    if len(_failed_attempts) == MAX_ATTEMPTS:
        await send_alert(f"🔒 {MAX_ATTEMPTS} intentos de login fallidos seguidos: dashboard bloqueado {LOCKOUT_SECONDS // 60} minutos.")


def register_successful_login() -> None:
    _failed_attempts.clear()


class AuthMiddleware(BaseHTTPMiddleware):
    """Exige sesion autenticada para cualquier pagina de NiceGUI salvo /login. No toca rutas que
    no sean paginas (assets, websocket de socket.io, etc.), asi que el login mismo puede cargar
    su JS/CSS y usar la conexion en tiempo real sin quedar bloqueado por si mismo."""

    async def dispatch(self, request: Request, call_next):
        if not nicegui_app.storage.user.get("authenticated", False):
            path = request.url.path
            if path in Client.page_routes.values() and path not in UNRESTRICTED_PAGE_ROUTES:
                nicegui_app.storage.user["redirect_to"] = path
                return RedirectResponse("/login")
        return await call_next(request)
