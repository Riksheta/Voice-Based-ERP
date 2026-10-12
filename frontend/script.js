/* Roll Call frontend. Plain JavaScript, no build step.
   Every call to the server goes through api(), so the voice layer can reuse it later. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/* Build DOM safely. Strings become text nodes, so a student named "<img onerror=...>" stays inert. */
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else e.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    e.append(kid);
  }
  return e;
}

const STATUS_LABEL = { present: "Present", absent: "Absent", late: "Late" };
const NEXT = { present: "absent", absent: "late", late: "present" };
const PERIODS = [1, 2, 3, 4, 5, 6, 7, 8];

const state = { me: null, courses: [], view: "today" };
const att = { courseId: null, date: "", period: 1, students: [], markedPeriods: [], dirty: false };

/* ---------- helpers ---------- */

function todayISO() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  if (form) opts.body = form;
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* not JSON */ }
  if (res.status === 401 && path !== "/api/login" && path !== "/api/me") {
    showLogin();
    throw new Error("Your session ended. Sign in again.");
  }
  if (!res.ok) throw new Error((data && data.error) || `Something went wrong (${res.status}).`);
  return data;
}

let toastTimer;
function toast(msg, kind = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3800);
}

function empty(title, text) {
  return h("div", { class: "empty" }, h("h3", {}, title), h("p", {}, text));
}

function fillSelect(sel, items, labelFn, selected) {
  sel.replaceChildren(...items.map((i) => h("option", { value: i.id, selected: i.id === selected }, labelFn(i))));
}

/* ---------- auth ---------- */

function showLogin() {
  state.me = null;
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#email").focus();
}

async function showApp(me) {
  state.me = me;
  $("#login").hidden = true;
  $("#app").hidden = false;
  $("#whoName").textContent = me.name;
  state.courses = await api("/api/courses");
  go("today");
}

$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#loginError");
  err.hidden = true;
  try {
    const me = await api("/api/login", {
      method: "POST",
      body: { email: $("#email").value, password: $("#password").value },
    });
    $("#password").value = "";
    await showApp(me);
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  }
});

$("#logoutBtn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  showLogin();
});

/* ---------- navigation ---------- */

const TITLES = { today: "Today", attendance: "Attendance", students: "Students", reports: "Reports", import: "Import students" };
const LOADERS = {
  today: loadToday, attendance: () => { att.loading = Promise.resolve(setupAttendance()); return att.loading; }, students: setupStudents,
  reports: setupReports, import: () => { $("#importResult").replaceChildren(); },
};

function go(view) {
  if (att.dirty && state.view === "attendance" && view !== "attendance") {
    if (!confirm("You have unsaved attendance. Leave without saving?")) return;
    att.dirty = false;
  }
  state.view = view;
  $$(".view").forEach((v) => { v.hidden = v.id !== `view-${view}`; });
  $$("#nav button").forEach((b) => {
    if (b.dataset.view === view) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  $("#viewTitle").textContent = TITLES[view];
  Promise.resolve(LOADERS[view]()).catch((ex) => toast(ex.message, "error"));
}

$("#nav").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-view]");
  if (b) go(b.dataset.view);
});

/* ---------- Today ---------- */

async function loadToday() {
  const box = $("#view-today");
  const data = await api("/api/dashboard");
  const hour = new Date().getHours();
  const part = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  const first = state.me.name.replace(/^(Dr\.|Prof\.|Mr\.|Ms\.|Mrs\.)\s*/i, "");
  const dateText = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });

  box.replaceChildren(h("div", { class: "greeting" },
    h("h2", {}, `${part}, ${first}`), h("p", {}, dateText)));

  if (!data.courses.length) {
    box.append(empty("No courses assigned yet", "Ask your administrator to assign a course to your account."));
    return;
  }

  box.append(h("div", { class: "course-list" }, data.courses.map((c) => {
    const marked = c.today_periods.length > 0;
    return h("article", { class: "course" },
      h("div", {},
        h("h3", {}, c.name),
        h("p", { class: "meta" }, `${c.code}, ${c.class_name}, ${c.student_count} students`)),
      h("div", {},
        h("p", { class: "today-state " + (marked ? "done" : "todo") },
          marked ? `Marked for period ${c.today_periods.join(", ")}` : "Not marked today"),
        h("p", { class: "meta" }, `${c.sessions} ${c.sessions === 1 ? "session" : "sessions"} so far`)),
      h("div", { class: "stat " + (c.defaulters ? "warn" : "") },
        h("b", {}, c.average == null ? "No data" : `${c.average}%`),
        h("span", {}, c.defaulters ? `average, ${c.defaulters} below 75%` : "average attendance")),
      h("button", { class: "btn", type: "button", onclick: () => openAttendance(c.id) }, "Take attendance"));
  })));
}

function openAttendance(courseId) {
  att.courseId = courseId;
  go("attendance");
}

/* ---------- Attendance ---------- */

const tally = {};

function setupAttendance() {
  if (!state.courses.length) {
    $("#roster").replaceChildren(empty("No courses assigned yet", "Ask your administrator to assign a course to your account."));
    return;
  }
  if (!att.courseId) att.courseId = state.courses[0].id;
  if (!att.date) att.date = todayISO();
  fillSelect($("#attCourse"), state.courses, (c) => `${c.code}, ${c.name} (${c.class_name})`, att.courseId);
  $("#attDate").max = todayISO();
  $("#attDate").value = att.date;
  att.dirty = false;
  renderPeriods();
  return loadRoster();
}

function renderPeriods() {
  $("#periods").replaceChildren(...PERIODS.map((p) => {
    const done = att.markedPeriods.includes(p);
    return h("button", {
      type: "button", class: done ? "done" : "", "aria-pressed": String(p === att.period),
      "aria-label": `Period ${p}${done ? ", attendance saved" : ""}`, title: done ? `Period ${p} (saved)` : `Period ${p}`,
      onclick: () => changeControl(() => { att.period = p; }),
    }, String(p));
  }));
}

/* Ask before throwing away unsaved taps. */
function changeControl(apply, revert) {
  if (att.dirty && !confirm("You have unsaved attendance. Switch without saving?")) {
    if (revert) revert();
    return;
  }
  att.dirty = false;
  apply();
  renderPeriods();
  loadRoster().catch((ex) => toast(ex.message, "error"));
}

$("#attCourse").addEventListener("change", (e) =>
  changeControl(() => { att.courseId = Number(e.target.value); }, () => { e.target.value = att.courseId; }));
$("#attDate").addEventListener("change", (e) => {
  if (!e.target.value) { e.target.value = att.date; return; }
  changeControl(() => { att.date = e.target.value; }, () => { e.target.value = att.date; });
});

async function loadRoster() {
  const q = new URLSearchParams({ date: att.date, period: att.period });
  const data = await api(`/api/courses/${att.courseId}/roster?${q}`);
  att.voiceUndo = null;
  $("#voiceUndo").disabled = true;
  att.markedPeriods = data.marked_periods;
  att.alreadyMarked = data.already_marked;
  // Anyone not yet saved starts as present. Faculty only tap the exceptions.
  att.students = data.students.map((s) => ({ id: s.id, roll_no: s.roll_no, name: s.name, status: s.status || "present" }));
  renderPeriods();
  renderRoster();
}

function renderRoster() {
  const box = $("#roster");
  box.replaceChildren();
  if (!att.students.length) {
    box.append(empty("This class has no students yet", "Add them from the Import tab, then come back."));
    return;
  }

  const mk = (key, label) => {
    const n = h("span", { class: "tally-n" }, "0");
    tally[key] = n;
    return h("div", { class: `tally-item ${key}` }, n, h("span", { class: "tally-l" }, label));
  };
  tally.bar = { present: h("span", { class: "seg present" }), late: h("span", { class: "seg late" }), absent: h("span", { class: "seg absent" }) };
  const saveBtn = h("button", { class: "btn", type: "button", onclick: () => saveAttendance(saveBtn) }, att.alreadyMarked ? "Update attendance" : "Save attendance");
  const note = att.alreadyMarked
    ? h("span", { class: "note warn" }, "Already saved for this period. Saving will update it.")
    : h("span", { class: "note" }, "Everyone starts as present. Tap a student to change: absent, then late, then present.");

  box.append(
    h("div", { class: "tally-bar" }, h("div", { class: "tally-inner" },
      h("div", { class: "tally-head" },
        h("div", { class: "tally-counts" }, mk("present", "Present"), mk("absent", "Absent"), mk("late", "Late")),
        h("div", { class: "tally-actions" },
          h("button", { class: "btn ghost", type: "button", onclick: allPresent }, "Everyone present"),
          saveBtn)),
      h("div", { class: "bar", role: "img", "aria-label": "Share of present, late and absent students" },
        tally.bar.present, tally.bar.late, tally.bar.absent),
      note)),
    h("div", { class: "grid" }, att.students.map(studentCard)));
  updateTally();
}

function studentCard(s) {
  const label = h("span", { class: "card-status" });
  const b = h("button", { type: "button", class: "student" },
    h("span", { class: "roll" }, s.roll_no), h("span", { class: "sname" }, s.name), label);
  s.paint = () => {
    b.dataset.status = s.status;
    label.textContent = STATUS_LABEL[s.status];
    b.setAttribute("aria-label", `${s.roll_no}, ${s.name}, ${STATUS_LABEL[s.status]}. Press to change.`);
  };
  b.addEventListener("click", () => { s.status = NEXT[s.status]; att.dirty = true; s.paint(); updateTally(); });
  s.paint();
  return b;
}

function updateTally() {
  const c = { present: 0, absent: 0, late: 0 };
  att.students.forEach((s) => { c[s.status] += 1; });
  const total = att.students.length || 1;
  for (const k of ["present", "absent", "late"]) {
    tally[k].textContent = c[k];
    tally.bar[k].style.width = `${(c[k] * 100) / total}%`;
  }
}

function allPresent() {
  att.students.forEach((s) => { s.status = "present"; s.paint(); });
  att.dirty = true;
  updateTally();
}

async function saveAttendance(btn) {
  if (btn) btn.disabled = true;
  try {
    const r = await api("/api/attendance", {
      method: "POST",
      body: {
        course_id: att.courseId, date: att.date, period: att.period,
        records: att.students.map((s) => ({ student_id: s.id, status: s.status })),
      },
    });
    att.dirty = false;
    toast(`Attendance saved for period ${r.period}: ${r.absent} absent, ${r.late} late.`);
    await loadRoster();
  } catch (ex) {
    toast(ex.message, "error");
    if (btn) btn.disabled = false;
  }
}


/* ---------- Voice ----------
   Speech (or typed text) goes to /api/voice/parse, which only *understands* the sentence.
   Here we apply it to the cards on screen. Nothing is saved until the faculty says "save"
   in a separate command or presses the Save button. */

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;
let listening = false;

function setListening(on) {
  listening = on;
  const b = $("#micBtn");
  b.classList.toggle("listening", on);
  b.setAttribute("aria-pressed", String(on));
  $("#micLabel").textContent = on ? "Listening" : "Speak";
}

function startListening() {
  if (!SR) {
    toast("This browser can't hear you. Type the command instead, or open Roll Call in Chrome or Edge.", "error");
    $("#voiceText").focus();
    return;
  }
  if (listening) { recognizer.stop(); return; }
  recognizer = new SR();
  recognizer.lang = "en-IN";
  recognizer.interimResults = true;
  recognizer.continuous = false;
  recognizer.onstart = () => setListening(true);
  recognizer.onend = () => setListening(false);
  recognizer.onerror = (ev) => {
    const msg = ev.error === "not-allowed" || ev.error === "service-not-allowed"
      ? "Microphone is blocked. Allow it from the lock icon in the address bar."
      : ev.error === "no-speech" ? "Didn't hear anything. Press Speak and try again."
      : `Voice input stopped (${ev.error}).`;
    toast(msg, "error");
  };
  recognizer.onresult = (ev) => {
    let text = "", final = false;
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      text += ev.results[i][0].transcript;
      if (ev.results[i].isFinal) final = true;
    }
    $("#voiceText").value = text;
    $("#voiceHeard").textContent = `Heard: ${text}`;
    if (final) runVoice(text);
  };
  recognizer.start();
}

$("#micBtn").addEventListener("click", () => {
  if (state.view !== "attendance") go("attendance");
  startListening();
});
$("#voiceRun").addEventListener("click", () => runVoice($("#voiceText").value));
$("#voiceText").addEventListener("keydown", (e) => { if (e.key === "Enter") runVoice($("#voiceText").value); });
$("#voiceUndo").addEventListener("click", () => { const ok = undoVoice(); showVoice([ok ? "Undid the last voice change." : "Nothing to undo."]); });

async function runVoice(text) {
  text = (text || "").trim();
  if (!text) { toast("Say or type a command first.", "error"); return; }
  $("#voiceHeard").textContent = `Heard: ${text}`;
  try {
    await att.loading;
    if (!att.courseId) throw new Error("Choose a course first.");
    if (!att.students.length) throw new Error("This class has no students yet.");
    const r = await api("/api/voice/parse", { method: "POST", body: { transcript: text, course_id: att.courseId } });
    await applyVoice(r);
    $("#voiceText").value = "";
  } catch (ex) {
    toast(ex.message, "error");
  }
}

function showVoice(lines, asks = []) {
  const box = $("#voiceOut");
  box.replaceChildren(
    ...lines.map((l) => h("p", { class: "voice-line" + (l.bad ? " bad" : "") }, l.text || l)),
    ...asks);
}

function snapshotStatuses() {
  if (!att.voiceUndo) {
    att.voiceUndo = att.students.map((s) => [s.id, s.status]);
    $("#voiceUndo").disabled = false;
  }
}

function undoVoice() {
  if (!att.voiceUndo) return false;
  const was = new Map(att.voiceUndo);
  att.students.forEach((s) => { if (was.has(s.id)) { s.status = was.get(s.id); s.paint(); } });
  att.voiceUndo = null;
  $("#voiceUndo").disabled = true;
  updateTally();
  return true;
}

function setStatuses(ids, status) {
  snapshotStatuses();
  const want = new Set(ids);
  att.students.forEach((s) => { if (want.has(s.id)) { s.status = status; s.paint(); } });
  att.dirty = true;
  updateTally();
}

async function applyVoice(r) {
  const lines = [];
  let changed = false, saveRequested = false;
  // Undo covers one spoken command at a time
  if (r.actions.some((a) => a.type === "set_all" || a.type === "set_status")) { att.voiceUndo = null; $("#voiceUndo").disabled = true; }

  for (const a of r.actions) {
    if (a.type === "show_report") {
      $("#repOnly").checked = !!a.defaulters_only;
      go("reports");
      return;
    }
    if (a.type === "set_date" || a.type === "set_period") {
      if (a.type === "set_date" ? a.date === att.date : a.period === att.period) continue;
      if (att.dirty) { lines.push({ text: "You have unsaved changes. Say save or undo first, then change the period or date.", bad: true }); continue; }
      const prev = { date: att.date, period: att.period };
      if (a.type === "set_date") { att.date = a.date; $("#attDate").value = a.date; }
      else att.period = a.period;
      try { await loadRoster(); } catch (ex) { Object.assign(att, prev); $("#attDate").value = att.date; await loadRoster(); throw ex; }
      lines.push(a.type === "set_date" ? `Date set to ${a.date}.` : `Switched to period ${a.period}.`);
    } else if (a.type === "undo") {
      lines.push(undoVoice() ? "Undid the last voice change." : { text: "Nothing to undo.", bad: true });
    } else if (a.type === "set_all") {
      setStatuses(att.students.map((s) => s.id), a.status);
      changed = true;
      lines.push(`Everyone set to ${a.status}.`);
    } else if (a.type === "set_status") {
      setStatuses(a.students.map((s) => s.id), a.status);
      changed = true;
      lines.push(`Marked ${a.status}: ${a.students.map((s) => s.name).join(", ")}.`);
    } else if (a.type === "save") {
      saveRequested = true;
    }
  }

  if (saveRequested) {
    if (changed) lines.push({ text: "Check the cards, then say save on its own to confirm.", bad: true });
    else { await saveAttendance(null); lines.push("Saved."); }
  }

  r.unresolved.forEach((u) => lines.push({ text: `Couldn't find "${u}" in this class.`, bad: true }));

  const asks = r.ambiguous.map((q) => {
    const row = h("div", { class: "ask" }, h("span", {}, `Which "${q.heard}" is ${q.status}?`));
    q.options.forEach((o) => row.append(h("button", { type: "button", class: "chip", onclick: () => {
      setStatuses([o.id], q.status);
      row.remove();
      toast(`Marked ${q.status}: ${o.name}.`);
    } }, `${o.name} (${o.roll_no})`)));
    return row;
  });

  if (!lines.length && !asks.length) lines.push({ text: 'I didn\'t catch a command. Try "Priya absent" or "everyone present".', bad: true });
  showVoice(lines, asks);
}

/* ---------- Students ---------- */

let allStudents = [];

async function setupStudents() {
  const classes = await api("/api/classes");
  if (!classes.length) {
    $("#stuList").replaceChildren(empty("No classes yet", "Classes appear here once a course is assigned to you."));
    return;
  }
  fillSelect($("#stuClass"), classes, (c) => c.name);
  $("#stuSearch").value = "";
  await loadStudents();
}

async function loadStudents() {
  allStudents = await api(`/api/students?class_id=${$("#stuClass").value}`);
  renderStudents();
}

function renderStudents() {
  const q = $("#stuSearch").value.trim().toLowerCase();
  const rows = allStudents.filter((s) => !q || s.name.toLowerCase().includes(q) || s.roll_no.toLowerCase().includes(q));
  const box = $("#stuList");
  if (!rows.length) {
    box.replaceChildren(empty(q ? "No match" : "No students in this class", q ? "Try a different name or roll number." : "Add students from the Import tab."));
    return;
  }
  box.replaceChildren(
    h("p", { class: "note", style: "margin-bottom:10px" }, `${rows.length} ${rows.length === 1 ? "student" : "students"}`),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Roll number"), h("th", {}, "Name"), h("th", {}, "Class"))),
      h("tbody", {}, rows.map((s) => h("tr", {}, h("td", { class: "num" }, s.roll_no), h("td", {}, s.name), h("td", {}, s.class_name)))))));
}

$("#stuClass").addEventListener("change", () => loadStudents().catch((ex) => toast(ex.message, "error")));
$("#stuSearch").addEventListener("input", renderStudents);

/* ---------- Reports ---------- */

let report = null;

async function setupReports() {
  if (!state.courses.length) {
    $("#repList").replaceChildren(empty("No courses assigned yet", "Reports appear once a course is assigned to you."));
    return;
  }
  fillSelect($("#repCourse"), state.courses, (c) => `${c.code}, ${c.name} (${c.class_name})`);
  await loadReport();
}

async function loadReport() {
  const id = $("#repCourse").value;
  const th = Number($("#repThreshold").value);
  const threshold = Number.isFinite(th) ? th : 75;
  report = await api(`/api/courses/${id}/summary?threshold=${threshold}`);
  $("#repExport").href = `/api/courses/${id}/report.csv?threshold=${threshold}`;
  renderReport();
}

function renderReport() {
  const box = $("#repList");
  if (!report) return;
  if (!report.sessions) {
    box.replaceChildren(empty("No attendance saved yet", "Percentages show up after you save attendance for at least one period."));
    return;
  }
  const onlyLow = $("#repOnly").checked;
  const rows = report.students.filter((s) => !onlyLow || s.defaulter);
  const low = report.students.filter((s) => s.defaulter).length;
  box.replaceChildren(
    h("p", { class: "note", style: "margin-bottom:10px" },
      `${report.sessions} ${report.sessions === 1 ? "session" : "sessions"} recorded. ${low} ${low === 1 ? "student is" : "students are"} below ${report.threshold}%.`),
    rows.length
      ? h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {},
            h("th", {}, "Roll number"), h("th", {}, "Name"), h("th", { class: "num" }, "Attended"),
            h("th", {}, "Attendance"), h("th", {}, "Status"))),
          h("tbody", {}, rows.map((s) =>
            h("tr", { class: s.defaulter ? "is-low" : "" },
              h("td", { class: "num" }, s.roll_no), h("td", {}, s.name),
              h("td", { class: "num" }, `${s.attended} of ${s.sessions}`),
              h("td", {}, h("div", { class: "pct" },
                h("span", { class: "pct-track" }, h("span", { class: "pct-fill" + (s.defaulter ? " low" : ""), style: `width:${s.percent}%` })),
                h("b", {}, `${s.percent}%`))),
              h("td", {}, h("span", { class: "flag" + (s.defaulter ? "" : " ok") }, s.defaulter ? "Below minimum" : "On track")))))))
      : empty("Nobody is below the minimum", "Everyone in this course is at or above the attendance you set."));
}

$("#repCourse").addEventListener("change", () => loadReport().catch((ex) => toast(ex.message, "error")));
$("#repThreshold").addEventListener("change", () => loadReport().catch((ex) => toast(ex.message, "error")));
$("#repOnly").addEventListener("change", renderReport);

/* ---------- Import ---------- */

$("#csvUpload").addEventListener("click", async () => {
  const file = $("#csvFile").files[0];
  const out = $("#importResult");
  if (!file) { toast("Choose a CSV file first.", "error"); return; }
  const form = new FormData();
  form.append("file", file);
  try {
    const r = await api("/api/students/upload", { method: "POST", form });
    out.replaceChildren(h("div", { class: "result" + (r.skipped.length ? " problem" : "") },
      `${r.added} ${r.added === 1 ? "student" : "students"} added. ${r.skipped.length} skipped.`,
      r.skipped.length ? h("ul", { class: "skipped" }, r.skipped.map((s) => h("li", {}, s))) : null));
    state.courses = await api("/api/courses");
    $("#csvFile").value = "";
  } catch (ex) {
    out.replaceChildren(h("div", { class: "result problem" }, ex.message));
  }
});

$("#csvSample").addEventListener("click", () => {
  const cls = state.courses[0] ? state.courses[0].class_name : "CSE-A";
  const blob = new Blob([`roll_no,name,class\n21CSE101,Sample Student,${cls}\n`], { type: "text/csv" });
  const a = h("a", { href: URL.createObjectURL(blob), download: "students_sample.csv" });
  a.click();
  URL.revokeObjectURL(a.href);
});

/* ---------- start ---------- */

(async function init() {
  try {
    showApp(await api("/api/me"));
  } catch (_) {
    showLogin();
  }
})();

window.addEventListener("beforeunload", (e) => {
  if (att.dirty) { e.preventDefault(); e.returnValue = ""; }
});