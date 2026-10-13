# Notas en cola

App web sencilla para escribir notas que funcionan como comandas de cocina: la primera que entra es la primera que sale. Todo corre en Docker y se abre desde el navegador.

## Arrancar

Requisitos: Docker Desktop (o Docker Engine con el plugin `compose`).

```bash
# 1. (opcional) cambia la clave de la base en .env
# 2. levanta todo
docker compose up --build -d
```

Abre **http://localhost:8080**. La documentación interactiva de la API está en http://localhost:8080/api/docs.

Comandos útiles:

```bash
docker compose ps            # ver el estado de los contenedores
docker compose logs -f api   # ver los logs del backend
docker compose down          # apagar (las notas se conservan)
docker compose down -v       # apagar y BORRAR todas las notas
```

## Formato de una nota

Pulsa **+** (o la tecla N) y escribe libremente. Cada línea se interpreta así:

| Empieza con | Significa |
|---|---|
| `Titulo:` | Título de la nota (si falta, se usa la primera línea) |
| `#` | Pendiente |
| `@` | Etiqueta |
| `$` | Prioridad: Alta, Media o Baja |
| cualquier otra cosa | Descripción |

```
Titulo: Reunion Con Edu Rosales
Reunion sobre contingencia, pendientes de planillas y Federacion

#enviar correo a john obregon
#terminar de procesar las planillas

@Apolo

$Alta
```

Los botones **# Pendiente**, **@ Etiqueta** y **$ Prioridad** del editor insertan el símbolo al inicio de una línea (útil en el celular). Al escribir `@` se sugieren las etiquetas que ya usaste, y al escribir `$` las prioridades; **Tab** usa la primera sugerencia. Ctrl + Enter guarda la nota.

## Buscar y filtrar

- El campo **Buscar notas** (atajo: tecla `/`) encuentra notas por título, descripción, pendientes o etiquetas, sin distinguir mayúsculas ni tildes. Escape borra la búsqueda.
- La dirección del navegador refleja la vista actual, por ejemplo `?etiqueta=Apolo&buscar=planillas`. Al recargar no se pierde y puedes guardarla como marcador.

## Cómo funciona la cola

- Las notas se muestran en orden de llegada; la más antigua lleva la marca **Sigue**.
- Una nota sale de la cola automáticamente cuando marcas todos sus pendientes.
- Si una nota no tiene pendientes, se completa con el botón **Completar**.
- Al desmarcar un pendiente o agregar uno nuevo al editar, la nota vuelve a la cola en su lugar original (conserva su fecha de llegada).
- El calendario filtra por fecha de creación; los días con un punto tienen notas.
- Los tickets empiezan contraídos. Tocar el recuadro **Pendientes** muestra la lista para marcarlos ahí mismo; tocar cualquier otra parte del ticket abre el detalle.
- Al completar o eliminar una nota aparece un aviso con **Deshacer** durante unos segundos. La nota eliminada se borra de verdad cuando ese aviso desaparece.
- En el celular, el calendario y los filtros están en el panel que abre el botón **Filtros**.

## Sincronización entre dispositivos

Todas las pantallas abiertas (PC, celular, otras pestañas) se actualizan solas en menos de un segundo cuando algo cambia en otra. No hace falta recargar.

- El servidor avisa de cada cambio por una conexión abierta (Server-Sent Events en `/api/events`) y cada pantalla vuelve a pedir lo que necesita. Cada pestaña ignora los avisos de sus propios cambios.
- Los tickets que cambiaron en otro dispositivo se resaltan un momento.
- Si estás editando una nota y alguien la modifica o la borra en otro dispositivo, el editor te avisa antes de que guardes.
- Si la conexión se corta, aparece "Sin conexión en vivo. Reintentando…" y se reconecta sola. Como respaldo, la pantalla también se actualiza al volver a la pestaña y cada 30 segundos.
- Los avisos viven en la memoria del backend, por eso la API debe correr con un solo proceso de uvicorn (como está configurada).

## Arquitectura

```
navegador ──► web (Nginx :8080) ──/api──► api (FastAPI :8000) ──► db (PostgreSQL 16)
                 sirve el frontend                                    └─ volumen notas_data
```

```
.
├── docker-compose.yml
├── .env                    # credenciales y puerto (no subir a git)
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py         # endpoints REST
│   │   ├── models.py       # tablas notes y tasks
│   │   ├── events.py       # avisos en vivo entre dispositivos
│   │   └── parser.py       # interpreta el texto con #, @ y $
│   └── tests/test_parser.py
└── frontend/
    ├── Dockerfile
    ├── nginx.conf          # sirve la web y redirige /api al backend
    └── public/             # index.html, styles.css, app.js (sin build)
```

### Endpoints

| Método | Ruta | Uso |
|---|---|---|
| GET | `/api/notes?status=active\|done\|all&tag=&priority=&date=YYYY-MM-DD&q=` | Listar notas (`q` busca texto) |
| POST | `/api/notes` `{raw}` | Crear nota desde el texto |
| GET / PUT / DELETE | `/api/notes/{id}` | Ver, editar (re-interpreta el texto) o borrar |
| PATCH | `/api/tasks/{id}` `{done}` | Marcar o desmarcar un pendiente |
| POST | `/api/notes/{id}/complete` y `/reopen` | Completar o devolver a la cola (solo notas sin pendientes) |
| POST | `/api/parse` `{raw}` | Vista previa sin guardar |
| GET | `/api/meta?month=YYYY-MM` | Etiquetas, prioridades y días con notas |
| GET | `/api/events` | Avisos en vivo de cambios (Server-Sent Events) |

## Desarrollo

Los tests del parser se corren sin Docker:

```bash
cd backend
pip install -r requirements.txt pytest
python -m pytest
```

Si cambias el frontend o el backend, vuelve a ejecutar `docker compose up --build -d`.

La zona horaria para el filtro por fecha se configura con `APP_TZ` en `.env` (por defecto `America/Lima`).
