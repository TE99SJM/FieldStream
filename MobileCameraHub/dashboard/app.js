const by = id => document.getElementById(id);
const node = (tag, text, cls) => {
  const el = document.createElement(tag);
  el.textContent = text;
  if (cls) el.className = cls;
  return el;
};
let key = sessionStorage.getItem("hub-key") || "";
let pending = false;
let timer;
let generation = 0;
let lastState;
let messageTimer;
async function api(path, method = "GET") {
  const res = await fetch("/api/" + path, {method, headers: {Authorization: "Bearer " + key}, cache: "no-store", signal: AbortSignal.timeout(25000)});
  const data = await res.json();
  if (res.status === 401) { logout(); throw Error(data.detail); }
  if (!res.ok) throw Error(data.detail || "Hub request failed.");
  return data;
}
function logout() {
  generation++;
  clearTimeout(timer);
  key = "";
  sessionStorage.removeItem("hub-key");
  by("desk").hidden = true;
  by("pair").hidden = false;
  by("air").textContent = "Standby";
  by("air").className = "pill";
}
function sceneButton(s) {
  const b = node("button", "", "scene");
  b.append(node("span", s.label), node("span", "", "arrow"));
  b.onclick = () => act("scenes/" + s.id);
  return b;
}
function cameraRow(c) {
  const el = node("div", "", "row");
  const left = node("div", "");
  left.append(node("div", c.label, "camname"), node("div", "", "meta"));
  const right = node("div", "", "right");
  right.append(node("span", "", "rate"));
  if (c.transport === "rtmp") {
    const reset = node("button", "Reset", "smallbutton");
    reset.setAttribute("aria-label", "Reset " + c.label);
    reset.onclick = () => { if (confirm("Disconnect the publisher for " + c.label + "?")) act("reset/" + c.id); };
    right.append(reset);
  }
  el.append(left, right);
  return el;
}
function render(state) {
  lastState = state;
  const o = state.obs, cal = state.calibration;
  const busy = pending || ["recording", "analyzing"].includes(cal.phase);
  by("air").textContent = o.on_air === null ? "OBS offline" : o.on_air ? "On air" : "Standby";
  by("air").className = o.on_air ? "pill live" : "pill";
  by("program").textContent = state.scenes.find(s => s.name === o.scene)?.label || o.scene || "—";
  by("output").textContent = o.fps == null ? "Offline" : o.fps + " fps";
  by("receiver").textContent = state.receiver ? "Listening" : "Offline";
  let issue = !o.connected ? (o.detail || "Connecting to OBS…") : !o.collection_ok ? "Select the configured scene collection in OBS." : "";
  if (!state.receiver) issue += " " + (state.receiver_detail || "Receiver offline.");
  by("issue").textContent = issue; by("issue").hidden = !issue;
  state.scenes.forEach(s => {
    let b = by("scene-" + s.id);
    if (!b) { b = sceneButton(s); b.id = "scene-" + s.id; by("scenes").append(b); }
    b.setAttribute("aria-pressed", String(s.name === o.scene));
    b.disabled = busy || !o.collection_ok || !o.existing?.includes(s.name);
  });
  state.cameras.forEach(c => {
    let row = by("cam-" + c.id);
    if (!row) { row = cameraRow(c); row.id = "cam-" + c.id; by("inputs").append(row); }
    const status = c.state === "offline" && c.publisher ? "Publisher connected · waiting for video" : c.state;
    const meta = row.querySelector(".meta");
    meta.textContent = (c.transport === "usb" ? "USB" : "Local RTMP") + " · " + status;
    if (c.detail && c.state !== "receiving") meta.textContent += " · " + c.detail;
    meta.className = "meta " + (c.state === "receiving" ? "good" : "warn");
    row.querySelector(".rate").textContent = (c.fps ?? "—") + " fps";
    const reset = row.querySelector("button");
    if (reset) reset.disabled = busy || !state.receiver;
  });
  by("build").disabled = busy || !o.collection_ok || o.on_air;
  by("cal").textContent = cal.message || cal.last?.message || "Aim every camera at the same slate to measure video and microphone timing.";
  const last = cal.last;
  by("delays").replaceChildren();
  if (last) {
    by("delays").append(node("div", "Last measurement · maximum spread " + last.observed_skew_ms + " ms · one frame " + last.frame_ms + " ms", "setup-line"));
    if (last.mode === "verify" && last.median_early_ms) {
      const leads = [...state.cameras.map(c => c.label + " " + last.median_early_ms[c.id] + " ms"), "Master mic " + last.median_early_ms["@audio"] + " ms"];
      by("delays").append(node("div", "Median lead over the latest input · " + leads.join("  /  "), "setup-line"));
    }
    if (last.applied) {
      const offsets = [...state.cameras.map(c => c.label + " " + last.offsets_ms[c.id] + " ms"), "Master mic " + last.offsets_ms["@audio"] + " ms"];
      by("delays").append(node("div", offsets.join("  /  "), "setup-line"));
    }
  }
}
async function act(path) {
  if (pending) return;
  pending = true;
  if (lastState) render(lastState);
  clearTimeout(messageTimer);
  try {
    by("message").textContent = (await api(path, "POST")).message;
    messageTimer = setTimeout(() => { by("message").textContent = ""; }, 3500);
  }
  catch (e) { by("message").textContent = e.message; }
  finally { pending = false; if (key) refresh(); }
}
async function refresh() {
  const version = ++generation;
  clearTimeout(timer);
  try {
    const state = await api("status");
    if (version !== generation) return;
    sessionStorage.setItem("hub-key", key);
    by("pair").hidden = true; by("desk").hidden = false;
    render(state);
    by("preview").hidden = true;
    if (by("showPreview").checked && state.obs.connected) {
      const result = await api("preview");
      if (version !== generation) return;
      by("preview").src = result.image; by("preview").hidden = false;
    }
  } catch (e) {
    if (version === generation || !key) {
      by("loginError").textContent = e.message;
      by("issue").textContent = "Dashboard connection lost · " + e.message;
      by("issue").hidden = false;
      by("desk").querySelectorAll("button:not(#disconnect)").forEach(b => b.disabled = true);
      by("air").textContent = "Status unknown";
      by("preview").hidden = true;
    }
  } finally { if (key && version === generation) timer = setTimeout(refresh, 1500); }
}
by("login").onsubmit = e => { e.preventDefault(); key = by("key").value.trim(); by("key").value = ""; refresh(); };
by("disconnect").onclick = logout;
by("build").onclick = () => act("layout");
by("showPreview").onchange = refresh;
if (key) refresh();
