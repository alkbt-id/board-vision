// «Видение доски» — the drill screen.
// A position appears, the opponent makes his move, the user draws arrows "who takes -> whom" on every piece of his
// that is now under attack (or presses «Угроз нет»), the server checks and the board shows the review.
// Every action goes to the event log (POST /api/log) so a session can be watched live and analysed later.
import { Chessground } from "./vendor/chessground/chessground.min.js";

const $ = (s) => document.querySelector(s);
const panel = $("#panel");
const wrap = $("#board-wrap");
const statusEl = $("#status");

// ---------- the event log ----------
function log(kind, data = {}) {
  const body = JSON.stringify({ kind, ...(S.sid ? { session: S.sid, index: S.index } : {}), ...data });
  fetch("/api/log", { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true }).catch(() => {});
}
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}
window.addEventListener("error", (e) => log("error_js", { message: String(e.message).slice(0, 200), at: `${e.filename}:${e.lineno}` }));

// ---------- state ----------
const S = {
  phase: "start",   // start | wait (opponent's move animating) | draw | review | summary
  sid: null, index: 0, size: 0, pos: null,
  shownAt: 0,       // performance.now() when the opponent's move had landed
  pick: null,       // tap-tap drawing: the square of the first tap
};
const ms = () => (S.shownAt ? Math.round(performance.now() - S.shownAt) : 0);

// ---------- sounds (Lichess "standard", fetched by scripts/fetch_sounds.sh; silent without them) ----------
const sounds = {};
let audio = null;
function unlockAudio() {
  if (audio) return;
  try { audio = new AudioContext(); } catch { return; }
  for (const name of ["Move", "Capture"]) {
    fetch(`/static/vendor/sound/${name}.mp3`).then((r) => (r.ok ? r.arrayBuffer() : null))
      .then((buf) => buf && audio.decodeAudioData(buf)).then((b) => { if (b) sounds[name] = b; }).catch(() => {});
  }
}
function play(name) {
  if (!audio || !sounds[name]) return;
  const src = audio.createBufferSource();
  src.buffer = sounds[name];
  src.connect(audio.destination);
  src.start();
}
document.addEventListener("pointerdown", unlockAudio, { once: true, capture: true });

// ---------- the board ----------
const cg = Chessground($("#board"), {
  coordinates: true,
  animation: { enabled: true, duration: 250 },
  movable: { free: false, color: undefined },
  premovable: { enabled: false },
  draggable: { enabled: false },
  selectable: { enabled: false },
  highlight: { lastMove: true, check: true },
  drawable: {
    enabled: true, visible: true, eraseOnMovablePieceClick: false,
    brushes: {
      green: { key: "g", color: "#15781B", opacity: 1, lineWidth: 10 },
      red: { key: "r", color: "#882020", opacity: 1, lineWidth: 10 },
      grey: { key: "gr", color: "#4a4a4a", opacity: 0.45, lineWidth: 10 },
      paleGreen: { key: "pg", color: "#15781B", opacity: 0.4, lineWidth: 15 },
    },
    // right-button arrows, drawn by chessground itself
    onChange: (shapes) => syncArrows(shapes, "mouse-right"),
  },
});

let arrows = []; // the user's arrows on the board now: [{from, to}]
const sameArrow = (a, b) => a.from === b.from && a.to === b.to;
function arrowList(shapes) {
  return shapes.filter((s) => s.dest && s.orig !== s.dest).map((s) => ({ from: s.orig, to: s.dest }));
}
// compares the board's arrows with the last known ones and logs what was added or removed
function syncArrows(shapes, how) {
  if (S.phase !== "draw") { cg.setShapes(S.reviewShapes || []); return; } // a right click outside drawing changes nothing
  const now = arrowList(shapes);
  for (const a of now) if (!arrows.some((b) => sameArrow(a, b))) log("arrow_add", { ...a, ms: ms(), how });
  for (const a of arrows) if (!now.some((b) => sameArrow(a, b))) log("arrow_del", { ...a, ms: ms(), how });
  arrows = now;
  // only arrows count: circles from a right click are dropped
  if (shapes.some((s) => !s.dest || s.orig === s.dest)) cg.setShapes(now.map((a) => ({ orig: a.from, dest: a.to, brush: "green" })));
  updateCheckButton();
}
function toggleArrow(from, to, how) {
  const shapes = arrows.map((a) => ({ orig: a.from, dest: a.to, brush: "green" }));
  const i = arrows.findIndex((a) => a.from === from && a.to === to);
  if (i >= 0) shapes.splice(i, 1); else shapes.push({ orig: from, dest: to, brush: "green" });
  cg.setShapes(shapes);
  syncArrows(shapes, how);
}
function clearArrows() {
  if (!arrows.length) return;
  cg.setShapes([]);
  arrows = [];
  setPick(null);
  log("arrows_clear", { ms: ms() });
  updateCheckButton();
}

// Left mouse button and fingers draw too (chessground draws only with the right button and would clear the arrows
// on a left click): a drag draws an arrow; a tap on one square, then on another, does the same.
const keyAt = (x, y) => { cg.state.dom.bounds.clear(); return cg.getKeyAtDomPos([x, y]); };
const drag = { from: null, id: null, moved: false };
function setPick(key) {
  S.pick = key;
  cg.set({ highlight: { custom: key ? new Map([[key, "from-pick"]]) : new Map() } });
}
function preview(from, to) {
  cg.setAutoShapes(from && to && from !== to ? [{ orig: from, dest: to, brush: "paleGreen" }] : []);
}
wrap.addEventListener("pointerdown", (e) => {
  if (e.pointerType === "mouse" && e.button !== 0) return; // the right button: chessground
  e.preventDefault();
  e.stopPropagation();
  if (S.phase !== "draw") return;
  drag.from = keyAt(e.clientX, e.clientY);
  drag.id = e.pointerId;
  drag.moved = false;
  wrap.setPointerCapture?.(e.pointerId);
}, { capture: true });
wrap.addEventListener("pointermove", (e) => {
  if (drag.id !== e.pointerId || !drag.from) return;
  const k = keyAt(e.clientX, e.clientY);
  if (k && k !== drag.from) drag.moved = true;
  if (drag.moved) preview(drag.from, k);
}, { capture: true });
wrap.addEventListener("pointerup", (e) => {
  if (drag.id !== e.pointerId) return;
  const from = drag.from, to = keyAt(e.clientX, e.clientY);
  drag.from = drag.id = null;
  preview(null);
  if (S.phase !== "draw" || !from) return;
  const how = e.pointerType === "touch" ? "touch" : e.pointerType === "pen" ? "pen" : "mouse-left";
  if (to && to !== from) { setPick(null); toggleArrow(from, to, `${how}-drag`); return; }
  // a tap
  if (!S.pick) setPick(from);
  else if (S.pick === from) setPick(null);
  else { const a = S.pick; setPick(null); toggleArrow(a, from, `${how}-tap`); }
}, { capture: true });
wrap.addEventListener("pointercancel", () => { drag.from = drag.id = null; preview(null); }, { capture: true });
// chessground listens to mouse/touch events: keep the left button and touches away from it
for (const t of ["mousedown", "touchstart"]) {
  wrap.addEventListener(t, (e) => { if (t === "touchstart" || e.button === 0) e.stopPropagation(); }, { capture: true, passive: true });
}
wrap.addEventListener("contextmenu", (e) => e.preventDefault());

// ---------- board size: as big as the window allows ----------
function layout() {
  const side = window.innerWidth <= 820 ? 0 : parseInt(getComputedStyle(document.documentElement).getPropertyValue("--side-w")) + 14;
  const top = $(".top").getBoundingClientRect().height;
  const hint = $("#board-hint").getBoundingClientRect().height + 6;
  const availH = window.innerHeight - top - 28 - hint;
  const availW = window.innerWidth - side - 28;
  const size = Math.max(240, Math.floor(Math.min(availH, availW) / 8) * 8);
  wrap.style.width = wrap.style.height = `${size}px`;
  $("#board-hint").style.maxWidth = `${size}px`;
  cg.redrawAll();
}
window.addEventListener("resize", layout);

// ---------- attention: window in the background, long pauses ----------
document.addEventListener("visibilitychange", () => log(document.hidden ? "hidden" : "visible", { phase: S.phase, ms: ms() }));
window.addEventListener("blur", () => log("blur", { phase: S.phase, ms: ms() }));
window.addEventListener("focus", () => log("focus", { phase: S.phase, ms: ms() }));
const IDLE_MS = 30000;
let lastActive = performance.now(), idle = false;
function activity() {
  if (idle) log("active", { ms: Math.round(performance.now() - lastActive), phase: S.phase });
  idle = false;
  lastActive = performance.now();
}
for (const t of ["pointerdown", "pointermove", "keydown", "wheel"]) document.addEventListener(t, activity, { capture: true, passive: true });
setInterval(() => {
  if (!idle && S.phase === "draw" && performance.now() - lastActive > IDLE_MS) {
    idle = true;
    log("idle", { ms: Math.round(performance.now() - lastActive), phase: S.phase });
  }
}, 2000);

// ---------- screens ----------
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const sec = (v) => `${(v / 1000).toFixed(1).replace(".", ",")} с`;
const plural = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
};

let stateTimer = null;
async function showStart() {
  S.phase = "start";
  S.sid = null;
  S.reviewShapes = null;
  wrap.classList.add("locked");
  cg.set({ fen: "start", orientation: "white", lastMove: undefined });
  cg.setShapes([]);
  cg.setAutoShapes([]);
  panel.innerHTML = `
    <h2>Что теперь под ударом?</h2>
    <ol class="rules">
      <li>Соперник делает ход.</li>
      <li>Нарисуй стрелку на каждую свою фигуру или пешку, которую он может выгодно взять — от его фигуры к твоей. И на мат в один ход, если он есть.</li>
      <li>Ищи по всей доске, не только у фигуры, которая пошла. Если угроз нет — так и ответь.</li>
    </ol>
    <p class="hint">«Выгодно взять» — нападающих больше, чем защитников, или нападает фигура дешевле.</p>
    <div id="pool" class="hint"></div>
    <button class="btn primary big" id="go" disabled>Начать — 15 позиций</button>
    <div id="days"></div>`;
  $("#go").addEventListener("click", startSession);
  await refreshState();
  loadDays();
}

async function refreshState() {
  clearTimeout(stateTimer);
  let st;
  try { st = await api("/api/state"); } catch (e) { statusEl.textContent = `сервер не отвечает: ${e.message}`; statusEl.className = "status bad"; return; }
  const c = st.counts, imp = st.import;
  statusEl.className = "status";
  statusEl.innerHTML = imp.running
    ? `<span class="spin"></span>готовлю позиции: ${esc(imp.step)}${imp.text ? " · " + esc(imp.text) : ""}`
    : `позиций ${c.threat + c.quiet}`;
  if (S.phase === "start") {
    const pool = $("#pool");
    if (pool) {
      pool.textContent = st.ready
        ? `Позиций: с угрозой ${c.threat}, тихих ${c.quiet}${c.quiet_unchecked ? ` (ещё ${c.quiet_unchecked} проверяет Stockfish)` : ""}. Из твоих партий ${c.own}, из чужих ${c.db}, из задач ${c.puzzle}.`
        : `Готовлю позиции из твоих партий, чужих партий и задач Lichess… ${imp.text ? `(${imp.text})` : ""}`;
      $("#go").disabled = !st.ready;
    }
  }
  if (imp.running) stateTimer = setTimeout(refreshState, 2000);
}

async function loadDays() {
  const el = $("#days");
  if (!el) return;
  let days;
  try { days = await api("/api/history"); } catch { return; }
  if (!days.length) return;
  el.innerHTML = `<h3>По дням</h3><div class="days">${days.slice(0, 10).map((d) => `
    <div><span>${esc(d.day.slice(5).split("-").reverse().join("."))}</span>
    <span>угроз найдено <b>${d.found}</b> из <b>${d.threats}</b> · тихих верно <b>${d.quiet_ok}</b> из <b>${d.quiet}</b></span>
    <span>${sec(d.median_ms)}</span></div>`).join("")}</div>
    <p class="hint">Справа — среднее время на позицию.</p>`;
}

async function startSession() {
  $("#go").disabled = true;
  try { showPosition(await api("/api/session", {})); } catch (e) { $("#pool").textContent = e.message; $("#go").disabled = false; }
}

function showPosition(p) {
  if (p.done) return showSummary(p.summary);
  Object.assign(S, { sid: p.session, index: p.index, size: p.size, pos: p, phase: "wait", shownAt: 0, reviewShapes: null });
  arrows = [];
  setPick(null);
  wrap.classList.remove("locked");
  cg.setShapes([]);
  cg.setAutoShapes([]);
  cg.set({ animation: { enabled: false } });
  cg.set({ fen: p.fen, orientation: p.orientation, turnColor: p.orientation === "white" ? "black" : "white", lastMove: undefined, check: false });
  cg.set({ animation: { enabled: true, duration: 250 } });
  panel.innerHTML = `
    <div class="step"><span>Позиция ${p.index + 1} из ${p.size}</span><span id="clock"></span></div>
    <div class="prog"><i style="width:${(p.index / p.size) * 100}%"></i></div>
    <p class="task" id="task">Смотри: соперник ходит…</p>
    <div class="actions"><button class="btn quiet" id="clear" disabled>Стереть стрелки</button></div>
    <button class="btn primary big" id="check" disabled>Угроз нет</button>`;
  $("#clear").addEventListener("click", clearArrows);
  $("#check").addEventListener("click", check);
  setTimeout(() => {
    cg.set({ fen: p.after, lastMove: [p.move.slice(0, 2), p.move.slice(2, 4)], turnColor: p.orientation });
    play(p.capture ? "Capture" : "Move");
    setTimeout(() => {
      S.phase = "draw";
      S.shownAt = performance.now();
      lastActive = performance.now();
      $("#task").innerHTML = "Соперник сходил. Нарисуй стрелки на все свои фигуры, которые теперь под ударом.";
      $("#check").disabled = false;
      $("#clear").disabled = false;
      log("move_shown", { item: p.item });
    }, 280);
  }, 450);
}

function updateCheckButton() {
  const b = $("#check");
  if (b && S.phase === "draw") b.textContent = arrows.length ? `Проверить (${arrows.length})` : "Угроз нет";
}

async function check() {
  if (S.phase !== "draw") return;
  S.phase = "review";
  const took = ms();
  setPick(null);
  $("#check").disabled = true;
  let r;
  try { r = await api("/api/answer", { session: S.sid, index: S.index, arrows, ms: took }); }
  catch (e) { S.phase = "draw"; $("#check").disabled = false; $("#task").textContent = `Не получилось проверить: ${e.message}`; return; }
  showReview(r, took);
}

function showReview(r, took) {
  wrap.classList.add("locked");
  const res = r.result;
  // the board: the user's right arrows green, extra ones grey; missed threats red from the attacker; found targets ringed
  const found = new Set(res.found);
  const userShapes = arrows.map((a) => {
    const right = (found.has(a.to) || found.has(a.from)) && !res.extra.some((x) => x.from === a.from && x.to === a.to);
    return { orig: a.from, dest: a.to, brush: right ? "green" : "grey" };
  });
  const auto = [];
  for (const t of r.threats) {
    if (found.has(t.target)) auto.push({ orig: t.target, brush: "green" });
    else auto.push({ orig: t.best.slice(0, 2), dest: t.target, brush: "red" });
  }
  S.reviewShapes = userShapes;
  cg.setShapes(userShapes);
  cg.setAutoShapes(auto);
  const n = r.threats.length;
  let head, cls;
  if (!n) {
    cls = res.ok ? "ok" : "fail";
    head = res.ok ? "Верно: угроз нет" : "Здесь угроз не было";
  } else {
    cls = res.ok ? "ok" : "fail";
    head = res.ok ? (n === 1 ? "Верно: угроза найдена" : `Верно: найдены все ${n}`) : `Найдено ${res.found.length} из ${n}`;
  }
  const why = { defended: "фигура защищена — взять её без потерь нельзя", not_attacked: "эту фигуру никто не бьёт", wrong_attacker: "бьёт её другая фигура" };
  const lines = res.lines.map((l) => `<li class="${l.startsWith("найдено") ? "found" : "missed"}">${esc(l)}</li>`);
  for (const x of res.extra) lines.push(`<li class="extra">лишняя стрелка <span class="sq">${esc(x.from)}→${esc(x.to)}</span>: ${esc(why[x.why] || x.why)}</li>`);
  const where = [r.source, r.motif].filter(Boolean).join(" · ") + (r.repeat ? " · повтор" : "");
  const link = r.ref && r.ref.startsWith("http") ? ` · <a href="${esc(r.ref)}" target="_blank" rel="noopener">открыть</a>` : "";
  const last = S.index + 1 >= S.size;
  panel.innerHTML = `
    <div class="step"><span>Позиция ${S.index + 1} из ${S.size}</span><span>${sec(took)}</span></div>
    <div class="prog"><i style="width:${((S.index + 1) / S.size) * 100}%"></i></div>
    <div class="result ${cls}"><h3>${head}</h3>${lines.length ? `<ul>${lines.join("")}</ul>` : ""}</div>
    <p class="hint">${esc(where)}${link}</p>
    <button class="btn primary big" id="next">${last ? "Итог сессии" : "Дальше"}</button>`;
  $("#next").addEventListener("click", next);
  $("#next").focus();
}

async function next() {
  if (S.phase !== "review") return;
  log("next_click", { ms: ms() });
  $("#next").disabled = true;
  try { showPosition(await api("/api/next", { session: S.sid })); } catch (e) { $("#next").disabled = false; panel.insertAdjacentHTML("beforeend", `<p class="hint">${esc(e.message)}</p>`); }
}

function showSummary(s) {
  S.phase = "summary";
  S.reviewShapes = null;
  wrap.classList.add("locked");
  cg.setShapes([]);
  cg.setAutoShapes([]);
  panel.innerHTML = `
    <h2>Сессия окончена</h2>
    <div class="stats">
      <span>Угроз найдено</span><b>${s.found} из ${s.threats}</b>
      <span>Позиций с угрозой без единого промаха</span><b>${s.threat_ok} из ${s.threat_positions}</b>
      <span>Тихих позиций отвечено верно</span><b>${s.quiet_ok} из ${s.quiet}</b>
      <span>Лишних стрелок</span><b>${s.extra}</b>
      <span>В среднем на позицию</span><b>${sec(s.median_ms)}</b>
    </div>
    <p class="hint">Пропущенные позиции вернутся завтра, потом через 3 дня, неделю, две недели и месяц.</p>
    <button class="btn primary big" id="again">Ещё сессия</button>
    <button class="btn quiet" id="home">К началу</button>`;
  $("#again").addEventListener("click", async () => { $("#again").disabled = true; try { showPosition(await api("/api/session", {})); } catch (e) { $("#again").disabled = false; panel.insertAdjacentHTML("beforeend", `<p class="hint">${esc(e.message)}</p>`); } });
  $("#home").addEventListener("click", showStart);
  $("#again").focus();
}

// ---------- keyboard: Enter — the screen's main button; Backspace — clear the arrows; Esc — cancel a tap ----------
document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "Enter" || e.key === " ") {
    const b = panel.querySelector(".btn.primary:not(:disabled)");
    if (b) { e.preventDefault(); b.click(); }
  } else if ((e.key === "Backspace" || e.key === "Delete") && S.phase === "draw") {
    e.preventDefault();
    clearArrows();
  } else if (e.key === "Escape") setPick(null);
});

// ---------- start ----------
const device = /iPad/.test(navigator.userAgent) || (navigator.maxTouchPoints > 1 && /Macintosh/.test(navigator.userAgent)) ? "iPad" : "Mac";
log("page_load", { device, w: window.innerWidth, h: window.innerHeight });
layout();
showStart();
