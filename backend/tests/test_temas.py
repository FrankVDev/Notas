"""El catálogo de temas (base de datos) y su diseño (temas.css) deben coincidir."""
import importlib.util
import re
from pathlib import Path

import pytest

from app import admin_service

BACKEND = Path(__file__).resolve().parents[1]
TEMAS_CSS = BACKEND.parent / "frontend" / "public" / "temas.css"


def temas_iniciales() -> list[dict]:
    path = BACKEND / "app" / "migrations" / "versions" / "0004_temas.py"
    spec = importlib.util.spec_from_file_location("migracion_0004", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TEMAS_INICIALES


@pytest.mark.skipif(not TEMAS_CSS.exists(), reason="el frontend no está junto al backend")
def test_cada_tema_del_catalogo_tiene_su_css():
    css = TEMAS_CSS.read_text(encoding="utf-8")
    con_css = set(re.findall(r'body\[data-tema="([a-z0-9-]+)"\]', css))
    for tema in temas_iniciales():
        assert tema["clave"] in con_css, f'Falta el bloque body[data-tema="{tema["clave"]}"] en temas.css'


def test_claves_iniciales_validas():
    claves = [t["clave"] for t in temas_iniciales()]
    assert len(claves) == len(set(claves))
    for clave in claves:
        assert admin_service.THEME_KEY_RE.match(clave)


@pytest.mark.parametrize("clave,valida", [
    ("nube", True), ("cielo-noche", True), ("tema2", True),
    ("x", False), ("Nube", False), ('nube"]', False), ("con espacio", False), ("a" * 31, False),
])
def test_formato_de_clave(clave, valida):
    assert bool(admin_service.THEME_KEY_RE.match(clave)) is valida
