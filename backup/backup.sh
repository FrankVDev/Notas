#!/bin/sh
# Respaldo automático de la base de notas.
# Corre en su propio contenedor: saca una copia al arrancar y luego cada
# BACKUP_INTERVAL_HOURS horas, y conserva solo las últimas BACKUP_KEEP.
set -eu

INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-24}"
KEEP="${BACKUP_KEEP:-14}"
DIR=/respaldos
export PGPASSWORD="$POSTGRES_PASSWORD"

echo "Respaldo automático: cada ${INTERVAL_HOURS} h, se conservan los últimos ${KEEP}."
until pg_isready -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q; do
  sleep 2
done

while true; do
  name="notas-$(date +%Y-%m-%d_%H%M).sql"
  if pg_dump -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner -f "$DIR/$name.tmp" \
     && gzip -f "$DIR/$name.tmp" && mv "$DIR/$name.tmp.gz" "$DIR/$name.gz"; then
    echo "Respaldo creado: $name.gz ($(du -h "$DIR/$name.gz" | cut -f1))"
  else
    echo "ERROR: no se pudo crear el respaldo" >&2
    rm -f "$DIR/$name.tmp" "$DIR/$name.tmp.gz"
  fi
  # borra los más antiguos, dejando los KEEP más recientes
  ls -1t "$DIR"/notas-*.sql.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
    rm -f "$old" && echo "Respaldo antiguo eliminado: $(basename "$old")"
  done
  sleep "$((INTERVAL_HOURS * 3600))"
done
