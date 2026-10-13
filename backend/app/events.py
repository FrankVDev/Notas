"""Avisos en vivo (Server-Sent Events).

Cada pantalla abierta se suscribe a /api/events. Cuando una nota cambia,
el backend publica un aviso corto ("la nota 5 se actualizó") y cada
pantalla vuelve a pedir los datos que necesita. Los avisos solo llegan a
las pantallas del mismo usuario.

Los suscriptores viven en memoria, así que esto funciona con un solo
proceso de uvicorn (el valor por defecto). Si algún día se usan varios
workers, habría que cambiar a LISTEN/NOTIFY de PostgreSQL.
"""
import asyncio
import json


class Broadcaster:
    def __init__(self) -> None:
        self._subscribers: dict[asyncio.Queue[str], int] = {}  # cola -> id de usuario
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, user_id: int) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self._subscribers[queue] = user_id
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.pop(queue, None)

    @property
    def count(self) -> int:
        return len(self._subscribers)

    def publish(self, user_id: int, op: str, note_id: int, origin: str | None) -> None:
        """Se puede llamar desde los endpoints síncronos (corren en otro hilo)."""
        if self._loop is None:
            return
        data = json.dumps({"op": op, "id": note_id, "origin": origin})

        def deliver() -> None:
            for queue, owner in list(self._subscribers.items()):
                if owner != user_id:
                    continue
                try:
                    queue.put_nowait(data)
                except asyncio.QueueFull:
                    pass  # una pantalla muy atrasada se pondrá al día al reconectar

        self._loop.call_soon_threadsafe(deliver)


broker = Broadcaster()
