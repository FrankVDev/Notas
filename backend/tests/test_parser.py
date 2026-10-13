from datetime import date

from app.parser import parse_note

HOY = date(2026, 10, 13)  # martes

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
    assert [t.text for t in n.tasks] == ["enviar correo a john obregon", "terminar de procesar las planillas"]
    assert all(t.assignee is None for t in n.tasks)
    assert n.due_date is None
    assert n.tags == ["Apolo"]
    assert n.priority == "alta"
    assert n.warnings == []


def test_sin_linea_titulo_usa_primera_linea():
    n = parse_note("Comprar materiales\n#cables\n$baja")
    assert n.title == "Comprar materiales"
    assert [t.text for t in n.tasks] == ["cables"]
    assert n.priority == "baja"


def test_etiquetas_sin_duplicados_y_prioridad_desconocida():
    n = parse_note("Título: X\n@Apolo\n@apolo\n@Federación\n$urgente")
    assert n.tags == ["Apolo", "Federación"]
    assert n.priority == "urgente"
    assert n.warnings


def test_fechas_absolutas_y_sin_anio():
    assert parse_note("X\n!15/10/2026", HOY).due_date == date(2026, 10, 15)
    assert parse_note("X\n!15/10/26", HOY).due_date == date(2026, 10, 15)
    assert parse_note("X\n!15-10", HOY).due_date == date(2026, 10, 15)
    # sin año y pasada hace poco: queda este año (vencida)
    assert parse_note("X\n!01/10", HOY).due_date == date(2026, 10, 1)
    # sin año y pasada hace mucho: es del año siguiente
    assert parse_note("X\n!05/01", HOY).due_date == date(2027, 1, 5)


def test_fechas_relativas():
    assert parse_note("X\n!hoy", HOY).due_date == HOY
    assert parse_note("X\n!mañana", HOY).due_date == date(2026, 10, 14)
    assert parse_note("X\n!Manana", HOY).due_date == date(2026, 10, 14)
    assert parse_note("X\n!viernes", HOY).due_date == date(2026, 10, 16)
    assert parse_note("X\n!martes", HOY).due_date == HOY          # hoy es martes
    assert parse_note("X\n!miércoles", HOY).due_date == date(2026, 10, 14)


def test_la_fecha_se_guarda_completa():
    n = parse_note("Titulo: X\n  !mañana\n#algo", HOY)
    assert n.normalized_raw == "Titulo: X\n  !14/10/2026\n#algo"
    # volver a interpretarla otro día no cambia la fecha
    assert parse_note(n.normalized_raw, date(2026, 12, 1)).due_date == date(2026, 10, 14)


def test_fecha_invalida_avisa_y_se_ignora():
    n = parse_note("X\n!31/02\n!el lunes que viene", HOY)
    assert n.due_date is None
    assert len(n.warnings) == 2


def test_responsables():
    n = parse_note("X\n#enviar correo >Lucía\n#llamar a John Obregón >John Obregón\n#comparar a > b\n#sin responsable", HOY)
    assert [(t.text, t.assignee) for t in n.tasks] == [
        ("enviar correo", "Lucía"),
        ("llamar a John Obregón", "John Obregón"),
        ("comparar a > b", None),   # ">" seguido de espacio no es responsable
        ("sin responsable", None),
    ]
