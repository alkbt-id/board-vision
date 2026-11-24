// «Видение доски» — the drill screen.
// A position appears, the opponent makes his move, the user draws arrows "who takes -> whom" on every piece of his
// that is now under attack, puts a circle on every piece of the opponent he can win now (or presses «Ничего нет», or
// draws his own move at once), the server checks and the board shows the review.
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
// every change of S.phase repaints the strip over the board that says what to do (syncMode below)
let _phase = S.phase;
delete S.phase;
Object.defineProperty(S, "phase", { get: () => _phase, set: (v) => { _phase = v; syncMode(); }, enumerable: true });

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
  coordinates: false, // drawn on the frame around the board instead (renderCoords)
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
      orange: { key: "o", color: "#e07b00", opacity: 1, lineWidth: 10 }, // the threats you draw (the orange frame)
      red: { key: "r", color: "#882020", opacity: 1, lineWidth: 10 },
      grey: { key: "gr", color: "#4a4a4a", opacity: 0.45, lineWidth: 10 },
      paleGreen: { key: "pg", color: "#15781B", opacity: 0.4, lineWidth: 15 },
      paleOrange: { key: "po", color: "#e07b00", opacity: 0.45, lineWidth: 15 },
      chance: { key: "ch", color: "#7b4fd6", opacity: 1, lineWidth: 10 }, // your chances: arrows from your pieces
      paleChance: { key: "pch", color: "#7b4fd6", opacity: 0.45, lineWidth: 15 },
    },
    // right-button arrows, drawn by chessground itself
    onChange: (shapes) => syncArrows(shapes, "mouse-right"),
  },
});

// The user's arrows on the board now, in the order drawn: [{from, to}]. Ways to take one back: «Отменить» (or
// Backspace / ⌘Z) removes the last one, ✕ in the list under the task removes any, and drawing an arrow between the
// same two squares again — in either direction — removes it (a reversed arrow counts as the same answer anyway).
let arrows = [];
let myMove = null; // «проверь свой ход»: your own move, the first arrow you draw (blue); the rest are his replies
const drawingNow = () => S.phase === "draw" || S.phase === "plan";
const sameLine = (a, b) => (a.from === b.from && a.to === b.to) || (a.from === b.to && a.to === b.from);
// The marking step: an arrow from his piece is a threat (orange); a circle on his piece is a chance of yours — you
// can win it (purple; a tap on it, or the right button on the Mac; on his king: a mate); an arrow from your own piece
// is your move (blue): it ends the marking, as «Ничего нет» / «Готово» would, and the plan goes on with it.
// A circle is kept as an arrow of no length: {from: "e5", to: "e5"}.
const mine = (sq) => !!S.v && cg.state.pieces.get(sq)?.color === S.v.orientation;
const his = (sq) => !!S.v && !!cg.state.pieces.get(sq) && cg.state.pieces.get(sq).color !== S.v.orientation;
const isCircle = (a) => a.from === a.to;
const isMoveArrow = (from, to) => S.phase === "draw" && from !== to && mine(from);
const toShapes = (list) => [
  // steps 2–3 keep your circles of step 1 on the board, pale
  ...(S.phase === "plan" && S.step1 ? S.step1.marks.filter(isCircle).map((a) => ({ orig: a.from, brush: "paleChance" })) : []),
  ...(myMove ? [{ orig: myMove.from, dest: myMove.to, brush: "blue" }] : []),
  ...list.map((a) => (isCircle(a) ? { orig: a.from, brush: "chance" } : { orig: a.from, dest: a.to, brush: "orange" }))];
// your move drawn in the marking step: a legal one ends the marking, anything else is not a move
function drawnMove(from, to, how) {
  if ((S.v?.dests?.[from] || []).includes(to)) { check({ from, to, how }); return; }
  setArrows(arrows);
  flashTask("Это не ход");
}
// a tap on his piece in the marking step: the circle on it on or off; true when it is on now
function toggleCircle(sq, how) {
  const i = arrows.findIndex((a) => isCircle(a) && a.from === sq);
  if (i >= 0) {
    log("arrow_del", { from: sq, to: sq, ms: ms(), how });
    setArrows(arrows.filter((_, j) => j !== i));
    return false;
  }
  log("arrow_add", { from: sq, to: sq, ms: ms(), how });
  setArrows([...arrows, { from: sq, to: sq }]);
  return true;
}
// the first arrow in a plan: your move, if it is one
function tryMove(from, to, how) {
  const ok = (S.v?.dests?.[from] || []).includes(to);
  if (!ok) { flashTask("Это не ход — нарисуй ход своей фигурой"); return false; }
  myMove = { from, to };
  syncMode();
  S.reviewShapes = null;
  cg.setAutoShapes([]);
  log("plan_move", { from, to, ms: ms(), how });
  setArrows(arrows);
  return true;
}
// a short message in the strip over the board, then back to what to do now
function flashTask(text) {
  bm.text.textContent = text;
  clearTimeout(flashTask.timer);
  flashTask.timer = setTimeout(syncMode, 1600);
}
function setArrows(list) {
  arrows = list;
  cg.setShapes(toShapes(list));
  renderArrows();
}
// chessground's own right-button drawing: compare with the known arrows; a new arrow over an existing line
// (the same one reversed) takes that line off; a circle counts only on his piece in the marking step
function syncArrows(shapes, how) {
  if (!drawingNow()) {
    // the review before your move: a right-button arrow starts your move at once
    if (S.phase === "review" && S.review?.toPlan) {
      const was = S.reviewShapes || [];
      const drawn = shapes.find((s) => s.dest && s.orig !== s.dest && !was.some((p) => p.orig === s.orig && p.dest === s.dest));
      planFromReview("mouse-right");
      if (drawn) tryMove(drawn.orig, drawn.dest, how);
      return;
    }
    cg.setShapes(S.reviewShapes || []); // a right click outside drawing changes nothing
    return;
  }
  // your move drawn again (chessground took the blue arrow off or recoloured it): take the move back
  if (S.phase === "plan" && myMove && !shapes.some((s) => s.brush === "blue")) { rethink(); return; }
  let now = shapes.filter((s) => s.brush !== "blue" && (s.dest ? s.orig !== s.dest : S.phase === "draw" && his(s.orig)))
    .map((s) => ({ from: s.orig, to: s.dest || s.orig }));
  if (S.phase === "draw") { // step 1: his threats and your circles; your move drawn with the right button ends it
    const fresh = now.filter((x) => !isCircle(x) && !arrows.some((b) => x.from === b.from && x.to === b.to));
    const mv = fresh.find((x) => isMoveArrow(x.from, x.to));
    if (mv) { drawnMove(mv.from, mv.to, how); return; }
  }
  if (S.phase === "plan" && !myMove) {
    const a = now.find((x) => !arrows.some((b) => x.from === b.from && x.to === b.to));
    if (a) { tryMove(a.from, a.to, how); now = now.filter((x) => x !== a); }
    if (!myMove) { cg.setShapes(toShapes(arrows)); return; }
  }
  let next = arrows.filter((a) => now.some((b) => a.from === b.from && a.to === b.to));
  for (const a of arrows) if (!next.includes(a)) log("arrow_del", { ...a, ms: ms(), how });
  for (const a of now) {
    if (arrows.some((b) => a.from === b.from && a.to === b.to)) continue;
    const twin = next.find((b) => sameLine(a, b));
    if (twin) { next = next.filter((b) => b !== twin); log("arrow_del", { ...twin, ms: ms(), how: `${how}-reverse` }); }
    else { next.push(a); log("arrow_add", { ...a, ms: ms(), how }); }
  }
  setArrows(next);
}
function toggleArrow(from, to, how, extra = {}) {
  if (isMoveArrow(from, to)) { drawnMove(from, to, how); return; }
  if (S.phase === "plan" && !myMove) { tryMove(from, to, how); return; }
  if (S.phase === "plan" && sameLine({ from, to }, myMove)) { rethink(); return; } // your move drawn again: take it back
  const twin = arrows.find((b) => sameLine({ from, to }, b));
  if (twin) {
    log("arrow_del", { ...twin, ms: ms(), how: twin.from === from ? how : `${how}-reverse` });
    setArrows(arrows.filter((b) => b !== twin));
  } else {
    log("arrow_add", { from, to, ms: ms(), how, ...extra });
    setArrows([...arrows, { from, to }]);
  }
}
function removeArrow(i, how) {
  const a = arrows[i];
  if (!a || !drawingNow()) return;
  log("arrow_del", { ...a, ms: ms(), how });
  setArrows(arrows.filter((_, j) => j !== i));
}
function undoArrow() {
  if (arrows.length) return removeArrow(arrows.length - 1, "undo");
  if (S.phase === "plan" && myMove) rethink();
}
// your move taken back: back to step 1 with your circles; a turn that started with no step 1 stays in step 2
function rethink() {
  if (S.phase !== "plan") return;
  log("plan_rethink", { ms: ms(), from: myMove?.from, to: myMove?.to });
  myMove = null;
  syncMode();
  setArrows([]);
}
function clearArrows() {
  if (!arrows.length || !drawingNow()) return;
  setPick(null);
  log("arrows_clear", { ms: ms(), n: arrows.length });
  setArrows([]);
}
// the strip over the board shows the button's label and the undo button: repaint it
const renderArrows = () => syncMode();

// Left mouse button and fingers draw too (chessground draws only with the right button and would clear the arrows
// on a left click): a drag draws an arrow; a tap on one square, then on another, does the same.
const keyAt = (x, y) => { cg.state.dom.bounds.clear(); return cg.getKeyAtDomPos([x, y]); };
const drag = { from: null, id: null, moved: false, trail: [] };
function setPick(key, circled = false) {
  S.pick = key;
  S.pickCircled = circled; // the tap on his piece that started this pick put a circle on it (the circle shows it)
  cg.set({ highlight: { custom: key && !circled ? new Map([[key, "from-pick"]]) : new Map() } });
}
function preview(from, to) {
  cg.setAutoShapes(from && to && from !== to ? [{ orig: from, dest: to, brush: (S.phase === "plan" && !myMove) || isMoveArrow(from, to) ? "paleBlue" : "paleOrange" }] : []);
}
// Where a finger meant to end. A finger hides the square and the lift often slides on by a square (the user aimed
// d2→e2 and got d2→f2): take the square the finger was on a moment before it left the glass, and if the arrow
// runs along a line past a piece of the other side onto an empty square, stop it at that piece — an arrow always
// ends on a piece of the other colour than where it starts (his piece → yours, a threat; yours → his, a chance),
// except a mate on an empty square, and a mating move's path never crosses a piece.
const LIFT_MS = 120;
function aimedEnd(from, trail, upKey, upTime) {
  let end = upKey;
  for (let i = trail.length - 1; i >= 0; i--) {
    if (upTime - trail[i].t >= LIFT_MS) { end = trail[i].k; break; }
  }
  if (!end || end === from) end = upKey;
  return snapToPiece(from, end);
}
function snapToPiece(from, to) {
  if (!from || !to || cg.state.pieces.get(to)) return to;
  const f0 = from.charCodeAt(0), r0 = +from[1], f1 = to.charCodeAt(0), r1 = +to[1];
  const df = Math.sign(f1 - f0), dr = Math.sign(r1 - r0);
  if (!(f0 === f1 || r0 === r1 || Math.abs(f1 - f0) === Math.abs(r1 - r0))) return to; // not along a line
  const start = cg.state.pieces.get(from);
  const want = start ? (start.color === "white" ? "black" : "white") : S.v?.orientation;
  for (let f = f0 + df, r = r0 + dr; f !== f1 || r !== r1; f += df, r += dr) {
    const k = String.fromCharCode(f) + r;
    const p = cg.state.pieces.get(k);
    if (p) return p.color === want ? k : to;
  }
  return to;
}
wrap.addEventListener("pointerdown", (e) => {
  if (e.pointerType === "mouse" && e.button !== 0) return; // the right button: chessground
  e.preventDefault();
  e.stopPropagation();
  if (!drawingNow() && !planFromReview(e.pointerType)) return;
  drag.from = keyAt(e.clientX, e.clientY);
  drag.id = e.pointerId;
  drag.moved = false;
  drag.trail = [];
  wrap.setPointerCapture?.(e.pointerId);
}, { capture: true });
wrap.addEventListener("pointermove", (e) => {
  if (drag.id !== e.pointerId || !drag.from) return;
  const k = keyAt(e.clientX, e.clientY);
  if (k && k !== drag.from) drag.moved = true;
  if (k && (!drag.trail.length || drag.trail[drag.trail.length - 1].k !== k)) drag.trail.push({ k, t: performance.now() });
  if (drag.moved) preview(drag.from, e.pointerType === "mouse" ? k : snapToPiece(drag.from, k));
}, { capture: true });
wrap.addEventListener("pointerup", (e) => {
  if (drag.id !== e.pointerId) return;
  const from = drag.from, upKey = keyAt(e.clientX, e.clientY);
  const to = e.pointerType === "mouse" ? upKey : aimedEnd(from, drag.trail, upKey, performance.now());
  drag.from = drag.id = null;
  preview(null);
  if (!drawingNow() || !from) return;
  const how = e.pointerType === "touch" ? "touch" : e.pointerType === "pen" ? "pen" : "mouse-left";
  if (to && to !== from) { setPick(null); toggleArrow(from, to, `${how}-drag`, to !== upKey ? { lifted_on: upKey } : {}); return; }
  // a tap. In the marking step a tap on his piece puts a circle on it (a chance) or takes it off, and starts an arrow
  // from it: a tap next on your piece or an empty square turns that circle into the arrow (a threat)
  // in step 1 a tap on his piece puts a circle on it or takes it off, and starts an arrow from it: a tap next on
  // your piece or an empty square turns that circle into the arrow (his threat); after a tap on your piece it is
  // your move
  if (S.pick === from) { if (S.pickCircled) toggleCircle(from, `${how}-tap`); setPick(null); return; }
  if (S.phase === "draw" && his(from) && !(S.pick && mine(S.pick))) {
    const on = toggleCircle(from, `${how}-tap`);
    setPick(on ? from : null, on);
    return;
  }
  if (!S.pick) { setPick(from); return; }
  const a = S.pick, circled = S.pickCircled;
  setPick(null);
  if (circled) setArrows(arrows.filter((x) => !(isCircle(x) && x.from === a))); // the circle becomes the arrow
  toggleArrow(a, from, `${how}-tap`);
}, { capture: true });
wrap.addEventListener("pointercancel", () => { drag.from = drag.id = null; preview(null); }, { capture: true });
// chessground listens to mouse/touch events: keep the left button and touches away from it
for (const t of ["mousedown", "touchstart"]) {
  wrap.addEventListener(t, (e) => { if (t === "touchstart" || e.button === 0) e.stopPropagation(); }, { capture: true, passive: true });
}
wrap.addEventListener("contextmenu", (e) => e.preventDefault());

// ---------- the strip over the board: the series, what to do now (or how it went) and the one button ----------
// orange — draw his threats (orange arrows); blue — draw your move (the blue arrow); green / red — the review;
// grey — wait. While you draw, a frame of the strip's colour runs inside the board.
const bm = { el: $("#board-mode"), dots: $("#bm-dots"), text: $("#bm-text"), undo: $("#bm-undo"), btn: $("#bm-btn") };
let bmAction = null;
function syncMode() {
  if (!bm.el) return;
  const ph = S.phase, rv = ph === "review" ? S.review : null;
  let kind = "", text = "", label = "", act = null;
  if (ph === "wait") { kind = "wait"; text = S.waitText || ""; }
  else if (ph === "draw") { kind = "threats"; text = "Угрозы и шансы"; label = arrows.length ? "Готово" : "Ничего нет"; act = check; }
  else if (ph === "plan") {
    if (myMove) { kind = "threats"; text = "Угрозы после хода"; label = "Хожу"; act = commitMove; }
    else { kind = "move"; text = S.v?.check ? "Шах! Твой ход" : "Твой ход"; label = "Хожу"; }
  } else if (ph === "review") {
    if (!rv) { kind = "wait"; text = "Проверяю…"; }
    else { kind = rv.kind; text = rv.text; label = rv.label; act = rv.onNext; }
  }
  bm.el.className = `board-mode${kind ? ` ${kind}` : ""}`;
  bm.text.textContent = text;
  if (rv?.chip) {
    const c = document.createElement("span");
    c.className = "lvl";
    c.textContent = rv.chip.text;
    c.title = rv.chip.title;
    bm.text.append(c);
  }
  bm.btn.hidden = !label;
  bm.btn.innerHTML = label ? `<span>${label}</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg>` : "";
  bm.btn.disabled = !act;
  bmAction = act;
  bm.undo.hidden = !(drawingNow() && (arrows.length || (ph === "plan" && myMove)));
  const d = dots();
  bm.dots.innerHTML = d.html;
  bm.dots.classList.toggle("dense", d.n > 15);
  bm.dots.setAttribute("aria-label", d.n ? `серия: ${d.done} из ${d.n}` : "");
  wrap.classList.toggle("mode-threats", kind === "threats");
  wrap.classList.toggle("mode-move", kind === "move");
  wrap.classList.toggle("mode-chances", kind === "chances");
}
bm.btn.addEventListener("click", () => { if (bmAction && !bm.btn.disabled) bmAction(); });
bm.undo.addEventListener("click", () => undoArrow());
// the series: a dot a turn — filled when everything of the turn was right
function dots() {
  const v = S.v;
  if (!S.sid || !v?.series) return { html: "", n: 0, done: 0 };
  const n = v.series.length, marks = S.marks || [], active = S.phase !== "review";
  let html = "";
  for (let i = 0; i < n; i++) {
    const cls = i < marks.length ? (marks[i] ? "ok" : "miss") : i === marks.length && active ? "now" : "todo";
    html += `<i class="dot ${cls}"></i>`;
  }
  return { html, n, done: marks.length };
}
// the review on the board: a round mark in the middle of an arrow — ✓ found, ✕ extra, ! missed
const MARK = {
  ok: { fill: "#2c8a3e", icon: '<path d="M-8 1l5.5 5.5L9-6" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>' },
  extra: { fill: "#6f6f6f", icon: '<path d="M-6.5-6.5l13 13M6.5-6.5l-13 13" stroke="#fff" stroke-width="5" stroke-linecap="round"/>' },
  miss: { fill: "#d13b25", icon: '<path d="M0-10v9" stroke="#fff" stroke-width="5" stroke-linecap="round"/><circle cy="8.5" r="3" fill="#fff"/>' },
};
function marked(shape, kind) {
  return badge(shape, MARK[kind].fill, MARK[kind].icon);
}
// the mark of a circle: in the top right corner of its square, smaller
function cornerMark(shape, kind) {
  const m = MARK[kind];
  return { ...shape, customSvg: { html: `<g transform="translate(80 20) scale(.8)"><circle r="19" fill="${m.fill}" stroke="#fff" stroke-width="4"/>${m.icon}</g>` } };
}
// a step of a line, numbered: 1 his move, 2 your answer, 3 his…
function numbered(shape, n, fill) {
  return badge(shape, fill, `<text y="8" text-anchor="middle" font-size="24" font-weight="700" fill="#fff" font-family="-apple-system, system-ui, sans-serif">${n}</text>`);
}
function badge(shape, fill, inner) {
  // chessground draws a custom svg in the square of `orig` (100 units a square); the mark goes halfway to `dest`
  const sign = cg.state.orientation === "white" ? 1 : -1;
  const dx = (shape.dest.charCodeAt(0) - shape.orig.charCodeAt(0)) * sign, dy = (+shape.orig[1] - +shape.dest[1]) * sign;
  return { ...shape, customSvg: { html: `<g transform="translate(${50 + dx * 50} ${50 + dy * 50})"><circle r="19" fill="${fill}" stroke="#fff" stroke-width="4"/>${inner}</g>` } };
}
// a line of moves, numbered: his in red, yours in blue (a capture that only looks fair, a fork with check, a mate)
// what kind of blow a missed move is, as a sign on its arrow instead of «!»: + check, # mate, a fork's prongs,
// a chain for a pin, an eye for a discovered attack
const MOTIF_ICON = {
  "шах": '<path d="M0-9.5v19M-9.5 0h19" stroke="#fff" stroke-width="5" stroke-linecap="round"/>',
  "мат": '<path d="M-4-10v20M4-10v20M-10-4h20M-10 4h20" stroke="#fff" stroke-width="3.6" stroke-linecap="round"/>',
  // a table fork: three tines, the neck, the handle (not a Y-shaped road fork)
  "вилка": '<path d="M-6-12v6.5M0-12v6.5M6-12v6.5M-6-5.5C-6 0 6 0 6-5.5M0-1.5V12" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>',
  "связка": '<g transform="rotate(-35)" fill="none" stroke="#fff" stroke-width="3.6"><rect x="-12" y="-5" width="14" height="10" rx="5"/><rect x="-2" y="-5" width="14" height="10" rx="5"/></g>',
  "вскрытое": '<path d="M-11.5 0C-6-7.5 6-7.5 11.5 0C6 7.5-6 7.5-11.5 0Z" fill="none" stroke="#fff" stroke-width="3.4"/><circle r="3.6" fill="#fff"/>',
};
const motifMark = (shape, motif, fill = "#d13b25") => badge(shape, fill, MOTIF_ICON[motif] || MARK.miss.icon);
// a line of moves, numbered 1, 2, 3…; the first move also carries the sign of what it is (a fork, a check…) near its
// head
function lineShapes(line, motif = "") {
  const out = line.map((st, i) => numbered({ orig: st.from, dest: st.to, brush: st.who === "you" ? "paleBlue" : "red" },
    i + 1, st.who === "you" ? "#2a78d6" : "#d13b25"));
  if (line.length && MOTIF_ICON[motif]) out.push(signNear(line[0], motif, line[0].who === "you" ? "#2a78d6" : "#d13b25"));
  return out;
}
// a small sign three quarters of the way along a move, clear of its number in the middle
function signNear(st, motif, fill) {
  const sign = cg.state.orientation === "white" ? 1 : -1;
  const dx = (st.to.charCodeAt(0) - st.from.charCodeAt(0)) * sign, dy = (+st.from[1] - +st.to[1]) * sign;
  return { orig: st.from, dest: st.to, customSvg: { html: `<g transform="translate(${50 + dx * 78} ${50 + dy * 78}) scale(.72)"><circle r="19" fill="${fill}" stroke="#fff" stroke-width="4"/>${MOTIF_ICON[motif]}</g>` } };
}
// a missed tactic of his after your move (vision/play.commit): his move in red with «!» and pale arrows to what it
// hits; a capture that only looks like a fair trade gets its whole line, numbered — 1 his capture, 2 your taking
// back (blue), 3 his answer and what it hits (Bxc4, Nxc4, Nxc2+ forking king and rook)
function attackShapes(at) {
  const hits = (st) => {
    const out = (st.targets || []).map((t) => ({ orig: st.to, dest: t, brush: "paleRed" }));
    const king = st.check && [...cg.state.pieces].find(([, p]) => p.role === "king" && p.color === S.v?.orientation);
    if (king) out.push({ orig: st.to, dest: king[0], brush: "paleRed" });
    return out;
  };
  const line = at.line || [];
  if (line.length) return [...lineShapes(line, at.motif), ...hits(line[line.length - 1])];
  const m = at.moves[0];
  return [motifMark({ orig: m.from, dest: m.to, brush: "red" }, at.motif), ...hits({ to: m.to, targets: at.targets, check: at.check })];
}
// while you play, the side panel goes away: only the board, the strip over it and ✕ in the header
function setPlaying(on) {
  const app = $(".app");
  $("#exit").hidden = !on;
  $("#moves").hidden = !on;
  if (app.classList.contains("playing") === on) return;
  app.classList.toggle("playing", on);
  layout();
}

// ---------- the coordinates on the frame: files above and below, ranks on both sides, turned with the board ----------
let coordsFor = "";
function renderCoords() {
  const o = cg.state.orientation;
  if (o === coordsFor) return;
  coordsFor = o;
  const files = o === "white" ? "abcdefgh" : "hgfedcba", ranks = o === "white" ? "87654321" : "12345678";
  const spans = (s) => [...s].map((c) => `<span>${c}</span>`).join("");
  for (const id of ["#fc-top", "#fc-bottom"]) $(id).innerHTML = spans(files);
  for (const id of ["#fc-left", "#fc-right"]) $(id).innerHTML = spans(ranks);
}

// ---------- the moves of the game fragment, on the right, as on Lichess: number, white, black; the last one lit ----------
const MOVES_W = 232;
// a piece letter of SAN as a small Merida piece, as the board draws it (Nf3 → [knight]f3)
const figurine = (san) => esc(san).replace(/[KQRBN]/g, (c) => `<i class="fig ${c}" aria-label="${c}"></i>`);
// a move as it is shown: `before` is the FEN it was made in (its number and whose move it was)
function addMove(before, san) {
  const [, turn, , , , full] = before.split(" ");
  S.moves = [...(S.moves || []), { n: +full || 1, white: turn === "w", san }];
  renderMoves();
}
function renderMoves() {
  const list = $("#moves-list");
  if (!list) return;
  const rows = [];
  for (const [i, m] of (S.moves || []).entries()) {
    let row = rows[rows.length - 1];
    if (!row || row.n !== m.n || (m.white && row.w)) rows.push((row = { n: m.n }));
    row[m.white ? "w" : "b"] = { san: m.san, last: i === S.moves.length - 1 };
  }
  const cell = (m, empty) => (m ? `<span class="mv${m.last ? " last" : ""}">${figurine(m.san)}</span>` : `<span class="mv empty">${empty}</span>`);
  list.innerHTML = rows.map((r) => `<div class="mrow"><span class="mn">${r.n}</span>${cell(r.w, r.b ? "…" : "")}${cell(r.b, "")}</div>`).join("");
  list.scrollTop = list.scrollHeight;
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// ---------- board size: as big as the window allows ----------
// The panel beside the board goes where the board stays biggest: on the right of a wide window, under it in a tall
// one — the moves while you play, the rating and the charts on the start screen.
const MOVES_H = 132; // the moves under the board: about four lines
const PANEL_H = 360; // the start screen's panel under the board
function layout() {
  const app = $(".app"), playing = app.classList.contains("playing");
  const top = $(".top").getBoundingClientRect().height;
  // the strip over the board is there only while you play; on the start screen the board starts level with the panel
  const strip = playing ? $("#board-mode").getBoundingClientRect().height + 6 : 0;
  const H = window.innerHeight - top - 28 - strip, W = window.innerWidth - 28;
  const fit = (h, w) => Math.max(240, Math.floor(Math.min(h, w) / 8) * 8); // the board with its frame
  let size, below = false;
  if (playing) {
    const right = fit(H, W - MOVES_W - 14), under = fit(H - MOVES_H - 14, W);
    below = under > right;
    size = below ? under : right;
  } else {
    // under the board the panel lays its content out in two columns (rating and button | charts): PANEL_H is enough
    const sideW = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--side-w"));
    const right = fit(H, W - sideW - 14), under = fit(H - PANEL_H - 14, W);
    below = under > right;
    size = below ? under : right;
    const side = $(".side");
    side.style.width = below ? `${size}px` : "";
    side.style.maxHeight = below ? `${Math.max(PANEL_H, H - size - 14)}px` : ""; // as tall as its content, at most this
  }
  app.classList.toggle("moves-below", playing && below);
  app.classList.toggle("side-below", !playing && below);
  // the frame: a little of the size on each side; the squares get the rest, a multiple of 8
  const frame = Math.min(30, Math.max(18, Math.round(size * 0.036)));
  size = Math.floor((size - 2 * frame) / 8) * 8 + 2 * frame;
  wrap.style.setProperty("--frame", `${frame}px`);
  wrap.style.setProperty("--coord", `${Math.round(frame * 0.56)}px`);
  renderCoords();
  wrap.style.width = wrap.style.height = `${size}px`;
  // the start screen's panel beside the board: exactly as tall, top and bottom level
  if (!playing) $(".side").style.height = below ? "" : `${size}px`;
  $("#board-mode").style.width = `${size}px`;
  const moves = $("#moves");
  moves.style.width = below ? `${size}px` : "";
  // on the right: as tall as strip and board; under it: what is left of the window, four lines at least
  moves.style.height = below ? `${Math.max(MOVES_H, H - size - 14)}px` : `${size + strip - 6}px`;
  cg.redrawAll();
  if (S.phase === "start" || S.phase === "summary") drawCharts();
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
  if (!idle && drawingNow() && performance.now() - lastActive > IDLE_MS) {
    idle = true;
    log("idle", { ms: Math.round(performance.now() - lastActive), phase: S.phase });
  }
}, 2000);

// ---------- screens ----------
const plural = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
};

let stateTimer = null;
let progress = null; // GET /api/progress: {start, rating, points: [{session, ts, rating, n, median_s}]}

// ---------- charts: hand-drawn SVG, one series each (no legend: the title names it), hover shows the values ----------
const fmtDate = (ts) => new Date(ts).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
function niceRange(vals, minSpan, step, floor = -Infinity) {
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < minSpan) { const m = (hi + lo) / 2; lo = m - minSpan / 2; hi = m + minSpan / 2; }
  if (lo < floor) { hi += floor - lo; lo = floor; }
  return [Math.floor(lo / step) * step, Math.ceil(hi / step) * step];
}
function lineChart(el, pts, o) {
  el.textContent = "";
  const vals = pts.map((p) => p[o.key]);
  const W = Math.max(200, el.clientWidth || el.parentElement.clientWidth - 36), H = o.height, L = 40, R = 14, T = 12, B = 8;
  const [lo, hi] = niceRange(vals, o.minSpan, o.step, o.floor);
  const x = (i) => (pts.length === 1 ? (L + W - R) / 2 : L + (i * (W - L - R)) / (pts.length - 1));
  const y = (v) => T + ((hi - v) / (hi - lo || 1)) * (H - T - B);
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W);
  svg.setAttribute("height", H);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${o.title}: последнее ${o.fmt(vals[vals.length - 1])}`);
  const add = (tag, attrs, parent = svg) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); parent.appendChild(n); return n; };
  for (const v of [lo, (lo + hi) / 2, hi]) {
    add("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" });
    add("text", { x: L - 6, y: y(v) + 4, class: "tick", "text-anchor": "end" }).textContent = o.fmt(v);
  }
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[o.key]).toFixed(1)}`).join(" ");
  if (pts.length > 1) {
    add("path", { d: `${d} L${x(pts.length - 1)},${H - B} L${x(0)},${H - B} Z`, class: "area" });
    add("path", { d, class: "line" });
  }
  // a long history: the line alone, with the last point marked (dots would merge into a smear)
  pts.forEach((p, i) => { if (pts.length <= 60 || i === pts.length - 1) add("circle", { cx: x(i), cy: y(p[o.key]), r: i === pts.length - 1 ? 5 : 4, class: i === pts.length - 1 ? "dot last" : "dot" }); });
  const cross = add("line", { x1: 0, x2: 0, y1: T, y2: H - B, class: "cross", visibility: "hidden" });
  const head = document.createElement("div");
  head.className = "chart-head";
  const title = document.createElement("span");
  title.textContent = o.title;
  const now = document.createElement("b");
  now.textContent = o.fmt(vals[vals.length - 1]);
  head.append(title, now);
  const tip = document.createElement("div");
  tip.className = "tip";
  tip.hidden = true;
  el.append(head, svg, tip);
  svg.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    let best = 0;
    pts.forEach((_, i) => { if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i; });
    const p = pts[best];
    cross.setAttribute("x1", x(best)); cross.setAttribute("x2", x(best)); cross.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.textContent = "";
    const v = document.createElement("b"); v.textContent = o.fmt(p[o.key]);
    const sub = document.createElement("span"); sub.textContent = fmtDate(p.ts);
    tip.append(v, sub);
    const left = Math.min(Math.max(0, (x(best) / W) * r.width - 50), r.width - 110);
    tip.style.left = `${left}px`;
  });
  svg.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); tip.hidden = true; });
}
function drawCharts() {
  const pts = progress?.points || [];
  const c1 = $("#ch-rating"), c2 = $("#ch-time");
  if (!c1) return;
  if (pts.length < 2) {
    c1.innerHTML = `<p class="muted">График появится после двух позиций.</p>`;
    if (c2) c2.textContent = "";
    return;
  }
  lineChart(c1, pts, { key: "rating", height: 150, title: "По позициям", fmt: (v) => String(Math.round(v)), minSpan: 80, step: 20 });
  const tp = pts.filter((p) => p.median_s != null);
  if (c2 && tp.length > 1) lineChart(c2, tp, { key: "median_s", height: 80, title: "Секунд на позицию", fmt: (v) => String(Math.round(v)), minSpan: 6, step: 2, floor: 0 });
}
function heroHTML(sub) {
  const pts = progress?.points || [];
  const now = progress?.rating ?? 1500;
  const prev = pts.length > 1 ? pts[pts.length - 2].rating : progress?.start ?? 1500;
  const delta = pts.length ? now - prev : 0;
  const cls = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
  const mark = delta > 0 ? "▲" : delta < 0 ? "▼" : "•";
  const deltaText = pts.length ? `${mark} ${delta > 0 ? "+" : delta < 0 ? "−" : "±"}${Math.abs(delta)} ${sub}` : "стартовый рейтинг";
  return `<div class="hero"><div class="hero-num">${now}</div><div class="hero-sub">рейтинг внимательности</div><div class="delta ${cls}">${deltaText}</div></div>`;
}
async function loadProgress() {
  try { progress = await api("/api/progress"); } catch { progress = null; }
}

const RULES = `
  <details class="rules"><summary>Как это работает</summary>
    <p>Соперник делает ход. 1) Нарисуй его угрозы сейчас — стрелки от его фигур к твоим, которые он может выиграть, и на поле мата. И тапни каждую его фигуру, которую ты можешь выиграть прямо сейчас (взять и остаться в плюсе после всех разменов, или выиграть шахом с вилкой; на Mac — или правый клик): на ней появится фиолетовый кружок. Твой мат, в том числе в 2–3 хода, — кружок на его короле. 2) Нарисуй свой ход — стрелку от своей фигуры. 3) Нарисуй его угрозы после твоего хода — стрелки от его фигур: каждый его шах (стрелку хода с шахом), что он сможет взять с выигрышем, мат, вилки и связки, которые выигрывают материал. 4) «Хожу». Ошибки, если они есть, покажутся все вместе, потом ходит соперник. Значки на пропущенных ходах: + шах, # мат, вилка (столовая) — вилка, цепочка — связка, глаз — вскрытое нападение, ! — взятие.</p>
    <p>Стрелка: протяни мышью (любой кнопкой) или пальцем, или коснись одной клетки, потом другой. Промахнулся — ↶ над доской, Backspace или та же стрелка ещё раз. Закончил — кнопка в цветной полосе над доской или Enter.</p>
    <p>Рейтинг растёт за верные ответы и сильнее — на трудных позициях. Повторы позиций не считаются.</p>
    <p>Серия: соперник ходит — отмечаешь угрозы — ходишь сам — отвечает бот уровня 1300 — снова отмечаешь… Серия без промахов делает следующую на ход длиннее, две серии с промахами подряд — на ход короче. Длина 1 — одна позиция.</p>
  </details>`;

async function showStart() {
  S.v = null;
  S.sid = null;
  S.review = null;
  S.phase = "start";
  setPlaying(false);

  arrows = [];
  setPick(null);
  S.reviewShapes = null;
  wrap.classList.add("locked");
  lockBoard();
  cg.set({ fen: "start", orientation: "white", lastMove: undefined, check: false });
  renderCoords();
  cg.setShapes([]);
  cg.setAutoShapes([]);
  await loadProgress();
  panel.innerHTML = `
    ${heroHTML("за последнюю позицию")}
    <div class="chart" id="ch-rating"></div>
    <div class="chart" id="ch-time"></div>
    <button class="btn primary big" id="go" disabled>Начать</button>
    <p class="muted center" id="level"></p>
    ${RULES}`;
  $("#go").addEventListener("click", startPlay);
  layout(); // the panel's height is known now: beside the board or under it (and the charts drawn at its width)
  await refreshState();
}

async function refreshState() {
  clearTimeout(stateTimer);
  let st;
  try { st = await api("/api/state"); } catch (e) {
    // asked again until it answers: the Mac app may be restarting, and the note must go once it is back
    statusEl.textContent = "сервер не отвечает"; statusEl.className = "status bad";
    stateTimer = setTimeout(refreshState, 5000);
    return;
  }
  const imp = st.import;
  statusEl.className = "status";
  statusEl.innerHTML = imp.running ? `<span class="spin"></span>готовлю позиции` : "";
  const go = $("#go");
  if (S.phase === "start" && go) {
    go.disabled = !st.ready;
    go.textContent = st.ready ? "Начать" : "Готовлю позиции…";
    const lv = $("#level");
    if (lv) lv.textContent = `Длина серии: ${st.level} ${plural(st.level, "ход", "хода", "ходов")}${st.best ? ` · рекорд ${st.best}` : ""}`;
  }
  if (imp.running) stateTimer = setTimeout(refreshState, 3000);
}

// ---------- the training: series of checks (vision/play.py) ----------
// A sitting is about 15 checks, made of series; a series is a fragment of a game: the opponent's move, your check,
// your move, the bot's move, your check… as many checks as your level (1 = one position, one move).
// Everything happens on the board: the strip over it says what to do and holds the one button (syncMode above).
async function leaveSession(how = "button") {
  if (!S.sid || S.phase === "start" || S.phase === "summary") return showStart();
  const sid = S.sid;
  log("leave_click", { how, phase: S.phase, ms: ms(), arrows: arrows.length });
  S.phase = "start";
  try { await api("/api/play/leave", { play: sid }); } catch {}
  showStart();
}
$("#exit").addEventListener("click", () => leaveSession("button"));
document.querySelector(".title").addEventListener("click", () => leaveSession("title"));

function startDrawing(item) {
  S.phase = "draw";
  S.shownAt = performance.now();
  lastActive = performance.now();
  renderArrows();
  log("move_shown", { item });
}

function lockBoard() {
  cg.set({ movable: { color: undefined, dests: new Map() }, draggable: { enabled: false }, selectable: { enabled: false } });
}

async function startPlay() {
  const b = $("#go");
  if (b) b.disabled = true;
  let v;
  try { v = await api("/api/play/start", {}); } catch (e) { if (b) { b.disabled = false; b.textContent = e.message; } return; }
  Object.assign(S, { sid: v.play, reviewShapes: null });
  playShow(v);
}

// a new series or the bot's answer: the move is shown, then the next step
function playShow(v) {
  S.v = v;
  S.index = v.checks;
  S.size = v.budget;
  setPlaying(true);
  lockBoard();
  arrows = [];
  myMove = null;
  setPick(null);
  S.reviewShapes = null;
  S.review = null;
  S.step1 = null;
  S.step1ok = null;
  wrap.classList.remove("locked");
  cg.setShapes([]);
  cg.setAutoShapes([]);
  if (v.new_series) { S.marks = []; S.moves = []; renderMoves(); }
  if (!v.bot) return playStep(v);
  const note = S.note || "";
  S.note = "";
  S.waitText = note || (v.new_series ? "Соперник ходит…" : "Бот ходит…");
  S.phase = "wait";
  if (v.new_series) {
    cg.set({ animation: { enabled: false } });
    cg.set({ fen: v.bot.before, orientation: v.orientation, lastMove: undefined, check: false });
    renderCoords();
    cg.set({ animation: { enabled: true, duration: 250 } });
  } else cg.set({ fen: v.bot.before });
  setTimeout(() => {
    if (S.v !== v) return;
    cg.set({ fen: v.fen, lastMove: [v.bot.move.slice(0, 2), v.bot.move.slice(2, 4)], turnColor: v.orientation, check: !!v.check });
    play(v.bot.capture ? "Capture" : "Move");
    addMove(v.bot.before, v.bot.san);
    setTimeout(() => { if (S.v === v) playStep(v); }, 280);
  }, (v.new_series ? 450 : 400) + (note ? 900 : 0));
}
function playStep(v) {
  if (v.phase === "check") return startDrawing(null);
  if (v.phase === "plan") return startPlan(v);
  if (v.phase === "next") return seriesEnd(v, null);
}
// «проверь свой ход»: draw your move (blue), then what he can do after it (orange); «Хожу» checks and plays it.
// `hints`: the threats you were just shown, pale, until you draw your move
function startPlan(v, hints = []) {
  myMove = null;
  arrows = [];
  setPick(null);
  S.reviewShapes = null;
  wrap.classList.remove("locked");
  cg.setShapes([]);
  cg.setAutoShapes(hints);
  S.shownAt = performance.now();
  lastActive = performance.now();
  S.phase = "plan";
}
async function commitMove() {
  if (S.phase !== "plan" || !myMove) return;
  const took = ms(), mv = myMove, drawn = arrows;
  S.review = null;
  S.phase = "review";
  const capture = !!cg.state.pieces.get(mv.to);
  let v;
  try { v = await api("/api/play/commit", { play: S.sid, move: mv.from + mv.to, arrows: drawn, ms: took }); }
  catch (e) { S.phase = "plan"; flashTask(e.message); return; }
  S.v = v;
  S.marks = [...(S.marks || []), (S.step1ok ?? true) && !!v.own.result.ok]; // one dot a turn: both steps right
  S.step1ok = null;
  myMove = null;
  arrows = [];
  wrap.classList.add("locked");
  // the board: your move played; your arrows (✓ green right, ✕ grey extra); what you missed in red with !
  cg.set({ fen: v.fen, lastMove: [mv.from, mv.to], turnColor: v.orientation === "white" ? "black" : "white" });
  play(capture ? "Capture" : "Move");
  if (v.own?.san && v.own?.before) addMove(v.own.before, v.own.san);
  const res = v.own.result, found = new Set(res.found);
  const user = drawn.map((a) => {
    const extra = res.extra.some((x) => x.from === a.from && x.to === a.to);
    return marked({ orig: a.from, dest: a.to, brush: extra ? "grey" : "green" }, extra ? "extra" : "ok");
  });
  const auto = [];
  // his taking back where you have just taken (optional): pale, from every piece that can, never a miss; a check
  // that only loses the checking piece is not shown at all when left out — there is nothing to learn from it
  for (const t of v.own.threats) {
    if (found.has(t.target) || (t.optional && t.kind === "check")) continue;
    auto.push(...(t.optional ? missedArrows(t, false, "paleOrange") : missedArrows(t, true)));
  }
  for (const at of v.own.attacks) if (!found.has(at.key)) auto.push(...attackShapes(at));
  if (res.ok) return goOn(v); // all seen: the opponent answers at once
  const shapes = user;
  S.reviewShapes = shapes;
  cg.setShapes(shapes);
  cg.setAutoShapes(auto);
  const n = v.own.threats.filter((t) => !t.optional).length + v.own.attacks.length;
  const review = () => { S.review = { kind: "fail", text: verdict(res, n), label: "Дальше", onNext: nextSeries }; };
  if (v.phase === "next") return seriesEnd(v, review);
  review();
  syncMode();
}
// the strip's word on a check: short, the board shows the rest
function verdict(res, n) {
  if (res.ok) return "Верно!";
  if (!n) return "Угроз не было";
  if (!res.missed.length) return "Лишняя отметка";
  return `${n - res.missed.length} из ${n}`;
}
// a chance of yours on the board: a circle on his piece and an arrow from your piece that wins it (a mate or a
// queening on an empty square: the move itself); `mark`: the «!» of a missed one; `pale`: a hint
function chanceShapes(t, mark = false, pale = false) {
  if (t.tactic) return pale ? [{ orig: t.line[0].from, dest: t.line[0].to, brush: "paleChance" }] : lineShapes(t.line, t.motif);
  const best = { orig: t.best.slice(0, 2), dest: t.best.slice(2, 4) };
  if (!t.promotion && cg.state.pieces.get(t.target)) {
    const circle = { orig: t.target, brush: pale ? "paleChance" : "red" };
    return [mark ? cornerMark(circle, "miss") : circle, { ...best, brush: pale ? "paleChance" : "paleRed" }];
  }
  const arrow = { ...best, brush: pale ? "paleChance" : "red" };
  return [mark ? marked(arrow, "miss") : arrow];
}
// a threat on the board: an arrow from every piece that can take there (a mate: the mating move);
// `mark`: the first one gets the «!» of a missed threat
function missedArrows(t, mark = false, brush = "red") {
  // one of his checks after your move: the checking move itself
  if (t.kind === "check") { const a = { orig: t.line[0].from, dest: t.line[0].to, brush }; return [mark ? motifMark(a, t.motif || "шах") : a]; }
  // the engine's tactic (a forced mate, a check that wins): its whole line, numbered; as a hint its first move
  if (t.tactic) return brush === "red" ? lineShapes(t.line, t.motif) : [{ orig: t.line[0].from, dest: t.line[0].to, brush }];
  const list = t.piece === null && t.mate ? [{ orig: t.best.slice(0, 2), dest: t.best.slice(2, 4), brush }]
    : [...(t.attackers || [t.best.slice(0, 2)]), ...(t.xray || [])].map((a) => ({ orig: a, dest: t.target, brush }));
  if (mark && list.length) list[0] = t.mate ? motifMark(list[0], "мат") : marked(list[0], "miss");
  return list;
}

// step 1 done («Ничего нет» / «Готово», or your move drawn at once): checked at once, before your move. Right: on to
// your move (the one drawn, if any), your circles stay pale. A mistake: the review first, then «Дальше» — or drawing on
// the board — starts your move
async function check(move = null) {
  if (S.phase !== "draw") return;
  const took = ms(), marks = arrows;
  setPick(null);
  if (move) cg.setShapes([...toShapes(arrows), { orig: move.from, dest: move.to, brush: "blue" }]);
  S.review = null;
  S.phase = "review";
  let a;
  try { a = await api("/api/play/answer", { play: S.sid, arrows: marks, ms: took, ...(move ? { move: move.from + move.to } : {}) }); }
  catch (e) { S.phase = "draw"; setArrows(marks); flashTask(`Не получилось: ${e.message}`); return; }
  S.v = a;
  S.step1ok = !!a.result.ok; // the turn's dot waits for your move: filled when both steps are right
  const r = { result: a.result, threats: a.threats || [], chances: a.chances || [] };
  if (a.phase !== "plan") { // the game is over in that position: no move to make
    S.marks = [...(S.marks || []), S.step1ok];
    S.step1ok = null;
    if (a.result.ok) return goOn(a);
    return seriesEnd(a, () => showReview(r, nextSeries));
  }
  if (a.result.ok) {
    startPlan(a, []);
    S.step1 = { marks };
    setArrows([]);
    if (move) tryMove(move.from, move.to, `${move.how}-early`);
    return;
  }
  const toPlan = (how) => { log("next_click", { ms: ms(), to: "plan", how }); startPlan(a, []); };
  showReview(r, () => toPlan("button"));
  S.review.toPlan = toPlan;
}
// a clean check is not stopped on: its dot fills and the game goes on (a new series says its length in the strip);
// only a check with a mistake stops for the review
function goOn(v) {
  S.note = seriesNote(v);
  nextSeries("auto");
}
function seriesNote(v) {
  const e = v.series_end;
  if (!e || !(e.length > 1 || e.next_level !== e.level_was)) return "";
  const mark = e.next_level > e.level_was ? "▲" : e.next_level < e.level_was ? "▼" : "↻";
  return `${e.clean ? "Серия пройдена" : "Серия с промахом"} · ${mark} ${e.next_level}`;
}
// the review before your move: drawing on the board starts your move at once — «Дальше» is not needed first
function planFromReview(how) {
  if (S.phase !== "review" || !S.review?.toPlan) return false;
  S.review.toPlan(`draw-${how}`);
  return true;
}
// the end of a series: the review (if any) and, in the strip, the next length (▲ longer, ▼ shorter, ↻ the same)
function seriesEnd(v, review) {
  S.phase = "review";
  wrap.classList.add("locked");
  if (review) review();
  const e = v.series_end;
  const lost = !!e?.lost;
  if (!review) S.review = { kind: lost ? "fail" : "end", text: "Серия закончена" };
  Object.assign(S.review, { label: v.last ? "Итог" : "Дальше", onNext: nextSeries });
  if (e && (e.length > 1 || e.next_level !== e.level_was)) {
    const up = e.next_level > e.level_was, down = e.next_level < e.level_was;
    const n = e.next_level, word = plural(n, "ход", "хода", "ходов");
    const title = e.clean ? "Серия пройдена!" : lost ? "Позиция проиграна" : e.game_over ? "Партия окончена" : "Серия с промахом";
    if (!review) S.review.text = title;
    S.review.chip = { text: `${up ? "▲" : down ? "▼" : "↻"} ${n}`, title: `${title} Дальше — ${n} ${word}` };
  }
  syncMode();
}
async function nextSeries(how = "button") {
  if (S.phase !== "review") return;
  log("next_click", { ms: ms(), how: typeof how === "string" ? how : "button" });
  S.reviewShapes = null;
  bm.btn.disabled = true;
  bmAction = null;
  let v;
  try { v = await api("/api/play/next", { play: S.sid }); } catch (e) { syncMode(); flashTask(e.message); return; }
  if (v.phase === "over") return showSummary(v.summary);
  playShow(v);
}

// the review of a check after his move: your right arrows green ✓, extra ones grey ✕, missed threats red ! from the
// attacker; the strip turns green or red with the count and «Дальше»
function showReview(r, onNext) {
  wrap.classList.add("locked");
  const res = r.result, found = new Set(res.found);
  const userShapes = arrows.map((a) => {
    const right = !res.extra.some((x) => x.from === a.from && x.to === a.to);
    if (isCircle(a)) return cornerMark({ orig: a.from, brush: right ? "green" : "grey" }, right ? "ok" : "extra");
    return marked({ orig: a.from, dest: a.to, brush: right ? "green" : "grey" }, right ? "ok" : "extra");
  });
  const auto = [];
  for (const t of r.threats) if (!t.optional && !found.has(t.target)) auto.push(...missedArrows(t, true));
  for (const t of r.chances) if (!t.optional && !found.has(t.target)) auto.push(...chanceShapes(t, true));
  S.reviewShapes = userShapes;
  cg.setShapes(userShapes);
  cg.setAutoShapes(auto);
  S.review = { kind: res.ok ? "ok" : "fail", text: verdict(res, res.n ?? r.threats.length), label: "Дальше", onNext };
  syncMode();
}

async function showSummary(s) {
  S.review = null;
  S.phase = "summary";
  setPlaying(false);
  S.reviewShapes = null;
  lockBoard();
  wrap.classList.add("locked");
  cg.setShapes([]);
  cg.setAutoShapes([]);
  await loadProgress();
  panel.innerHTML = `
    ${heroHTML("за эту сессию")}
    <p class="muted">Верно ${s.ok} из ${s.checks} · серий без промахов ${s.clean} из ${s.series} · длина серии ${s.level}</p>
    <div class="chart" id="ch-rating"></div>
    <div class="chart" id="ch-time"></div>
    <button class="btn primary big" id="again">Ещё</button>
    <button class="btn quiet" id="home">На главный</button>`;
  drawCharts();
  $("#again").addEventListener("click", async () => { $("#again").disabled = true; await startPlay(); });
  $("#home").addEventListener("click", showStart);
  $("#again").focus();
}

// ---------- keyboard: Enter or Space — the button in the strip (the main button on the start screen); Backspace or ⌘Z — take back the last arrow; ⇧Backspace — all;
// Esc — cancel a started tap; Esc twice — back to the start screen ----------
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && drawingNow()) {
    e.preventDefault();
    undoArrow();
    return;
  }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "Enter" || e.key === " ") {
    if (e.target instanceof HTMLButtonElement && e.target !== bm.btn) return; // a focused button does its own thing
    const b = $(".app").classList.contains("playing") ? (bm.btn.hidden || bm.btn.disabled ? null : bm.btn)
      : panel.querySelector(".btn.primary:not(:disabled)");
    if (b) { e.preventDefault(); b.click(); }
  } else if ((e.key === "Backspace" || e.key === "Delete") && drawingNow()) {
    e.preventDefault();
    if (e.shiftKey) clearArrows(); else undoArrow();
  } else if (e.key === "Escape") {
    if (S.pick) { setPick(null); return; }
    const now = performance.now();
    if (now - (S.lastEsc || 0) < 800) leaveSession("esc"); // Esc twice: back to the start screen
    S.lastEsc = now;
  }
});

// ---------- start ----------
const device = /iPad/.test(navigator.userAgent) || (navigator.maxTouchPoints > 1 && /Macintosh/.test(navigator.userAgent)) ? "iPad" : "Mac";
log("page_load", { device, w: window.innerWidth, h: window.innerHeight });
layout();
showStart();
