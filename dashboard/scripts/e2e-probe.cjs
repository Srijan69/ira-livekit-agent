// End-to-end probe for the real app. Loaded by electron/main.ts only when
// IRA_E2E_PROBE points here, and never in a packaged build:
//
//   npm run build
//   IRA_E2E_PROBE=<absolute path to this file> npx electron .
//
// Logs every supervisor transition and what the window shows, types one
// harmless prompt into the real command bar, captures the window, then quits.
// Both Python children die with it -- that is part of what this checks.
//
// The page is read by visible text rather than by class selectors: Tailwind's
// class names (`text-[12px]`) need escaping at three levels to survive into a
// querySelector, which is how the first version of this probe read nothing.
const { writeFileSync, mkdirSync } = require("node:fs");
const { join } = require("node:path");

const OUT = join(__dirname, "..", "shots");
const DURATION_MS = Number(process.env.IRA_E2E_SECONDS || 60) * 1000;
const PROMPT_AT_MS = Number(process.env.IRA_E2E_PROMPT_AT || 30) * 1000;

// Harmless on purpose: no tool, no desktop action, nothing opened.
const PROMPT = "Reply with just the word ready, nothing else.";

const READ_VIEW = `(function(){
  function texts(selector){
    return Array.prototype.map.call(document.querySelectorAll(selector), function(el){
      return (el.textContent || '').trim();
    });
  }
  var badge = texts('header span').filter(function(t){
    return t === 'Online' || t === 'Connecting' || t === 'Offline';
  })[0] || null;
  var orb = texts('p.label-caps').filter(function(t){
    return t.slice(-3) === '...' || t === 'OFFLINE';
  })[0] || null;
  var cues = Array.prototype.filter.call(document.querySelectorAll('span.label-caps'), function(el){
    return getComputedStyle(el).color === 'rgb(34, 211, 238)';
  }).map(function(el){ return (el.textContent || '').split('+').join('').trim(); });
  var banner = document.querySelector('[role=status] p');
  var mic = document.querySelector('header button[aria-pressed]');
  return JSON.stringify({
    badge: badge,
    orb: orb,
    cues: cues,
    mic: mic ? mic.getAttribute('aria-pressed') : null,
    banner: banner ? banner.textContent : null,
    voiceElement: Boolean(document.querySelector('audio[data-ira-voice]'))
  });
})()`;

// React owns the input's value, so it is set through the native setter and
// announced with an input event -- assigning .value directly is invisible to
// React's onChange and the Send button would stay disabled.
const TYPE_PROMPT = `(function(){
  var input = document.querySelector('input[aria-label="Ask IRA anything"]');
  if (!input) return 'no input';
  if (input.disabled) return 'input disabled';
  var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(input, ${JSON.stringify(PROMPT)});
  input.dispatchEvent(new Event('input', { bubbles: true }));
  return 'typed';
})()`;

const PRESS_SEND = `(function(){
  var send = document.querySelector('button[aria-label="Send"]');
  if (!send) return 'no send button';
  if (send.disabled) return 'send disabled';
  send.click();
  var input = document.querySelector('input[aria-label="Ask IRA anything"]');
  return 'sent; input now ' + JSON.stringify(input ? input.value : null);
})()`;

module.exports = ({ app, window, agent, bridge }) => {
  mkdirSync(OUT, { recursive: true });
  const t0 = Date.now();
  const stamp = () => `+${((Date.now() - t0) / 1000).toFixed(1)}s`;
  const log = (...parts) => console.log("[e2e]", stamp(), ...parts);
  const run = (code) => window.webContents.executeJavaScript(code);

  for (const proc of [agent, bridge].filter(Boolean)) {
    let last = "";
    setInterval(() => {
      const status = proc.status();
      const key = `${status.state}|${status.reason ?? ""}`;
      if (key !== last) {
        last = key;
        log(`${status.name}: ${status.state}${status.reason ? ` (${status.reason})` : ""}`);
      }
    }, 200);
  }

  let lastView = "";
  setInterval(async () => {
    try {
      const view = await run(READ_VIEW);
      if (view !== lastView) {
        lastView = view;
        log("view:", view);
      }
    } catch (error) {
      log("view read failed:", error.message);
    }
  }, 400);

  window.webContents.on("console-message", (_event, level, message) => {
    if (level >= 2) log("renderer:", String(message).slice(0, 300));
  });

  let shot = 0;
  const capture = async (label) => {
    const image = await window.webContents.capturePage();
    const name = `e2e-${String(shot++).padStart(2, "0")}-${label}.png`;
    writeFileSync(join(OUT, name), image.toPNG());
    log("captured", name);
  };

  setTimeout(() => void capture("startup"), 6_000);
  setTimeout(() => void capture("connected"), 22_000);

  setTimeout(async () => {
    log("typing prompt:", JSON.stringify(PROMPT));
    log("type:", await run(TYPE_PROMPT));
    setTimeout(async () => {
      log("send:", await run(PRESS_SEND));
      setTimeout(() => void capture("after-prompt"), 2_500);
    }, 400);
  }, PROMPT_AT_MS);

  setTimeout(async () => {
    await capture("final");
    const logs = { agent: agent?.recentLogs() ?? [], bridge: bridge?.recentLogs() ?? [] };
    writeFileSync(join(OUT, "e2e-logs.json"), JSON.stringify(logs, null, 2));
    log("done; quitting");
    app.quit();
  }, DURATION_MS);
};
