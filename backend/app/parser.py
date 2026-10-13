"""Convierte el texto libre del bloc de notas en una nota estructurada.

Reglas (una por línea):
    Titulo: ...   -> título de la nota (también acepta "Título:")
    # texto       -> pendiente; "#texto >Nombre" le asigna un responsable
    @ texto       -> etiqueta
    $ texto       -> prioridad (Alta, Media, Baja)
    ! fecha       -> fecha límite: 15/10, 15/10/2026, hoy, mañana, viernes...
    cualquier otra línea -> descripción

Las fechas relativas (hoy, mañana, viernes) y las fechas sin año se
convierten a una fecha completa en `normalized_raw`, que es el texto que
se guarda. Así la fecha no cambia cada vez que se vuelve a interpretar.

Si no hay línea "Titulo:", la primera línea de descripción pasa a ser el título.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

TITLE_RE = re.compile(r"^t[ií]tulo\s*:\s*(.*)$", re.IGNORECASE)

PRIORITIES = {
    "alta": "alta", "high": "alta",
    "media": "media", "medio": "media", "medium": "media",
    "baja": "baja", "bajo": "baja", "low": "baja",
}


WEEKDAYS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
DATE_RE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2}|\d{4}))?$")
# Responsable al final de un pendiente: "...texto >Nombre" (">" pegado al nombre)
ASSIGNEE_RE = re.compile(r"^(.*\S)\s+>(\S.*)$")
# Una fecha sin año que pasó hace más de esto se asume del año siguiente
PAST_TOLERANCE = timedelta(days=90)


def fold(text: str) -> str:
    """minúsculas y sin tildes, para comparar palabras como 'mañana' o 'miércoles'"""
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )


def parse_due(value: str, today: date) -> date | None:
    v = fold(value.strip())
    if v == "hoy":
        return today
    if v in ("manana", "mañana"):
        return today + timedelta(days=1)
    if v == "pasado manana":
        return today + timedelta(days=2)
    if v in WEEKDAYS:
        # el próximo día con ese nombre (hoy, si coincide)
        return today + timedelta(days=(WEEKDAYS.index(v) - today.weekday()) % 7)
    m = DATE_RE.match(v)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
    try:
        if year:
            y = int(year) + (2000 if len(year) == 2 else 0)
            return date(y, month, day)
        candidate = date(today.year, month, day)
        if candidate < today - PAST_TOLERANCE:
            candidate = date(today.year + 1, month, day)
        return candidate
    except ValueError:  # 31/02, 15/13...
        return None


def format_due(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


@dataclass
class ParsedTask:
    text: str
    assignee: str | None = None


@dataclass
class ParsedNote:
    title: str = ""
    description: str = ""
    tasks: list[ParsedTask] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    priority: str | None = None
    due_date: date | None = None
    normalized_raw: str = ""
    warnings: list[str] = field(default_factory=list)


def parse_note(raw: str, today: date | None = None) -> ParsedNote:
    today = today or date.today()
    note = ParsedNote()
    desc: list[str] = []
    title: str | None = None
    out_lines: list[str] = []  # texto a guardar, con las fechas ya completas

    for line in raw.splitlines():
        out_lines.append(line)
        s = line.strip()

        if not s:
            # conserva un solo salto de párrafo dentro de la descripción
            if desc and desc[-1] != "":
                desc.append("")
            continue

        m = TITLE_RE.match(s)
        if m and title is None:
            title = m.group(1).strip()
            continue

        marker, value = s[0], s[1:].strip()

        if marker == "#" and value:
            m = ASSIGNEE_RE.match(value)
            if m:
                note.tasks.append(ParsedTask(m.group(1).strip(), m.group(2).strip()[:100]))
            else:
                note.tasks.append(ParsedTask(value))
        elif marker == "@" and value:
            if value.lower() not in (t.lower() for t in note.tags):
                note.tags.append(value)
        elif marker == "$" and value:
            key = value.lower()
            if key in PRIORITIES:
                note.priority = PRIORITIES[key]
            else:
                note.priority = key
                note.warnings.append(
                    f'La prioridad "{value}" no es Alta, Media ni Baja; se guardará tal cual.'
                )
        elif marker == "!" and value:
            due = parse_due(value, today)
            if due is None:
                note.warnings.append(
                    f'No entendí la fecha "{value}". Usa por ejemplo !15/10, !15/10/2026, !mañana o !viernes.'
                )
            else:
                note.due_date = due
                indent = line[: len(line) - len(line.lstrip())]
                out_lines[-1] = f"{indent}!{format_due(due)}"
        else:
            desc.append(s)

    while desc and desc[-1] == "":
        desc.pop()

    if not title:
        if desc:
            title = desc.pop(0)
            while desc and desc[0] == "":
                desc.pop(0)
        else:
            title = "Sin título"
            note.warnings.append('Falta el título: escribe una línea "Titulo: ..."')

    note.title = title[:300]
    note.description = "\n".join(desc)
    note.normalized_raw = "\n".join(out_lines)
    return note
