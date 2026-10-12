"""Convierte el texto libre del bloc de notas en una nota estructurada.

Reglas (una por línea):
    Titulo: ...   -> título de la nota (también acepta "Título:")
    # texto       -> pendiente
    @ texto       -> etiqueta
    $ texto       -> prioridad (Alta, Media, Baja)
    cualquier otra línea -> descripción

Si no hay línea "Titulo:", la primera línea de descripción pasa a ser el título.
"""
import re
from dataclasses import dataclass, field

TITLE_RE = re.compile(r"^t[ií]tulo\s*:\s*(.*)$", re.IGNORECASE)

PRIORITIES = {
    "alta": "alta", "high": "alta",
    "media": "media", "medio": "media", "medium": "media",
    "baja": "baja", "bajo": "baja", "low": "baja",
}


@dataclass
class ParsedNote:
    title: str = ""
    description: str = ""
    tasks: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    priority: str | None = None
    warnings: list[str] = field(default_factory=list)


def parse_note(raw: str) -> ParsedNote:
    note = ParsedNote()
    desc: list[str] = []
    title: str | None = None

    for line in raw.splitlines():
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
            note.tasks.append(value)
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
    return note
