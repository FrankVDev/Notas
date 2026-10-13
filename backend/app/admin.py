"""Administración de cuentas desde la terminal.

Uso (desde la carpeta del proyecto):
    docker compose exec api python -m app.admin usuarios
    docker compose exec api python -m app.admin sesiones ana
    docker compose exec api python -m app.admin crear ana
    docker compose exec api python -m app.admin restablecer ana
    docker compose exec api python -m app.admin cerrar-sesiones ana
    docker compose exec api python -m app.admin borrar ana

La lógica vive en admin_service.py; este archivo solo lee argumentos y
muestra resultados.
"""
import argparse
import getpass
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from . import admin_service as svc
from .models import SessionLocal

TZ = ZoneInfo(os.environ.get("APP_TZ", "America/Lima"))


def fmt(dt: datetime | None) -> str:
    return dt.astimezone(TZ).strftime("%d/%m/%Y %H:%M") if dt else "-"


def table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))


def ask_password(prompt: str = "Contraseña nueva: ") -> str:
    first = getpass.getpass(prompt)
    if getpass.getpass("Repítela: ") != first:
        raise svc.AdminError("Las contraseñas no coinciden.")
    return first


def confirm(expected: str, message: str) -> bool:
    print(message)
    return input(f'Escribe "{expected}" para confirmar: ').strip().lower() == expected.lower()


# ---------- Subcomandos ----------

def cmd_usuarios(db, args) -> None:
    users = svc.list_users(db)
    if not users:
        print("Todavía no hay cuentas. La primera que se cree en la app recibirá las notas existentes.")
        return
    table(
        ["ID", "Usuario", "Creado", "En cola", "Completadas", "Sesiones", "Último ingreso"],
        [[str(u.id), u.username, fmt(u.created_at), str(u.notes_active), str(u.notes_done),
          str(u.sessions), fmt(u.last_login)] for u in users],
    )
    print(f"\n{len(users)} cuenta(s).")


def cmd_sesiones(db, args) -> None:
    sessions = svc.list_sessions(db, args.usuario)
    if not sessions:
        print(f"{args.usuario} no tiene sesiones abiertas.")
        return
    table(["ID", "Inicio", "Vence", "Dispositivo"],
          [[str(s.id), fmt(s.created_at), fmt(s.expires_at), (s.device or "-")[:70]] for s in sessions])


def cmd_crear(db, args) -> None:
    password = args.contrasena or (svc.generate_password() if args.generar else ask_password("Contraseña: "))
    user = svc.create_user(db, args.usuario, password)
    db.commit()
    print(f"Cuenta creada: {user.username}")
    if args.generar:
        print(f"Contraseña: {password}")


def cmd_restablecer(db, args) -> None:
    custom = args.contrasena or (ask_password() if args.elegir else None)
    password, closed = svc.reset_password(db, args.usuario, custom)
    print(f"Contraseña de {svc.get_user(db, args.usuario).username} restablecida. Sesiones cerradas: {closed}.")
    if not custom:
        print(f"Contraseña temporal: {password}")
        print("Pídele que la cambie desde el menú: Cambiar contraseña.")


def cmd_cerrar_sesiones(db, args) -> None:
    closed = svc.end_sessions(db, args.usuario)
    print(f"Sesiones cerradas: {closed}. Deberá iniciar sesión de nuevo en cada dispositivo.")


def cmd_borrar(db, args) -> None:
    user = svc.get_user(db, args.usuario)
    total = next((u.notes_total for u in svc.list_users(db) if u.id == user.id), 0)
    if not args.confirmar and not confirm(
        user.username,
        f"Se borrará la cuenta {user.username} y sus {total} nota(s). No se puede deshacer "
        "(salvo restaurando un respaldo).",
    ):
        print("Cancelado. No se borró nada.")
        return
    deleted = svc.delete_user(db, user.username)
    print(f"Cuenta {user.username} borrada junto con {deleted} nota(s).")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.admin", description="Administración de cuentas de Notas.")
    sub = p.add_subparsers(dest="comando", required=True, metavar="comando")

    sub.add_parser("usuarios", help="lista las cuentas con sus notas y sesiones").set_defaults(fn=cmd_usuarios)

    s = sub.add_parser("sesiones", help="muestra los dispositivos con sesión abierta de un usuario")
    s.add_argument("usuario")
    s.set_defaults(fn=cmd_sesiones)

    s = sub.add_parser("crear", help="crea una cuenta (útil con ALLOW_SIGNUP=false)")
    s.add_argument("usuario")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--generar", action="store_true", help="genera una contraseña aleatoria y la muestra")
    g.add_argument("--contrasena", help="contraseña (si no, se pide sin mostrarla)")
    s.set_defaults(fn=cmd_crear)

    s = sub.add_parser("restablecer", help="contraseña temporal nueva y cierra todas sus sesiones")
    s.add_argument("usuario")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--elegir", action="store_true", help="escribir la contraseña en lugar de generarla")
    g.add_argument("--contrasena", help="contraseña nueva")
    s.set_defaults(fn=cmd_restablecer)

    s = sub.add_parser("cerrar-sesiones", help="obliga al usuario a iniciar sesión de nuevo")
    s.add_argument("usuario")
    s.set_defaults(fn=cmd_cerrar_sesiones)

    s = sub.add_parser("borrar", help="borra la cuenta y TODAS sus notas")
    s.add_argument("usuario")
    s.add_argument("--confirmar", action="store_true", help="no preguntar (para scripts)")
    s.set_defaults(fn=cmd_borrar)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with SessionLocal() as db:
        try:
            args.fn(db, args)
        except svc.AdminError as e:
            print(f"Error: {e.message}", file=sys.stderr)
            return 1
        except (KeyboardInterrupt, EOFError):
            print("\nCancelado.")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
