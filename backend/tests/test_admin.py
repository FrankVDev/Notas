import re

import pytest

from app import admin, admin_service


def test_contrasena_generada_es_valida_y_legible():
    pwds = {admin_service.generate_password() for _ in range(200)}
    assert len(pwds) == 200  # no se repiten
    for p in pwds:
        assert re.fullmatch(r"[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}", p)
        assert not set("01lo") & set(p)  # sin caracteres que se confunden


def test_comandos_disponibles():
    parser = admin.build_parser()
    for argv in (["usuarios"], ["sesiones", "ana"], ["crear", "ana", "--generar"],
                 ["restablecer", "ana"], ["cerrar-sesiones", "ana"], ["borrar", "ana", "--confirmar"]):
        assert parser.parse_args(argv).fn
    with pytest.raises(SystemExit):
        parser.parse_args(["crear", "ana", "--generar", "--contrasena", "x"])  # opciones excluyentes
