# Notas en cola

App web sencilla para escribir notas que funcionan como comandas de cocina: la primera que entra es la primera que sale. Todo corre en Docker y se abre desde el navegador.

## Arrancar

Requisitos: Docker Desktop (o Docker Engine con el plugin `compose`).

```bash
# 1. (opcional) cambia la clave de la base en .env
# 2. levanta todo
docker compose up --build -d
```

Abre **http://localhost:8080** y crea tu cuenta. **La primera cuenta que se crea recibe todas las notas que ya existían**, así que crea la tuya antes que nadie. La documentación interactiva de la API está en http://localhost:8080/api/docs.

Para instalarla en un servidor Rocky Linux detrás de un proxy inverso, con nombre propio (`http://notas.home.arpa`), acceso por SSH y usuarios compartidos, sigue la guía [docs/rocky-linux.md](docs/rocky-linux.md).

### Dos formas de ejecutarla

| Modo | Dirección | Cómo |
|---|---|---|
| **Directo** (por defecto) | `http://IP:8080` | Tal cual; el puerto se cambia con `WEB_PORT` |
| **Detrás de Caddy** | `http://notas.home.arpa` | En `.env`: `COMPOSE_FILE=docker-compose.yml:docker-compose.proxy.yml` (en Windows, `;` en lugar de `:`). Requiere el repositorio de infraestructura con Caddy |

En modo proxy, el contenedor `web` no publica ningún puerto y se une a la red compartida `proxy` con el nombre `notas-web`. La app recibe la IP real de cada visitante (para el límite de intentos de inicio de sesión) solo si la conexión viene de esa red, así que nadie puede falsearla.

Comandos útiles:

```bash
docker compose ps            # ver el estado de los contenedores
docker compose logs -f api   # ver los logs del backend
docker compose down          # apagar (las notas se conservan)
docker compose down -v       # apagar y BORRAR todas las notas
```

## Cuentas

- Cada usuario tiene su propio tablero: solo ve y modifica sus notas, y los avisos en vivo solo llegan a sus dispositivos.
- La sesión dura 30 días por dispositivo y se renueva sola mientras uses la app. **Cerrar sesión** y **Cambiar contraseña** están al final del menú; cambiar la contraseña cierra la sesión en los demás dispositivos.
- Las contraseñas se guardan con hash scrypt y las sesiones como hash SHA-256: una copia de la base no permite entrar.
- Tras 5 intentos fallidos de inicio de sesión desde la misma IP para un usuario, hay que esperar 10 minutos.
- Para que nadie más pueda crear cuentas, pon `ALLOW_SIGNUP=false` en `.env` y ejecuta `docker compose up -d`.
- La app funciona por http en la red local, así que las contraseñas viajan sin cifrar dentro de tu wifi. Con Caddy se puede activar https gratis (ver el repositorio de infraestructura); en ese caso pon `COOKIE_SECURE=true`. Dentro de Tailscale el tráfico ya va cifrado.

## Administrar cuentas

Desde la carpeta del proyecto, en la PC donde corre Docker:

| Comando | Qué hace |
|---|---|
| `docker compose exec api python -m app.admin usuarios` | Lista las cuentas con sus notas en cola y completadas, sesiones abiertas y último ingreso |
| `docker compose exec api python -m app.admin sesiones ana` | Muestra en qué dispositivos tiene sesión abierta |
| `docker compose exec api python -m app.admin crear ana` | Crea una cuenta (pide la contraseña; con `--generar` crea una aleatoria). Útil con `ALLOW_SIGNUP=false` |
| `docker compose exec api python -m app.admin restablecer ana` | Para una contraseña olvidada: genera una temporal, la muestra y cierra todas sus sesiones (`--elegir` para escribirla) |
| `docker compose exec api python -m app.admin cerrar-sesiones ana` | Obliga a iniciar sesión de nuevo en todos sus dispositivos |
| `docker compose exec api python -m app.admin borrar ana` | Borra la cuenta y **todas sus notas** (pide escribir el nombre para confirmar) |

La lógica está en `backend/app/admin_service.py`, separada del comando (`admin.py`), para que una futura vista de administrador en la app pueda usar las mismas funciones desde la API.

## Respaldo automático

El servicio `backup` saca una copia de la base al arrancar y luego cada 24 horas, en la carpeta `respaldos/` del proyecto (por ejemplo `notas-2026-10-13_0300.sql.gz`), y conserva las 14 más recientes. Se ajusta con `BACKUP_INTERVAL_HOURS` y `BACKUP_KEEP` en `.env`. La carpeta no se sube a git.

Para restaurar un respaldo (reemplaza todas las notas actuales):

```bash
docker compose stop api
docker compose exec db psql -U notas -d postgres -c "DROP DATABASE notas WITH (FORCE)" -c "CREATE DATABASE notas"
docker compose cp respaldos/notas-2026-10-13_0300.sql.gz db:/tmp/r.sql.gz
docker compose exec db sh -c "gunzip -c /tmp/r.sql.gz | psql -U notas -d notas"
docker compose start api
```

## Formato de una nota

Pulsa **+** (o la tecla N) y escribe libremente. Cada línea se interpreta así:

| Empieza con | Significa |
|---|---|
| `Titulo:` | Título de la nota (si falta, se usa la primera línea) |
| `#` | Pendiente. Con `>Nombre` al final se asigna un responsable: `#enviar correo >Lucía` |
| `@` | Etiqueta |
| `$` | Prioridad: Alta, Media o Baja |
| `!` | Fecha límite: `!15/10`, `!15/10/2026`, `!hoy`, `!mañana`, `!viernes` |
| cualquier otra cosa | Descripción |

```
Titulo: Reunion Con Edu Rosales
Reunion sobre contingencia, pendientes de planillas y Federacion

#enviar correo a john obregon >Lucía
#terminar de procesar las planillas

@Apolo
$Alta
!viernes
```

Las fechas relativas (`!hoy`, `!mañana`, `!viernes`) y las fechas sin año se guardan como fecha completa (`!17/10/2026`), para que no cambien al editar la nota otro día. Una fecha sin año que ya pasó hace poco se toma como vencida; si pasó hace más de 3 meses, se asume del año siguiente.

Los botones **# Pendiente**, **@ Etiqueta** y **$ Prioridad** del editor insertan el símbolo al inicio de una línea (útil en el celular). Al escribir `@` se sugieren las etiquetas que ya usaste, y al escribir `$` las prioridades; **Tab** usa la primera sugerencia. Ctrl + Enter guarda la nota.

## Fecha límite

- En cola, el ticket muestra "Vence el 20 oct", "Vence mañana" o "Vence hoy" (en ámbar) y "Vencida hace 3 días" (en rojo, con una franja roja arriba del ticket).
- Las notas completadas muestran "Cumplida a tiempo" o "Cerrada con N días de retraso". No se marcan como vencidas porque ya están resueltas.
- La fecha vence al terminar ese día en la zona horaria de `APP_TZ`.
- En el calendario, los días con un cuadrito rojo tienen vencimientos de notas en cola. Al tocar un día se ven las notas creadas o que vencen ese día.

## Exportar

En el detalle de una nota, **Copiar texto** la copia lista para pegar en un correo o documento, y **Descargar .md** la guarda como archivo Markdown.

## Buscar y filtrar

- El campo **Buscar notas** (atajo: tecla `/`) encuentra notas por título, descripción, pendientes, responsables o etiquetas, sin distinguir mayúsculas ni tildes. Escape borra la búsqueda.
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
                 sirve el frontend                                    ├─ volumen notas_data
                                                       backup ────────┘─► carpeta respaldos/
```

```
.
├── docker-compose.yml
├── docker-compose.proxy.yml # modo detrás de Caddy (opcional)
├── docs/rocky-linux.md     # instalación en un servidor Rocky Linux
├── .env                    # credenciales y puerto (no subir a git)
├── backup/backup.sh        # respaldo automático
├── respaldos/              # copias de la base (no se sube a git)
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py         # endpoints REST
│   │   ├── models.py       # tablas notes y tasks
│   │   ├── migrate.py      # aplica las migraciones al arrancar
│   │   ├── migrations/     # migraciones de Alembic
│   │   ├── events.py       # avisos en vivo entre dispositivos
│   │   ├── auth.py         # cuentas, contraseñas y sesiones
│   │   ├── admin_service.py # operaciones de administración (reutilizables)
│   │   ├── admin.py        # comando de terminal: python -m app.admin
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
| GET | `/api/notes?status=active\|done\|all&tag=&priority=&assignee=&date=YYYY-MM-DD&q=` | Listar notas (`q` busca texto; `date` coincide con creación o vencimiento) |
| POST | `/api/notes` `{raw}` | Crear nota desde el texto |
| GET / PUT / DELETE | `/api/notes/{id}` | Ver, editar (re-interpreta el texto) o borrar |
| PATCH | `/api/tasks/{id}` `{done}` | Marcar o desmarcar un pendiente |
| POST | `/api/notes/{id}/complete` y `/reopen` | Completar o devolver a la cola (solo notas sin pendientes) |
| POST | `/api/parse` `{raw}` | Vista previa sin guardar |
| GET | `/api/meta?month=YYYY-MM` | Etiquetas, prioridades, responsables, días con notas y días con vencimientos |
| GET | `/api/events` | Avisos en vivo de cambios (Server-Sent Events) |
| GET | `/api/auth/config` | Si se pueden crear cuentas y si ya existe alguna |
| POST | `/api/auth/register`, `/api/auth/login`, `/api/auth/logout` | Crear cuenta, entrar y salir (`{username, password}`) |
| GET | `/api/auth/me` | Usuario de la sesión actual |
| POST | `/api/auth/password` `{current, new}` | Cambiar contraseña |

Todos los endpoints de notas requieren sesión; una nota de otro usuario responde 404 como si no existiera.

## Migraciones de la base de datos

La estructura de la base la maneja [Alembic](https://alembic.sqlalchemy.org). Al arrancar, la API aplica sola las migraciones pendientes de `backend/app/migrations/versions`, así que al hacer `git pull` y `docker compose up --build -d` la base se actualiza sin perder notas. Una base creada por una versión anterior (sin Alembic) se reconoce y se actualiza igual.

Para cambiar la estructura (agregar un campo, por ejemplo):

1. Modifica el modelo en `backend/app/models.py`.
2. Genera la migración comparando el modelo con la base:
   ```bash
   docker compose exec api alembic revision --autogenerate -m "describe el cambio"
   docker compose cp api:/app/app/migrations/versions ./backend/app/migrations/
   ```
3. Revisa el archivo generado, súbelo con git y reconstruye.

Otros comandos útiles: `docker compose exec api alembic current` (versión actual) y `docker compose exec api alembic downgrade -1` (deshacer la última migración). Antes de migrar una base con notas importantes, haz un respaldo con `pg_dump`.

## Desarrollo

Los tests del parser se corren sin Docker:

```bash
cd backend
pip install -r requirements.txt pytest
python -m pytest
```

Si cambias el frontend o el backend, vuelve a ejecutar `docker compose up --build -d`.

La zona horaria para el filtro por fecha se configura con `APP_TZ` en `.env` (por defecto `America/Lima`).
