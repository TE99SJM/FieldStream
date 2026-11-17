const key = sessionStorage.getItem("hub-key");
const result = document.getElementById("result");
const buttons = [...document.querySelectorAll("button")];
const pause = ms => new Promise(r => setTimeout(r, ms));
async function request(path, method) {
  const r = await fetch("/api/" + path, {method, headers: {Authorization: "Bearer " + key}, cache: "no-store", signal: AbortSignal.timeout(20000)});
  const j = await r.json(); if (!r.ok) throw Error(j.detail); return j;
}
async function play(mode) {
  buttons.forEach(b => b.disabled = true);
  let audio, raf = 0;
  try {
    audio = new (window.AudioContext || window.webkitAudioContext)();
    await audio.resume();
    result.textContent = "Checking the hub and starting the OBS recording…";
    await request("calibration/" + mode, "POST");
    document.body.classList.add("play");
    const start = audio.currentTime;
    const starts = [2.5, 7.5, 14];
    for (const t of starts) {
      const osc = audio.createOscillator(), gain = audio.createGain();
      osc.frequency.value = 1000;
      gain.gain.value = 0.55;
      osc.connect(gain).connect(audio.destination);
      osc.start(start + t); osc.stop(start + t + 0.48);
    }
    function paint() {
      const t = audio.currentTime - start;
      document.body.classList.toggle("white", starts.some(s => t >= s && t < s + 0.48));
      if (t < 15) raf = requestAnimationFrame(paint);
    }
    paint();
    await pause(18500);
    document.body.classList.remove("play", "white");
    result.textContent = "Analyzing the recorded grid and microphone…";
    for (let i = 0; i < 120; i++) {
      const c = (await request("status", "GET")).calibration;
      if (!["recording", "analyzing"].includes(c.phase)) { result.textContent = c.message || "Complete."; return; }
      await pause(1000);
    }
    result.textContent = "Check the switcher for the final result.";
  } catch (e) { result.textContent = e.message; }
  finally {
    cancelAnimationFrame(raf); document.body.classList.remove("play", "white");
    if (audio) await audio.close();
    buttons.forEach(b => b.disabled = false);
  }
}
if (!key) { buttons.forEach(b => b.disabled = true); result.textContent = "Open Switcher and enter the hub control key first."; }
document.getElementById("apply").onclick = () => play("apply");
document.getElementById("verify").onclick = () => play("verify");
