"use strict";

// ---------- Utilidades ----------
const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

// replaceChildren que ignora null/false (útil con elementos opcionales)
function fill(el, ...kids) {
  el.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));
}

async function api(path, options = {}) {
  const res = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let msg = `El servidor respondió con error ${res.status}.`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") msg = data.detail;
    } catch { /* sin cuerpo */ }
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

const MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
  "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
const WEEKDAYS = ["L", "M", "X", "J", "V", "S", "D"];
const PRIO_ORDER = ["alta", "media", "baja"];

const pad = (n, len = 3) => String(n).padStart(len, "0");
const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1, 2)}-${pad(d.getDate(), 2)}`;
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const prioClass = (p) => (PRIO_ORDER.includes(p) ? `prio prio-${p}` : "prio prio-otra");

function fmtStamp(iso) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
  return `${d.getDate()} ${MONTHS[d.getMonth()].slice(0, 3)}, ${time}`;
}
function fmtLong(iso) {
  return new Date(iso).toLocaleString("es", { dateStyle: "long", timeStyle: "short" });
}

let toastTimer;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

// ---------- Estado ----------
const today = new Date();
const state = {
  status: "active",      // active | done
  tag: null,
  priority: null,
  date: null,            // "YYYY-MM-DD"
  month: new Date(today.getFullYear(), today.getMonth(), 1),
  meta: { tags: [], priorities: [], days: [] },
  notes: [],
  editing: null,         // nota que se está editando (null = nueva)
};

const hasFilters = () => Boolean(state.tag || state.priority || state.date);

// ---------- Carga de datos ----------
async function loadNotes() {
  const q = new URLSearchParams({ status: state.status });
  if (state.tag) q.set("tag", state.tag);
  if (state.priority) q.set("priority", state.priority);
  if (state.date) q.set("date", state.date);
  try {
    state.notes = await api(`/notes?${q}`);
    renderBoard();
  } catch (err) {
    renderBoardError(err);
  }
}

async function loadMeta() {
  const month = `${state.month.getFullYear()}-${pad(state.month.getMonth() + 1, 2)}`;
  try {
    state.meta = await api(`/meta?month=${month}`);
  } catch {
    state.meta = { tags: [], priorities: [], days: [] };
  }
  renderCalendar();
  renderFilters();
}

const refresh = () => Promise.all([loadNotes(), loadMeta()]);

// ---------- Calendario ----------
function renderCalendar() {
  const y = state.month.getFullYear();
  const m = state.month.getMonth();
  const firstOffset = (new Date(y, m, 1).getDay() + 6) % 7; // semana empieza el lunes
  const daysInMonth = new Date(y, m + 1, 0).getDate();
  const withNotes = new Set(state.meta.days);
  const todayIso = isoDay(today);

  const shiftMonth = (delta) => {
    state.month = new Date(y, m + delta, 1);
    loadMeta();
  };

  const grid = h("div", { class: "cal-grid", role: "group", "aria-label": `${MONTHS[m]} ${y}` },
    WEEKDAYS.map((d) => h("span", { class: "cal-wd", "aria-hidden": "true" }, d)),
    Array.from({ length: firstOffset }, () => h("span")),
  );

  for (let d = 1; d <= daysInMonth; d++) {
    const iso = isoDay(new Date(y, m, d));
    const classes = ["cal-day"];
    if (iso === todayIso) classes.push("today");
    if (withNotes.has(iso)) classes.push("has-notes");
    grid.append(h("button", {
      type: "button",
      class: classes.join(" "),
      "aria-pressed": String(state.date === iso),
      "aria-label": `${d} de ${MONTHS[m]}${withNotes.has(iso) ? ", con notas" : ""}`,
      onclick: () => {
        state.date = state.date === iso ? null : iso;
        renderCalendar();
        renderFilters();
        loadNotes();
      },
    }, d));
  }

  fill($("#calendar"),
    h("div", { class: "cal-head" },
      h("button", { type: "button", class: "cal-nav", "aria-label": "Mes anterior", onclick: () => shiftMonth(-1) }, "‹"),
      h("span", { class: "cal-month" }, `${MONTHS[m]} ${y}`),
      h("button", { type: "button", class: "cal-nav", "aria-label": "Mes siguiente", onclick: () => shiftMonth(1) }, "›"),
    ),
    grid,
  );
}

// ---------- Filtros ----------
function chip(label, sym, pressed, onclick) {
  return h("button", { type: "button", class: "chip", "aria-pressed": String(pressed), onclick },
    h("span", { class: "sym" }, sym), label);
}

function renderFilters() {
  const setStatus = (s) => { state.status = s; renderFilters(); loadNotes(); };

  const priorities = [...new Set([...PRIO_ORDER, ...state.meta.priorities])];
  const prioChips = priorities.map((p) => chip(cap(p), "$", state.priority === p, () => {
    state.priority = state.priority === p ? null : p;
    renderFilters();
    loadNotes();
  }));

  const tagChips = state.meta.tags.map((t) => chip(t, "@", state.tag === t, () => {
    state.tag = state.tag === t ? null : t;
    renderFilters();
    loadNotes();
  }));
  // si la etiqueta filtrada ya no existe, que siga visible para poder quitarla
  if (state.tag && !state.meta.tags.includes(state.tag)) {
    tagChips.push(chip(state.tag, "@", true, () => { state.tag = null; renderFilters(); loadNotes(); }));
  }

  const dateGroup = state.date
    ? h("div", { class: "filter-group" },
        h("p", { class: "filter-label" }, "Fecha de creación"),
        h("div", { class: "chips" }, h("button", {
          type: "button", class: "chip", "aria-pressed": "true",
          onclick: () => { state.date = null; renderCalendar(); renderFilters(); loadNotes(); },
        }, fmtDate(state.date), " ×")))
    : null;

  fill($("#filters"),
    h("div", { class: "filter-group" },
      h("p", { class: "filter-label" }, "Estado"),
      h("div", { class: "segmented" },
        h("button", { type: "button", "aria-pressed": String(state.status === "active"), onclick: () => setStatus("active") }, "En cola"),
        h("button", { type: "button", "aria-pressed": String(state.status === "done"), onclick: () => setStatus("done") }, "Completadas"),
      )),
    dateGroup,
    h("div", { class: "filter-group" },
      h("p", { class: "filter-label" }, "Prioridad"),
      h("div", { class: "chips" }, prioChips)),
    h("div", { class: "filter-group" },
      h("p", { class: "filter-label" }, "Etiquetas"),
      tagChips.length
        ? h("div", { class: "chips" }, tagChips)
        : h("p", { class: "empty-hint" }, "Aparecen al escribir @etiqueta en una nota.")),
  );

  $("#clear-filters").hidden = !hasFilters();
}

function fmtDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1].slice(0, 3)} ${y}`;
}

$("#clear-filters").addEventListener("click", () => {
  state.tag = state.priority = state.date = null;
  renderCalendar();
  renderFilters();
  loadNotes();
});

// ---------- Tablero ----------
function ticket(note, isNext) {
  const done = Boolean(note.completed_at);
  const open = () => openDetail(note.id);

  const pend = note.total === 0
    ? h("div", { class: "pend-box is-clear" }, "Sin pendientes")
    : h("div", { class: `pend-box${note.pending === 0 ? " is-clear" : ""}` },
        h("span", {}, "Pendientes"),
        h("span", {}, h("b", {}, note.pending), h("small", {}, ` / ${note.total}`)));

  return h("li", { class: "ticket-wrap" },
    h("article", {
      class: `ticket${done ? " is-done" : ""}`,
      tabindex: "0",
      role: "button",
      "aria-label": `Nota ${note.id}: ${note.title}. ${note.pending} pendientes.`,
      onclick: open,
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } },
    },
      h("header", { class: "ticket-head" },
        h("span", { class: "ticket-num" }, `N.º ${pad(note.id)}`),
        isNext ? h("span", { class: "ticket-next" }, "Sigue") : null,
        h("time", { datetime: note.created_at }, fmtStamp(note.created_at))),
      h("div", { class: "ticket-title-row" },
        h("h3", { class: "ticket-title" }, note.title),
        h("span", { class: "ticket-arrow", "aria-hidden": "true" }, "→")),
      pend,
      h("div", { class: "ticket-meta" },
        note.tags.length ? h("div", { class: "ticket-tags" }, note.tags.map((t) => "@" + t).join("  ")) : null,
        note.priority ? h("div", { class: prioClass(note.priority) }, "$" + cap(note.priority)) : null),
    ));
}

function renderBoard() {
  const active = state.status === "active";
  $("#board-title").textContent = active ? "En cola" : "Completadas";
  const n = state.notes.length;
  $("#board-summary").textContent = active
    ? (n === 1 ? "1 nota, en orden de llegada" : `${n} notas, en orden de llegada`)
    : (n === 1 ? "1 nota, la más reciente primero" : `${n} notas, la más reciente primero`);

  fill($("#tickets"), ...state.notes.map((note, i) => ticket(note, active && i === 0 && !hasFilters())));

  const empty = $("#board-empty");
  empty.hidden = n > 0;
  if (n === 0) {
    if (hasFilters()) {
      fill(empty,
        h("p", {}, "Ninguna nota coincide con estos filtros."),
        h("button", { type: "button", class: "btn", onclick: () => $("#clear-filters").click() }, "Limpiar filtros"));
    } else if (active) {
      fill(empty,
        h("p", {}, "No hay notas en cola. Pulsa + para escribir la primera."));
    } else {
      fill(empty,
        h("p", {}, "Todavía no hay notas completadas. Una nota sale de la cola cuando marcas todos sus pendientes."));
    }
  }
}

function renderBoardError(err) {
  fill($("#tickets"));
  $("#board-summary").textContent = "";
  const empty = $("#board-empty");
  empty.hidden = false;
  fill(empty,
    h("p", {}, "No se pudo conectar con el servidor."),
    h("p", { class: "empty-hint" }, `${err.message} Revisa que los contenedores estén en marcha con docker compose ps.`),
    h("button", { type: "button", class: "btn", onclick: refresh }, "Reintentar"));
}

// ---------- Bloc de notas (crear / editar) ----------
const editor = $("#editor");
const editorText = $("#editor-text");
let previewTimer;

function openEditor(note = null) {
  state.editing = note;
  $("#editor-title").textContent = note ? `Editar nota N.º ${pad(note.id)}` : "Nueva nota";
  $("#editor-save").textContent = note ? "Guardar cambios" : "Guardar nota";
  editorText.value = note ? note.raw : "Titulo: ";
  $("#editor-error").hidden = true;
  renderPreview(null);
  editor.showModal();
  editorText.focus();
  editorText.setSelectionRange(editorText.value.length, editorText.value.length);
  if (note) schedulePreview();
}

function renderPreview(p) {
  const box = $("#editor-preview");
  if (!p) {
    fill(box, h("p", { class: "preview-empty" }, "Escribe la nota y aquí verás cómo se interpreta."));
    return;
  }
  fill(box,
    h("h3", { class: "ticket-title" }, p.title),
    p.description ? h("p", { class: "preview-desc" }, p.description) : null,
    p.tasks.length ? h("ul", {}, p.tasks.map((t) => h("li", {}, t))) : null,
    p.tags.length ? h("div", { class: "ticket-tags" }, p.tags.map((t) => "@" + t).join("  ")) : null,
    p.priority ? h("div", { class: prioClass(p.priority) }, "$" + cap(p.priority)) : null,
    p.warnings.map((w) => h("p", { class: "preview-warn" }, w)),
  );
}

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    const raw = editorText.value;
    if (!raw.trim() || raw.trim().toLowerCase() === "titulo:") { renderPreview(null); return; }
    try {
      renderPreview(await api("/parse", { method: "POST", body: JSON.stringify({ raw }) }));
    } catch { /* la vista previa es opcional */ }
  }, 250);
}

async function saveEditor() {
  const raw = editorText.value;
  const err = $("#editor-error");
  if (!raw.trim() || raw.trim().toLowerCase() === "titulo:") {
    err.textContent = "Escribe al menos un título para guardar la nota.";
    err.hidden = false;
    editorText.focus();
    return;
  }
  const btn = $("#editor-save");
  btn.disabled = true;
  try {
    const body = JSON.stringify({ raw });
    if (state.editing) {
      await api(`/notes/${state.editing.id}`, { method: "PUT", body });
      toast("Cambios guardados.");
    } else {
      const created = await api("/notes", { method: "POST", body });
      toast(`Nota N.º ${pad(created.id)} agregada a la cola.`);
    }
    editor.close();
    if (detail.open) detail.close();
    refresh();
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
  }
}

editorText.addEventListener("input", () => { $("#editor-error").hidden = true; schedulePreview(); });
editorText.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveEditor(); }
});
$("#editor-save").addEventListener("click", saveEditor);
$("#new-note").addEventListener("click", () => openEditor());

// ---------- Detalle ----------
const detail = $("#detail");

async function openDetail(id) {
  try {
    renderDetail(await api(`/notes/${id}`));
    if (!detail.open) detail.showModal();
  } catch (e) {
    toast(e.message);
    refresh();
  }
}

function renderDetail(note) {
  const done = Boolean(note.completed_at);

  const toggle = async (task, checked) => {
    try {
      const updated = await api(`/tasks/${task.id}`, { method: "PATCH", body: JSON.stringify({ done: checked }) });
      renderDetail(updated);
      if (!done && updated.completed_at) toast("Todos los pendientes listos: la nota salió de la cola.");
      if (done && !updated.completed_at) toast("La nota volvió a la cola.");
      refresh();
    } catch (e) { toast(e.message); }
  };

  const action = async (path, msg) => {
    try {
      await api(path, { method: "POST" });
      toast(msg);
      detail.close();
      refresh();
    } catch (e) { toast(e.message); }
  };

  const remove = async () => {
    if (!confirm(`¿Eliminar la nota "${note.title}"? No se puede deshacer.`)) return;
    try {
      await api(`/notes/${note.id}`, { method: "DELETE" });
      toast("Nota eliminada.");
      detail.close();
      refresh();
    } catch (e) { toast(e.message); }
  };

  let status;
  if (done) status = `Completada el ${fmtLong(note.completed_at)}`;
  else if (note.total === 0) status = "Sin pendientes: complétala cuando termines.";
  else status = note.pending === 1
    ? "Falta 1 pendiente para que salga de la cola."
    : `Faltan ${note.pending} pendientes para que salga de la cola.`;

  fill($("#detail-body"),
    h("header", { class: "sheet-head" },
      h("div", {},
        h("h2", { id: "detail-title" }, note.title),
        h("p", { class: "sheet-sub" }, `N.º ${pad(note.id)}, creada el ${fmtLong(note.created_at)}`)),
      h("button", { type: "button", class: "icon-btn", "aria-label": "Cerrar", onclick: () => detail.close() }, "×")),
    h("div", { class: "detail-paper" },
      note.description ? h("p", { class: "detail-desc" }, note.description) : null,
      note.tasks.length
        ? h("ul", { class: "checklist", "aria-label": "Pendientes" }, note.tasks.map((t) =>
            h("li", {}, h("label", { class: t.done ? "done" : null },
              h("input", { type: "checkbox", checked: t.done, onchange: (e) => toggle(t, e.target.checked) }),
              h("span", {}, t.text)))))
        : null,
      (note.tags.length || note.priority)
        ? h("div", { class: "detail-meta" },
            note.tags.length ? h("span", { class: "ticket-tags" }, note.tags.map((t) => "@" + t).join("  ")) : null,
            note.priority ? h("span", { class: prioClass(note.priority) }, "$" + cap(note.priority)) : null)
        : null),
    h("p", { class: "status-line" }, status),
    h("footer", { class: "sheet-foot" },
      h("button", { type: "button", class: "btn btn-danger", onclick: remove }, "Eliminar"),
      h("span", { class: "spacer" }),
      h("button", { type: "button", class: "btn", onclick: () => openEditor(note) }, "Editar"),
      note.total === 0 && !done
        ? h("button", { type: "button", class: "btn btn-primary", onclick: () => action(`/notes/${note.id}/complete`, "Nota completada.") }, "Completar")
        : null,
      note.total === 0 && done
        ? h("button", { type: "button", class: "btn", onclick: () => action(`/notes/${note.id}/reopen`, "La nota volvió a la cola.") }, "Devolver a la cola")
        : null),
  );
}

// Clic fuera del diálogo lo cierra
for (const dlg of [editor, detail]) {
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
}

// Atajo: N abre una nota nueva
document.addEventListener("keydown", (e) => {
  if (e.key.toLowerCase() === "n" && !e.ctrlKey && !e.metaKey && !e.altKey
      && !editor.open && !detail.open && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) {
    e.preventDefault();
    openEditor();
  }
});

refresh();
