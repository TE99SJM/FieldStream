"use strict";
const el = id => document.getElementById(id);
const message = value => { el("message").textContent = value; };
const names = {"Field - Grid":"Five-camera grid", "Field - Sony + PiP":"Sony + DJI inset", "Field - Sony Full":"Sony ZV-1F", "Field - DJI Full":"DJI Osmo 360"};
const title = scene => names[scene] || scene.replace(/^Field - /, "").replace(/ Full$/, "");
let key = location.hash.slice(1) || sessionStorage.getItem("fieldPair") || "";
if (location.hash) history.replaceState(null, "", location.pathname);
let busy = false;
let buttons = new Map();
let shownScene = "";
let imageUrl = "";

function clearPreview() {
  if (imageUrl) URL.revokeObjectURL(imageUrl);
  imageUrl = "";
  el("preview").hidden = true;
  el("preview").removeAttribute("src");
}
function paired(ok) {
  el("controls").hidden = !ok;
  el("pair").hidden = ok;
  el("broadcast").hidden = !ok;
  if (!ok) clearPreview();
}
async function api(path, options={}) {
  const headers = {"Authorization":"Bearer " + key, ...options.headers};
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 9000);
  let resp;
  try {
    resp = await fetch(path, {...options, headers, signal:controller.signal, cache:"no-store"});
  } finally {clearTimeout(timer);}
  if (resp.status === 401) {
    key = "";
    sessionStorage.removeItem("fieldPair");
    paired(false);
  }
  if (!resp.ok) {
    const info = await resp.json();
    throw new Error(info.error || "Hub request failed.");
  }
  return resp;
}
function prepareButtons(scenes) {
  if (buttons.size) return;
  for (const name of scenes) {
    const full = name.endsWith(" Full");
    const button = document.createElement("button");
    button.type = "button"; button.className = "card";
    const icon = document.createElement("span");
    icon.className = "symbol " + (full ? "camera" : name.endsWith("Grid") ? "grid" : "pip");
    icon.setAttribute("aria-hidden", "true");
    const text = document.createElement("span"); text.textContent = title(name);
    button.append(icon, text); button.addEventListener("click", () => switchScene(name));
    el(full ? "singles" : "mixes").append(button);
    buttons.set(name, button);
  }
}
function render(data) {
  prepareButtons(data.scenes);
  if (shownScene !== data.scene) clearPreview();
  shownScene = data.scene;
  el("current").textContent = title(data.scene);
  el("broadcast").textContent = data.streaming ? "On air" : "Off air";
  el("broadcast").classList.toggle("on", data.streaming);
  for (const [scene, button] of buttons) {
    button.setAttribute("aria-pressed", String(scene === data.scene));
    button.disabled = false;
  }
  sessionStorage.setItem("fieldPair", key);
  paired(true); message("");
}
function unavailable(error) {
  if (key) {
    el("broadcast").textContent = "Disconnected";
    el("broadcast").classList.remove("on");
    el("current").textContent = "Waiting for hub";
    for (const button of buttons.values()) {
      button.setAttribute("aria-pressed", "false");
      button.disabled = true;
    }
    clearPreview();
  }
  message(error.name === "AbortError" ? "Hub request timed out." : error.message);
}
async function refresh() {
  if (busy || !key || document.hidden) return;
  busy = true;
  try {render(await (await api("/state")).json());}
  catch(error) {unavailable(error);}
  finally {busy = false;}
}
let switching = false;
async function switchScene(name) {
  if (switching) return;
  switching = true;
  // A periodic status response must not land after the switch and display stale state.
  while (busy) await new Promise(resolve => setTimeout(resolve, 40));
  busy = true;
  for (const button of buttons.values()) button.disabled = true;
  try {
    render(await (await api("/scene", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({scene:name})})).json());
  } catch(error) {unavailable(error);}
  finally {busy = false; switching = false;}
}
el("picture").addEventListener("click", async () => {
  el("picture").disabled = true;
  const scene = shownScene;
  try {
    const blob = await (await api("/snapshot")).blob();
    if (scene === shownScene) {
      clearPreview(); imageUrl = URL.createObjectURL(blob);
      el("preview").src = imageUrl; el("preview").hidden = false;
      message("Snapshot captured " + new Date().toLocaleTimeString());
    }
  } catch(error) {message(error.message);}
  finally {el("picture").disabled = false;}
});
document.querySelector("form").addEventListener("submit", event => {
  event.preventDefault();
  key = el("key").value.trim(); el("key").value = ""; refresh();
});
paired(Boolean(key)); refresh(); setInterval(refresh, 2500);
document.addEventListener("visibilitychange", refresh);
