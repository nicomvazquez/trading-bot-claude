"""Genera el hash de la contrasena de login del dashboard, sin que la contrasena en texto plano
quede guardada ni impresa en ningun lado (se lee con getpass, que no la muestra en pantalla).

Uso:
    python -m app.tools.set_password
    (o dentro de Docker: docker compose exec app python -m app.tools.set_password)

Pega las dos lineas que imprime al final en tu archivo .env y reiniciá la app
(`docker compose up -d`, un simple restart no relee el archivo)."""

import getpass

from app.auth import hash_password


def main() -> None:
    user = input("Usuario para el dashboard [admin]: ").strip() or "admin"
    while True:
        password = getpass.getpass("Contraseña nueva: ")
        if len(password) < 8:
            print("Usá al menos 8 caracteres.")
            continue
        confirm = getpass.getpass("Repetila: ")
        if password != confirm:
            print("No coinciden, probá de nuevo.")
            continue
        break

    print("\nPegá estas líneas en tu archivo .env y reiniciá la app (docker compose up -d):\n")
    print(f"APP_USER={user}")
    print(f"APP_PASSWORD_HASH={hash_password(password)}")


if __name__ == "__main__":
    main()
