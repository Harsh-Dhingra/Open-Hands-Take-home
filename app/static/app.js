// Presentation only: every rule (turns, validity, win/draw, winning cells) comes from the server.
"use strict";

const $ = (sel) => document.querySelector(sel);
const POLL_MS = 1000;
const OFFLINE_MESSAGE = "Cannot reach the server. Retrying…";

let gameId = null;
let state = null;
let pollTimer = null;
let prevBoard = null; // for the pop animation on newly placed marks

const CFG = { rows: $("#cfg-rows"), cols: $("#cfg-cols"), k: $("#cfg-k") };

// --- helpers ---------------------------------------------------------------

async function api(path, options) {
  let res;
  try {
    res = await fetch(path, options);
  } catch {
    throw new Error(OFFLINE_MESSAGE);
  }
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok) {
    throw Object.assign(new Error(body?.error?.message || res.statusText), {
      code: body?.error?.code,
    });
  }
  return body;
}

function store(fn) { try { return fn(sessionStorage); } catch { return null; } }
const roleKey = (id) => `role:${id}`;
const selectedRole = () => document.querySelector('input[name="role"]:checked').value;
function setRole(role) {
  const radio = document.querySelector(`input[name="role"][value="${role}"]`);
  if (radio) radio.checked = true;
}
function showError(message) { $("#error").textContent = message || ""; }
function clearOfflineNotice() {
  if ($("#error").textContent === OFFLINE_MESSAGE) showError("");
}

// --- rendering -------------------------------------------------------------

function statusText(s) {
  if (s.status === "won") return `${s.winner} wins!`;
  if (s.status === "draw") return "It's a draw.";
  const role = selectedRole();
  if (role === "both") return `${s.next_player} to move`;
  return s.next_player === role ? `Your turn (${role})` : `Waiting for ${s.next_player}…`;
}

function render() {
  const s = state;
  const board = $("#board");
  board.style.setProperty("--cols", s.cols);
  $("#rules").textContent = `${s.rows}×${s.cols} board · ${s.k} in a row to win`;
  board.dataset.next = s.next_player || "";
  const win = new Set(s.winning_line.map(([r, c]) => `${r},${c}`));

  board.replaceChildren(
    ...s.board.flatMap((line, r) =>
      line.map((mark, c) => {
        const cell = document.createElement("button");
        cell.type = "button";
        cell.className = "cell" + (mark ? ` ${mark}` : " empty") + (win.has(`${r},${c}`) ? " win" : "");
        cell.textContent = mark || "";
        cell.disabled = s.status !== "in_progress" || mark !== null;
        cell.setAttribute("role", "gridcell");
        cell.setAttribute("aria-label", `Row ${r + 1}, column ${c + 1}, ${mark || "empty"}`);
        if (mark && prevBoard && prevBoard[r]?.[c] !== mark) cell.classList.add("pop");
        cell.addEventListener("click", () => play(r, c));
        return cell;
      }),
    ),
  );
  prevBoard = s.board;

  const status = $("#status");
  status.textContent = statusText(s);
  status.classList.toggle("won", s.status === "won");

  $("#share").hidden = false;
  const link = `${location.origin}${location.pathname}#${gameId}`;
  $("#share-link").textContent = link;
  $("#share-link").href = link;
}

// --- actions ---------------------------------------------------------------

function show(id, s) {
  gameId = id;
  state = s;
  render();
  watch(id, s);
}

// Only fields the user filled in are sent; the server applies defaults and validates ranges.
function configBody() {
  const body = {};
  for (const [name, input] of Object.entries(CFG)) {
    if (input.value !== "") body[name] = Number(input.value);
  }
  return body;
}

let limits = null;

// Hint only: the server stays the authority and still returns invalid_config for bad input.
function syncHint() {
  if (!limits) return;
  const rows = CFG.rows.valueAsNumber;
  const cols = CFG.cols.valueAsNumber;
  const longest = Math.max(rows, cols);
  if (Number.isFinite(longest)) CFG.k.max = longest;
  const kMax = Number.isFinite(longest) ? longest : "longest side";
  $("#cfg-hint").textContent =
    `Rows and columns: ${limits.min_size}–${limits.max_size}. In a row: ${limits.min_k}–${kMax}.`;
}

async function loadLimits() {
  try {
    const config = await api("/config");
    limits = config.limits;
    const { defaults } = config;
    for (const name of ["rows", "cols"]) {
      CFG[name].min = limits.min_size;
      CFG[name].max = limits.max_size;
      CFG[name].value = defaults[name];
    }
    CFG.k.min = limits.min_k;
    CFG.k.value = defaults.k;
    syncHint();
  } catch (e) {
    showError(e.message);
  }
}

async function newGame() {
  showError("");
  try {
    const s = await api("/games", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(configBody()),
    });
    store((st) => st.setItem(roleKey(s.id), selectedRole()));
    history.replaceState(null, "", `#${s.id}`);
    prevBoard = null;
    show(s.id, s);
  } catch (e) {
    showError(e.message);
  }
}

async function loadGame(id) {
  showError("");
  try {
    const s = await api(`/games/${encodeURIComponent(id)}`);
    // A newcomer to a shared link defaults to O; the creator's tab remembers X.
    setRole(store((st) => st.getItem(roleKey(id))) || "O");
    prevBoard = null;
    show(s.id, s);
  } catch (e) {
    showError(e.code === "not_found" ? `No game with id "${id}".` : e.message);
  }
}

let moving = false; // a move request is in flight: ignore further clicks (no double submit)

async function play(row, col) {
  if (moving) return;
  moving = true;
  showError("");
  const role = selectedRole();
  const player = role === "both" ? state.next_player : role;
  try {
    show(gameId, await api(`/games/${gameId}/moves`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      // Echo the version the server last told us about; the server decides if it is stale.
      body: JSON.stringify({ player, row, col, expected_version: state.version }),
    }));
  } catch (e) {
    if (e.code === "stale_version") {
      // Someone moved since we last looked. Show the latest position; do not retry for them.
      try {
        show(gameId, await api(`/games/${gameId}`));
        showError("The board changed while you were deciding. Here is the latest position.");
      } catch (e2) {
        showError(e2.message);
      }
    } else {
      showError(e.message);
      refresh(); // our view may be stale in some other way
    }
  } finally {
    moving = false;
  }
}

async function refresh() {
  if (!gameId) return;
  try {
    const s = await api(`/games/${gameId}`);
    clearOfflineNotice();
    if (s.version !== state.version || s.status !== state.status) show(gameId, s);
  } catch (e) {
    showError(e.message);
  }
}

function startPolling() {
  if (!pollTimer) pollTimer = setInterval(refresh, POLL_MS);
  setTransport("polling");
}
function stopPolling() { clearInterval(pollTimer); pollTimer = null; }

// --- live updates -----------------------------------------------------------
// Server-Sent Events push every change. Polling is only the fallback while the
// stream is down (EventSource reconnects by itself, sending Last-Event-ID so
// the server does not repeat what we already have).

let source = null;
let sourceGame = null;

function setTransport(name) { document.body.dataset.transport = name; }

function closeStream() {
  if (source) source.close();
  source = null;
  sourceGame = null;
}

function watch(id, s) {
  if (s.status !== "in_progress") {
    closeStream();
    stopPolling();
    setTransport("none");
    return;
  }
  if (source && sourceGame === id) return; // already following this game
  closeStream();
  if (typeof EventSource === "undefined") { startPolling(); return; }
  sourceGame = id;
  source = new EventSource(`/games/${encodeURIComponent(id)}/events`);
  source.addEventListener("state", (e) => {
    stopPolling();
    setTransport("sse");
    clearOfflineNotice(); // the stream is back
    const incoming = JSON.parse(e.data);
    if (incoming.id !== gameId) return;
    if (!state || incoming.version !== state.version || incoming.status !== state.status) {
      show(gameId, incoming); // our own move already updated state; this skips the echo
    }
  });
  source.addEventListener("end", closeStream);
  source.onerror = () => startPolling(); // the browser keeps retrying the stream meanwhile
}

// --- wiring ----------------------------------------------------------------

$("#new-game").addEventListener("click", newGame);
CFG.rows.addEventListener("input", syncHint);
CFG.cols.addEventListener("input", syncHint);

$("#join-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const raw = $("#join-id").value.trim();
  const id = raw.includes("#") ? raw.split("#").pop() : raw; // accept a pasted share link
  if (id) { history.replaceState(null, "", `#${id}`); loadGame(id); }
});

document.querySelectorAll('input[name="role"]').forEach((radio) =>
  radio.addEventListener("change", () => {
    if (gameId) { store((st) => st.setItem(roleKey(gameId), selectedRole())); render(); }
  }),
);

$("#copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#share-link").href); showError(""); }
  catch { showError("Copy failed; select the link and copy it manually."); }
});

addEventListener("hashchange", () => {
  const id = location.hash.slice(1);
  if (id && id !== gameId) loadGame(id);
});

loadLimits();
if (location.hash.length > 1) loadGame(location.hash.slice(1));
