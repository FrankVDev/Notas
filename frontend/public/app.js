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

// Identificador de esta pestaña. El servidor lo incluye en los avisos en vivo
// para que cada pantalla ignore los cambios que hizo ella misma.
// (crypto.randomUUID no existe en http por IP, de ahí el respaldo)
const CLIENT_ID = crypto.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

// Cambios enviados al servidor que aún no tienen respuesta
let mutating = 0;

async function api(path, options = {}) {
  const isWrite = options.method && options.method !== "GET";
  if (isWrite) mutating++;
  try {
    const res = await fetch("/api" + path, {
      headers: { "Content-Type": "application/json", "X-Client-Id": CLIENT_ID },
      ...options,
    });
    if (!res.ok) {
      let msg = `El servidor respondió con error ${res.status}.`;
      try {
        const data = await res.json();
        if (typeof data.detail === "string") msg = data.detail;
      } catch { /* sin cuerpo */ }
      const err = new Error(msg);
      err.status = res.status;
      throw err;
    }
    return res.status === 204 ? null : res.json();
  } finally {
    if (isWrite) {
      mutating--;
      if (mutating === 0 && syncWanted) requestSync();
    }
  }
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

// Aviso inferior. Con `action` muestra un botón (p. ej. Deshacer) y dura más.
const toastEl = $("#toast");
let toastTimer = null;
let toastRemaining = 0;
let toastStartedAt = 0;

function hideToast() {
  clearTimeout(toastTimer);
  toastTimer = null;
  toastEl.classList.remove("show");
}
function startToastTimer(ms) {
  clearTimeout(toastTimer);
  toastRemaining = ms;
  toastStartedAt = Date.now();
  toastTimer = setTimeout(hideToast, ms);
}
function toast(msg, { action = null, duration } = {}) {
  $("#toast-msg").textContent = msg;
  const btn = $("#toast-action");
  btn.hidden = !action;
  btn.onclick = null;
  if (action) {
    btn.textContent = action.label;
    btn.onclick = () => { hideToast(); action.onClick(); };
  }
  toastEl.classList.add("show");
  startToastTimer(duration ?? (action ? 7000 : 3500));
}
// El aviso no desaparece mientras el puntero o el foco están sobre él
for (const ev of ["mouseenter", "focusin"]) {
  toastEl.addEventListener(ev, () => {
    if (!toastTimer) return;
    clearTimeout(toastTimer);
    toastRemaining -= Date.now() - toastStartedAt;
  });
}
for (const ev of ["mouseleave", "focusout"]) {
  toastEl.addEventListener(ev, () => {
    if (toastEl.classList.contains("show") && !toastEl.contains(document.activeElement)) {
      startToastTimer(Math.max(toastRemaining, 2000));
    }
  });
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
  detail: null,          // nota abierta en el detalle
  hidden: new Set(),     // notas borradas que aún se pueden deshacer
  expanded: new Set(),   // tickets con la lista de pendientes abierta (por defecto, contraídos)
  flash: new Set(),      // tickets que cambiaron en otro dispositivo (se resaltan un momento)
  loaded: false,
};

// Cada cambio local invalida las cargas que estaban en curso,
// para que una respuesta vieja no pise lo que el usuario acaba de hacer.
let viewSeq = 0;

state.q = "";  // texto de búsqueda

// Filtros del panel lateral (calendario, prioridad, etiqueta)
const panelFilterCount = () => [state.tag, state.priority, state.date].filter(Boolean).length;
// Cualquier filtro, incluida la búsqueda
const hasFilters = () => panelFilterCount() > 0 || Boolean(state.q);

// ---------- Filtros en la URL ----------
// La dirección refleja la vista actual: se puede recargar o guardar como marcador.
function readUrl() {
  const p = new URLSearchParams(location.search);
  state.status = p.get("estado") === "completadas" ? "done" : "active";
  state.tag = p.get("etiqueta") || null;
  state.priority = p.get("prioridad") || null;
  state.q = p.get("buscar") || "";
  const date = p.get("fecha");
  state.date = /^\d{4}-\d{2}-\d{2}$/.test(date || "") ? date : null;
  if (state.date) {
    const [y, m] = state.date.split("-").map(Number);
    state.month = new Date(y, m - 1, 1);
  }
  $("#search").value = state.q;
}

function writeUrl() {
  const p = new URLSearchParams();
  if (state.status === "done") p.set("estado", "completadas");
  if (state.tag) p.set("etiqueta", state.tag);
  if (state.priority) p.set("prioridad", state.priority);
  if (state.date) p.set("fecha", state.date);
  if (state.q) p.set("buscar", state.q);
  const target = p.toString() ? `?${p}` : "";
  if (location.search !== target) history.replaceState(null, "", target || location.pathname);
}

// ---------- Carga de datos ----------
// Barra de progreso: solo aparece si la espera supera 300 ms
let busyCount = 0;
let busyTimer = null;
function setBusy(on) {
  busyCount += on ? 1 : -1;
  const bar = $("#progress");
  if (busyCount > 0 && !busyTimer && bar.hidden) {
    busyTimer = setTimeout(() => { busyTimer = null; if (busyCount > 0) bar.hidden = false; }, 300);
  } else if (busyCount === 0) {
    clearTimeout(busyTimer);
    busyTimer = null;
    bar.hidden = true;
  }
  $(".board").setAttribute("aria-busy", String(busyCount > 0));
}

async function loadNotes() {
  const seq = ++viewSeq;
  const q = new URLSearchParams({ status: state.status });
  if (state.tag) q.set("tag", state.tag);
  if (state.priority) q.set("priority", state.priority);
  if (state.date) q.set("date", state.date);
  if (state.q) q.set("q", state.q);
  writeUrl();
  setBusy(true);
  try {
    const notes = await api(`/notes?${q}`);
    if (seq !== viewSeq) return;
    state.notes = notes.filter((n) => !state.hidden.has(n.id));
    state.loaded = true;
    renderBoard();
  } catch (err) {
    if (seq === viewSeq) renderBoardError(err);
  } finally {
    setBusy(false);
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

  const active = panelFilterCount();
  $("#clear-filters").hidden = active === 0;
  const badge = $("#filters-badge");
  badge.textContent = active;
  badge.hidden = active === 0;
  $("#open-menu").setAttribute("aria-label", active ? `Filtros, ${active} activos` : "Filtros");
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

function clearAllFilters() {
  state.q = "";
  $("#search").value = "";
  $("#clear-filters").click();
}

// ---------- Búsqueda ----------
const searchInput = $("#search");
let searchTimer = null;

searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    const q = searchInput.value.trim();
    if (q === state.q) return;
    state.q = q;
    loadNotes();
  }, 250);
});
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    e.preventDefault();
    if (searchInput.value) {
      searchInput.value = "";
      searchInput.dispatchEvent(new Event("input"));
    } else {
      searchInput.blur();
    }
  }
});

// ---------- Menú lateral en pantallas chicas ----------
const menu = $("#menu");
const mobileQuery = window.matchMedia("(max-width: 860px)");

function setMenu(open) {
  const isMobile = mobileQuery.matches;
  menu.classList.toggle("is-open", open && isMobile);
  $("#menu-backdrop").hidden = !(open && isMobile);
  $("#open-menu").setAttribute("aria-expanded", String(open && isMobile));
  // mientras el panel está abierto, el resto de la página no recibe foco
  for (const el of [$(".board"), $("#new-note")]) el.inert = open && isMobile;
  if (open && isMobile) $("#close-menu").focus();
}
const openMenu = () => setMenu(true);
const closeMenu = (returnFocus = true) => {
  const wasOpen = menu.classList.contains("is-open");
  setMenu(false);
  if (wasOpen && returnFocus) $("#open-menu").focus();
};

$("#open-menu").addEventListener("click", openMenu);
$("#close-menu").addEventListener("click", () => closeMenu());
$("#menu-backdrop").addEventListener("click", () => closeMenu());
menu.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
mobileQuery.addEventListener("change", () => closeMenu(false));

// ---------- Tablero ----------
const MAX_TICKET_TASKS = 5;

function checklistItem(note, task, scope) {
  return h("li", {},
    h("label", { class: `check${task.done ? " done" : ""}` },
      h("input", {
        type: "checkbox",
        checked: task.done,
        "data-focus-key": `${scope}-${task.id}`,
        onchange: (e) => toggleTask(note.id, task.id, e.target.checked),
      }),
      h("span", {}, task.text)));
}

function ticket(note, isNext) {
  const done = Boolean(note.completed_at);
  const shown = note.tasks.slice(0, MAX_TICKET_TASKS);
  const hiddenCount = note.tasks.length - shown.length;

  const expanded = state.expanded.has(note.id);
  const listId = `pend-${note.id}`;
  const pend = note.total === 0
    ? h("div", { class: "pend-box is-clear" }, "Sin pendientes")
    : h("div", { class: `pend-box${note.pending === 0 ? " is-clear" : ""}` },
        h("button", {
          type: "button",
          class: "pend-toggle",
          "aria-expanded": String(expanded),
          "aria-controls": listId,
          "aria-label": `Pendientes de ${note.title}: ${note.pending} de ${note.total}. ${expanded ? "Ocultar" : "Mostrar"} lista`,
          "data-focus-key": `p-${note.id}`,
          onclick: () => toggleExpanded(note.id),
        },
          h("span", { class: "pend-label" }, "Pendientes"),
          h("span", {}, h("b", {}, note.pending), h("small", {}, ` / ${note.total}`)),
          h("span", { class: "pend-chevron", "aria-hidden": "true" })),
        expanded
          ? h("ul", { class: "pend-list", id: listId, "aria-label": `Pendientes de ${note.title}` },
              shown.map((t) => checklistItem(note, t, "t")))
          : null,
        expanded && hiddenCount > 0 ? h("p", { class: "pend-more" }, `y ${hiddenCount} más en el detalle`) : null);

  return h("li", { class: `ticket-wrap${state.flash.has(note.id) ? " is-flash" : ""}`, "data-note-id": note.id },
    h("article", { class: `ticket${done ? " is-done" : ""}` },
      h("header", { class: "ticket-head" },
        h("span", { class: "ticket-num" }, `N.º ${pad(note.id)}`),
        isNext ? h("span", { class: "ticket-next" }, "Sigue") : null,
        h("time", { datetime: note.created_at }, fmtStamp(note.created_at))),
      h("h3", { class: "ticket-title-row" },
        // el botón del título cubre todo el ticket (ver CSS), así se abre con un toque en cualquier parte
        h("button", {
          type: "button",
          class: "ticket-open",
          "data-focus-key": `o-${note.id}`,
          onclick: () => openDetail(note.id),
        },
          h("span", { class: "ticket-title" }, note.title),
          h("span", { class: "ticket-arrow", "aria-hidden": "true" }, "→"))),
      pend,
      h("div", { class: "ticket-meta" },
        note.tags.length ? h("div", { class: "ticket-tags" }, note.tags.map((t) => "@" + t).join("  ")) : null,
        note.priority ? h("div", { class: prioClass(note.priority) }, "$" + cap(note.priority)) : null),
    ));
}

function toggleExpanded(id) {
  if (state.expanded.has(id)) state.expanded.delete(id);
  else state.expanded.add(id);
  renderBoard();
}

// Vuelve a dibujar sin perder el elemento que tenía el foco
function keepFocus(render) {
  const key = document.activeElement?.dataset?.focusKey;
  render();
  if (key) document.querySelector(`[data-focus-key="${key}"]`)?.focus();
}

function renderBoard() {
  const active = state.status === "active";
  $("#board-title").textContent = active ? "En cola" : "Completadas";
  const n = state.notes.length;
  $("#board-summary").textContent = active
    ? (n === 1 ? "1 nota, en orden de llegada" : `${n} notas, en orden de llegada`)
    : (n === 1 ? "1 nota, la más reciente primero" : `${n} notas, la más reciente primero`);

  keepFocus(() => fill($("#tickets"), ...state.notes.map((note, i) => ticket(note, active && i === 0 && !hasFilters()))));

  const empty = $("#board-empty");
  empty.hidden = n > 0 || !state.loaded;
  if (n === 0 && state.loaded) {
    if (hasFilters()) {
      const where = active ? "en cola" : "completada";
      const msg = state.q
        ? `Ninguna nota ${where} contiene “${state.q}”${panelFilterCount() ? " con estos filtros" : ""}.`
        : "Ninguna nota coincide con estos filtros.";
      fill(empty,
        h("p", {}, msg),
        h("div", { class: "empty-actions" },
          state.q
            ? h("button", {
                type: "button", class: "btn",
                onclick: () => { state.status = active ? "done" : "active"; renderFilters(); loadNotes(); },
              }, active ? "Buscar en completadas" : "Buscar en la cola")
            : null,
          h("button", { type: "button", class: "btn", onclick: clearAllFilters }, "Limpiar todo")));
    } else if (active) {
      fill(empty,
        h("p", {}, "No hay notas en cola. Pulsa + para escribir la primera."));
    } else {
      fill(empty,
        h("p", {}, "Todavía no hay notas completadas. Una nota sale de la cola cuando marcas todos sus pendientes."));
    }
  }
}

// ---------- Cambios con respuesta inmediata ----------
const belongsToView = (note) => (state.status === "active" ? !note.completed_at : Boolean(note.completed_at));

function sortNotes() {
  if (state.status === "active") {
    state.notes.sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id);
  } else {
    state.notes.sort((a, b) => b.completed_at.localeCompare(a.completed_at));
  }
}

// Saca un ticket del tablero con una breve animación
function removeFromBoard(id) {
  const li = document.querySelector(`.ticket-wrap[data-note-id="${id}"]`);
  const hadFocus = li?.contains(document.activeElement);
  const index = state.notes.findIndex((n) => n.id === id);
  state.notes = state.notes.filter((n) => n.id !== id);

  const finish = () => {
    renderBoard();
    if (hadFocus) {
      // el foco pasa al ticket que ocupó su lugar, o al título del tablero
      const next = document.querySelectorAll(".ticket-open")[Math.min(index, state.notes.length - 1)];
      (next || $("#board-title")).focus();
    }
  };
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (li && !reduced) {
    li.classList.add("is-leaving");
    setTimeout(finish, 420);
  } else {
    finish();
  }
}

// Aplica una versión nueva de una nota en el tablero y en el detalle
function applyNote(note) {
  const i = state.notes.findIndex((n) => n.id === note.id);
  if (belongsToView(note)) {
    if (i >= 0) state.notes[i] = note;
    else state.notes.push(note);
    sortNotes();
    renderBoard();
  } else if (i >= 0) {
    // primero se ve el cambio (casilla marcada, contador en 0) y luego sale
    state.notes[i] = note;
    renderBoard();
    removeFromBoard(note.id);
  }
  if (detail.open && state.detail?.id === note.id) keepFocus(() => renderDetail(note));
}

function findNote(id) {
  return state.notes.find((n) => n.id === id) || (state.detail?.id === id ? state.detail : null);
}

// Calcula localmente cómo queda la nota, igual que el servidor
function withTask(note, taskId, done) {
  const tasks = note.tasks.map((t) => (t.id === taskId ? { ...t, done } : t));
  const pending = tasks.filter((t) => !t.done).length;
  let completed_at = note.completed_at;
  if (tasks.length) completed_at = pending === 0 ? (note.completed_at || new Date().toISOString()) : null;
  return { ...note, tasks, pending, completed_at };
}

async function toggleTask(noteId, taskId, done, known = null) {
  // `known` sirve para deshacer cuando la nota ya no está en el tablero
  const before = findNote(noteId) || known;
  if (!before) return;
  viewSeq++;  // descarta cargas en curso que traerían el estado anterior
  applyNote(withTask(before, taskId, done));
  try {
    const saved = await api(`/tasks/${taskId}`, { method: "PATCH", body: JSON.stringify({ done }) });
    applyNote(saved);
    if (!before.completed_at && saved.completed_at) {
      toast(`"${saved.title}" salió de la cola.`, {
        action: { label: "Deshacer", onClick: () => toggleTask(noteId, taskId, false, saved) },
      });
    } else if (before.completed_at && !saved.completed_at) {
      toast(`"${saved.title}" volvió a la cola.`);
    }
    loadMeta();
  } catch (e) {
    applyNote(before);
    toast(`No se guardó el cambio. ${e.message}`);
  }
}

// Completar o reabrir notas sin pendientes
async function setCompleted(note, complete) {
  viewSeq++;
  const optimistic = { ...note, completed_at: complete ? new Date().toISOString() : null };
  applyNote(optimistic);
  if (detail.open) detail.close();
  try {
    const saved = await api(`/notes/${note.id}/${complete ? "complete" : "reopen"}`, { method: "POST" });
    applyNote(saved);
    toast(complete ? `"${saved.title}" salió de la cola.` : `"${saved.title}" volvió a la cola.`, {
      action: { label: "Deshacer", onClick: () => setCompleted(saved, !complete) },
    });
  } catch (e) {
    applyNote(note);
    toast(e.message);
  }
}

// Borrado diferido: la nota desaparece al instante y se borra de verdad
// cuando ya no se puede deshacer.
const pendingDeletes = new Map();
const UNDO_MS = 7000;

function deleteNote(note) {
  if (detail.open) detail.close();
  state.hidden.add(note.id);
  removeFromBoard(note.id);
  const timer = setTimeout(() => commitDelete(note.id), UNDO_MS);
  pendingDeletes.set(note.id, timer);
  toast(`"${note.title}" eliminada.`, {
    duration: UNDO_MS,
    action: {
      label: "Deshacer",
      onClick: () => {
        clearTimeout(pendingDeletes.get(note.id));
        pendingDeletes.delete(note.id);
        state.hidden.delete(note.id);
        applyNote(note);
        toast(`"${note.title}" restaurada.`);
      },
    },
  });
}

async function commitDelete(id) {
  pendingDeletes.delete(id);
  try {
    await api(`/notes/${id}`, { method: "DELETE" });
    state.hidden.delete(id);
    loadMeta();
  } catch (e) {
    state.hidden.delete(id);
    if (e.status === 404) return;  // ya la había borrado otro dispositivo
    toast(`No se pudo eliminar la nota. ${e.message}`);
    loadNotes();
  }
}

// Si se cierra la página con un borrado pendiente, se completa igual
window.addEventListener("pagehide", () => {
  for (const [id, timer] of pendingDeletes) {
    clearTimeout(timer);
    fetch(`/api/notes/${id}`, { method: "DELETE", keepalive: true });
  }
  pendingDeletes.clear();
});

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
  state.editingRemote = null;  // "updated" | "deleted" si cambió en otro dispositivo
  suggestions = [];
  $("#editor-suggest").hidden = true;
  $("#editor-remote").hidden = true;
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
    if (state.editing && state.editingRemote !== "deleted") {
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

// Inserta texto conservando el historial de deshacer (Ctrl + Z) del navegador
function insertText(start, end, text) {
  editorText.focus();
  editorText.setSelectionRange(start, end);
  if (!document.execCommand("insertText", false, text)) {
    editorText.setRangeText(text, start, end, "end");
  }
  editorText.dispatchEvent(new Event("input"));
}

function currentLine() {
  const v = editorText.value;
  const pos = editorText.selectionStart;
  const start = v.lastIndexOf("\n", pos - 1) + 1;
  const nl = v.indexOf("\n", pos);
  const end = nl === -1 ? v.length : nl;
  return { start, end, pos, text: v.slice(start, end) };
}

// Botones # @ $: empiezan una línea nueva con el símbolo
function insertMarker(sym) {
  const line = currentLine();
  if (line.text.trim() === "") insertText(line.start, line.end, sym);
  else insertText(line.end, line.end, "\n" + sym);
}
for (const btn of document.querySelectorAll("[data-insert]")) {
  btn.addEventListener("mousedown", (e) => e.preventDefault());  // no quitar el foco al texto
  btn.addEventListener("click", () => insertMarker(btn.dataset.insert));
}

// Autocompletado de @etiquetas y $prioridad
const fold = (t) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
let suggestions = [];

function updateSuggestions() {
  const box = $("#editor-suggest");
  const line = currentLine();
  const before = editorText.value.slice(line.start, line.pos);
  const after = editorText.value.slice(line.pos, line.end);
  const m = before.match(/^\s*([@$])(.*)$/);
  suggestions = [];

  if (m && after.trim() === "" && editorText === document.activeElement) {
    const [, sym, typed] = m;
    const t = fold(typed.trim());
    let pool;
    if (sym === "@") {
      // etiquetas ya usadas en otras notas, sin las que ya tiene esta
      const inNote = new Set();
      let offset = 0;
      for (const l of editorText.value.split("\n")) {
        if (offset !== line.start && l.trim().startsWith("@")) inNote.add(fold(l.trim().slice(1).trim()));
        offset += l.length + 1;
      }
      pool = state.meta.tags.filter((tag) => !inNote.has(fold(tag)));
    } else {
      pool = ["Alta", "Media", "Baja"];
    }
    suggestions = pool
      .filter((v) => fold(v).includes(t) && fold(v) !== t)
      .sort((a, b) => fold(b).startsWith(t) - fold(a).startsWith(t))
      .slice(0, 6)
      .map((v) => ({ sym, value: v }));
  }

  if (!suggestions.length) {
    box.hidden = true;
    fill(box);
    return;
  }
  fill(box,
    h("span", { class: "suggest-label" }, "Sugerencias:"),
    suggestions.map((s, i) => h("button", {
      type: "button",
      class: `chip${i === 0 ? " is-first" : ""}`,
      onmousedown: (e) => e.preventDefault(),
      onclick: () => acceptSuggestion(s),
    }, h("span", { class: "sym" }, s.sym), s.value)),
    h("span", { class: "suggest-hint" }, "Tab para usar la primera"));
  box.hidden = false;
}

function acceptSuggestion(s) {
  const line = currentLine();
  const indent = line.text.match(/^\s*/)[0];
  insertText(line.start, line.end, `${indent}${s.sym}${s.value}`);
  suggestions = [];
  updateSuggestions();
}

editorText.addEventListener("input", () => {
  $("#editor-error").hidden = true;
  schedulePreview();
  updateSuggestions();
});
editorText.addEventListener("click", updateSuggestions);
editorText.addEventListener("keyup", (e) => {
  if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) updateSuggestions();
});
editorText.addEventListener("blur", () => setTimeout(updateSuggestions, 0));
editorText.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveEditor(); return; }
  if (e.key === "Tab" && !e.shiftKey && suggestions.length) {
    e.preventDefault();
    acceptSuggestion(suggestions[0]);
  } else if (e.key === "Escape" && suggestions.length) {
    // Escape cierra primero las sugerencias, no el editor
    e.preventDefault();
    suggestions = [];
    $("#editor-suggest").hidden = true;
  }
});
$("#editor-save").addEventListener("click", saveEditor);
$("#new-note").addEventListener("click", () => openEditor());

// ---------- Detalle ----------
const detail = $("#detail");

function showEditorRemote(op) {
  state.editingRemote = op;
  const box = $("#editor-remote");
  if (op === "deleted") {
    fill(box,
      h("span", {}, "Esta nota se eliminó en otro dispositivo. Si guardas, se creará como una nota nueva."));
    $("#editor-save").textContent = "Guardar como nota nueva";
  } else {
    fill(box,
      h("span", {}, "Esta nota cambió en otro dispositivo. Si guardas, tus cambios reemplazarán los suyos."),
      h("button", { type: "button", class: "link-btn", onclick: loadRemoteVersion }, "Ver la versión nueva"));
  }
  box.hidden = false;
}

async function loadRemoteVersion() {
  try {
    const fresh = await api(`/notes/${state.editing.id}`);
    state.editing = fresh;
    state.editingRemote = null;
    editorText.value = fresh.raw;
    $("#editor-remote").hidden = true;
    schedulePreview();
    editorText.focus();
  } catch (e) {
    if (e.status === 404) showEditorRemote("deleted");
    else toast(e.message);
  }
}

async function openDetail(id) {
  try {
    const note = await api(`/notes/${id}`);
    renderDetail(note);
    if (!detail.open) detail.showModal();
  } catch (e) {
    toast(e.message);
    refresh();
  }
}

function renderDetail(note) {
  state.detail = note;
  const done = Boolean(note.completed_at);

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
        ? h("ul", { class: "checklist", "aria-label": "Pendientes" }, note.tasks.map((t) => checklistItem(note, t, "d")))
        : null,
      (note.tags.length || note.priority)
        ? h("div", { class: "detail-meta" },
            note.tags.length ? h("span", { class: "ticket-tags" }, note.tags.map((t) => "@" + t).join("  ")) : null,
            note.priority ? h("span", { class: prioClass(note.priority) }, "$" + cap(note.priority)) : null)
        : null),
    h("p", { class: "status-line" }, status),
    h("footer", { class: "sheet-foot" },
      h("button", { type: "button", class: "btn btn-danger", onclick: () => deleteNote(note) }, "Eliminar"),
      h("span", { class: "spacer" }),
      h("button", { type: "button", class: "btn", onclick: () => openEditor(note) }, "Editar"),
      note.total === 0 && !done
        ? h("button", { type: "button", class: "btn btn-primary", onclick: () => setCompleted(note, true) }, "Completar")
        : null,
      note.total === 0 && done
        ? h("button", { type: "button", class: "btn", onclick: () => setCompleted(note, false) }, "Devolver a la cola")
        : null),
  );
}

detail.addEventListener("close", () => { state.detail = null; });

// Clic fuera del diálogo lo cierra
for (const dlg of [editor, detail]) {
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
}

// Atajo: N abre una nota nueva
document.addEventListener("keydown", (e) => {
  if (e.key.toLowerCase() === "n" && !e.ctrlKey && !e.metaKey && !e.altKey
      && !editor.open && !detail.open && !menu.classList.contains("is-open") && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) {
    e.preventDefault();
    openEditor();
  } else if (e.key === "/" && !e.ctrlKey && !e.metaKey && !editor.open && !detail.open
      && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) {
    e.preventDefault();
    if (menu.classList.contains("is-open")) closeMenu(false);
    searchInput.focus();
    searchInput.select();
  }
});

// ---------- Sincronización en vivo entre dispositivos ----------
let syncWanted = false;
let syncTimer = null;

// Agrupa varios avisos seguidos en una sola recarga
function requestSync() {
  clearTimeout(syncTimer);
  syncTimer = setTimeout(syncNow, 150);
}

async function syncNow() {
  // si hay un cambio propio en camino, se espera a que termine
  // para no mostrar por un instante el estado anterior
  if (mutating > 0) { syncWanted = true; return; }
  syncWanted = false;
  await Promise.all([loadNotes(), loadMeta()]);
  if (state.flash.size) setTimeout(() => state.flash.clear(), 1600);

  if (detail.open && state.detail) {
    try {
      const fresh = await api(`/notes/${state.detail.id}`);
      if (detail.open && state.detail?.id === fresh.id) keepFocus(() => renderDetail(fresh));
    } catch (e) {
      if (e.status === 404 && detail.open) {
        detail.close();
        toast("La nota que tenías abierta se eliminó en otro dispositivo.");
      }
    }
  }
}

function onRemoteChange(event) {
  let change;
  try { change = JSON.parse(event.data); } catch { return; }
  if (change.origin === CLIENT_ID) return;  // es un cambio de esta misma pestaña
  if (change.op !== "deleted") state.flash.add(change.id);
  if (editor.open && state.editing?.id === change.id) showEditorRemote(change.op);
  requestSync();
}

let live = null;
let liveDown = false;
let liveDownTimer = null;
let reconnectTimer = null;

function setLiveStatus(ok) {
  if (ok) {
    clearTimeout(liveDownTimer);
    liveDownTimer = null;
    $("#live-status").hidden = true;
    return;
  }
  // solo se avisa si el corte dura más de unos segundos; los reintentos
  // no reinician la cuenta
  if (!liveDownTimer && $("#live-status").hidden) {
    liveDownTimer = setTimeout(() => { $("#live-status").hidden = false; }, 5000);
  }
}

function connectLive() {
  if (!("EventSource" in window)) return;
  clearTimeout(reconnectTimer);
  live?.close();
  live = new EventSource("/api/events");

  live.addEventListener("ready", () => {
    setLiveStatus(true);
    // al reconectar pudo haber cambios que no llegaron: se recarga una vez
    if (liveDown) requestSync();
    liveDown = false;
  });
  live.addEventListener("change", onRemoteChange);
  live.onerror = () => {
    liveDown = true;
    setLiveStatus(false);
    // si el servidor respondió con error (no solo un corte), el navegador
    // deja de reintentar por su cuenta: se reintenta aquí
    if (live.readyState === EventSource.CLOSED) {
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connectLive, 5000);
    }
  };
}

// Al volver a la pestaña o desbloquear el celular
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  requestSync();
  if (!live || live.readyState === EventSource.CLOSED) connectLive();
});
window.addEventListener("online", () => { requestSync(); connectLive(); });

// Respaldo: por si la conexión en vivo falla sin que el navegador lo note
setInterval(() => {
  if (document.visibilityState === "visible") requestSync();
}, 30000);

readUrl();
refresh();
connectLive();
