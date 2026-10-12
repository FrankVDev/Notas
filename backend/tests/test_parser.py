from app.parser import parse_note

EJEMPLO = """Titulo: Reunion Con Edu Rosales
Reunion sobre contingencia, pendientes de planillas y Federacion

#enviar correo a john obregon
#terminar de procesar las planillas

@Apolo 

$Alta
"""


def test_ejemplo_clasico():
    n = parse_note(EJEMPLO)
    assert n.title == "Reunion Con Edu Rosales"
    assert n.description == "Reunion sobre contingencia, pendientes de planillas y Federacion"
    assert n.tasks == ["enviar correo a john obregon", "terminar de procesar las planillas"]
    assert n.tags == ["Apolo"]
    assert n.priority == "alta"
    assert n.warnings == []


def test_sin_linea_titulo_usa_primera_linea():
    n = parse_note("Comprar materiales\n#cables\n$baja")
    assert n.title == "Comprar materiales"
    assert n.tasks == ["cables"]
    assert n.priority == "baja"


def test_etiquetas_sin_duplicados_y_prioridad_desconocida():
    n = parse_note("Título: X\n@Apolo\n@apolo\n@Federación\n$urgente")
    assert n.tags == ["Apolo", "Federación"]
    assert n.priority == "urgente"
    assert n.warnings
