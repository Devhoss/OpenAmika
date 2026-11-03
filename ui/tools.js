/* OpenAmika — Tools page: her agents & MCP servers, consent settings, jobs, approvals, call log. */
(function () {
  "use strict";
  const { nav, footer, toast, getJSON, postAction, el } = window.OA;
  nav("tools"); footer();
  const $ = (id) => document.getElementById(id);

  let state = null;        // last GET /api/build_f/tools payload (jobs and approvals are shown on the Use page)

  function configFromState(s) {
    // the brain hands over the raw tools.json (env / cwd / headers included) — a save must never strip
    // what the cards do not show. The reconstruction below is only for a brain that predates "config".
    if (s.config && typeof s.config === "object") {
      const doc = JSON.parse(JSON.stringify(s.config));
      doc.mcpServers = doc.mcpServers && typeof doc.mcpServers === "object" ? doc.mcpServers : {};
      return doc;
    }
    const servers = {};
    for (const [name, server] of Object.entries(s.servers || {})) {
      const entry = { enabled: !!server.enabled, tools: { ...(server.policies || {}) } };
      if (server.transport === "http") {
        entry.url = server.target;
      } else {
        const parts = (server.target || "").trim().split(/\s+/);
        entry.command = parts[0] || "";
        entry.args = parts.slice(1);
      }
      if (server.kind === "agent") entry.kind = "agent";
      if (server.persona_note) entry.persona_note = server.persona_note;
      servers[name] = entry;
    }
    return { enabled: !!s.enabled, clerk_role: s.clerk_role || "surface", limits: { ...(s.limits || {}) }, mcpServers: servers };
  }

  function splitCommand(text) {
    const out = [];
    const re = /"([^"]*)"|(\S+)/g;
    let m;
    while ((m = re.exec(text || ""))) out.push(m[1] !== undefined ? m[1] : m[2]);
    return out;
  }
  function joinCommand(auto) {
    if (!auto || !auto.command) return "";
    const q = (s) => (/\s/.test(s) ? '"' + s + '"' : s);
    return [auto.command, ...(auto.args || [])].map(q).join(" ");
  }

  function mergedConfig() {
    // the UI-visible knobs win; everything else (env/headers/cwd) comes from the raw editor
    let raw;
    try { raw = JSON.parse($("rawConfig").value); } catch (_) { raw = null; }
    const base = raw && typeof raw === "object" ? raw : configFromState(state || {});
    base.enabled = $("toolsEnabled").checked;
    base.clerk_role = $("clerkRole").value;
    base.limits = {
      max_calls_per_turn: Number($("limCalls").value) || 3,
      call_timeout_s: Number($("limTimeout").value) || 20,
      poll_interval_s: Number($("limPoll").value) || 2.5,
    };
    base.mcpServers = base.mcpServers || {};
    document.querySelectorAll("[data-server-toggle]").forEach((input) => {
      const name = input.getAttribute("data-server-toggle");
      if (base.mcpServers[name]) base.mcpServers[name].enabled = input.checked;
    });
    document.querySelectorAll("select[data-policy-server]").forEach((select) => {
      const name = select.getAttribute("data-policy-server");
      const tool = select.getAttribute("data-policy-tool");
      if (base.mcpServers[name]) {
        base.mcpServers[name].tools = base.mcpServers[name].tools || {};
        base.mcpServers[name].tools[tool] = select.value;
      }
    });
    document.querySelectorAll("input[data-autostart]").forEach((input) => {
      const name = input.getAttribute("data-autostart");
      const entry = base.mcpServers[name];
      if (!entry) return;
      const parts = splitCommand(input.value.trim());
      if (!parts.length) { delete entry.autostart; return; }
      const prev = entry.autostart && typeof entry.autostart === "object" ? entry.autostart : {};
      entry.autostart = { ...prev, command: parts[0], args: parts.slice(1) };   // cwd/env from the raw editor survive
    });
    document.querySelectorAll("textarea[data-note]").forEach((area) => {
      const name = area.getAttribute("data-note");
      if (base.mcpServers[name]) {
        const note = area.value.trim();
        if (note) base.mcpServers[name].persona_note = note; else delete base.mcpServers[name].persona_note;
      }
    });
    return base;
  }

  async function save(cfg) {
    const result = await postAction("/api/build_f/tools/config", "tools-config", { config: cfg });
    apply(result);
    toast("Tool settings saved.");
  }

  function apply(s) {
    state = s;
    $("toolsEnabled").checked = !!s.enabled;
    $("clerkRole").value = s.clerk_role || "surface";
    const limits = s.limits || {};
    $("limCalls").value = limits.max_calls_per_turn || 3;
    $("limTimeout").value = limits.call_timeout_s || 20;
    $("limPoll").value = limits.poll_interval_s || 2.5;
    if (document.activeElement !== $("rawConfig")) {
      $("rawConfig").value = JSON.stringify(configFromState(s), null, 2);
    }
    renderServers(s);
    renderLog(s);
  }

  function renderServers(s) {
    const box = $("serversBox");
    box.replaceChildren();
    const entries = Object.entries(s.servers || {});
    if (!entries.length) {
      box.append(el("section", { class: "tech-card" },
        el("div", { class: "card-hint", text: "No servers yet — add one below. Hermes Agent and nanoMuse plug in through servers/openamika-agents; the smart-home server lives in servers/openamika-iot." })));
      return;
    }
    for (const [name, server] of entries) {
      const card = el("section", { class: "tech-card server-card" });
      const toggle = el("input", { type: "checkbox", "data-server-toggle": name });
      toggle.checked = !!server.enabled;
      toggle.addEventListener("change", () => save(mergedConfig()).catch((e) => toast(String(e.message || e), true)));
      // dot: grey = disabled · amber = enabled but the bridge is not up · red = bridge up, the agent itself unreachable · green = all good
      const health = server.health || null;
      const agentDown = !!(server.connected && health && health.ok === false);
      const starting = agentDown && health.reason === "starting";
      const startFailed = agentDown && health.reason === "start_failed";
      const refusing = agentDown && health.reason === "refused";      // up and authenticated, but it would not take her last task
      const canStart = server.kind === "agent" && server.enabled && server.autostart && agentDown && !starting;
      const head = el("div", { class: "server-head" },
        el("span", { class: "dot " + (!server.enabled ? "" : !server.connected ? "warn" : starting ? "warn" : agentDown ? "err" : "ok") }),
        el("span", { class: "name", text: name }),
        el("span", { class: "tag", text: server.kind === "agent" ? "agent" : server.transport }),
        health ? el("span", { class: "tag", style: starting ? "color:var(--warn);" : agentDown ? "color:var(--err);" : "",
          text: starting ? "agent starting…" : startFailed ? "start failed" : refusing ? "agent refusing tasks" : agentDown ? "agent unreachable"
            : "agent online" + (health.version ? " · v" + health.version : "") }) : "",
        canStart ? el("button", {
          class: "btn small primary", text: "Start",
          onclick: async (event) => {
            event.preventDefault();
            toast("Starting " + name + "…");
            try {
              const result = await postAction("/api/build_f/tools/start", "tools-start", { server: name });
              toast(result.started ? name + " launched — the card turns green when it answers." : name + ": " + ((result.health || {}).detail || "not started"), !result.started);
              refresh();
            } catch (error) { toast(name + ": " + (error.message || error), true); }
          },
        }) : "",
        el("span", { class: "target mono", text: server.target || "" }),
        el("label", { class: "switch" }, toggle, el("span", { class: "track" })),
        el("button", {
          class: "btn small", text: "Test",
          onclick: async (event) => {
            event.preventDefault();
            toast("Connecting " + name + "…");
            try {
              const result = await postAction("/api/build_f/tools/test", "tools-test", { server: name });
              const h = result.health;
              const agentNote = h ? (h.ok ? " · agent online" + (h.version ? " v" + h.version : "") : " · agent UNREACHABLE: " + (h.detail || "")) : "";
              toast(name + ": " + (result.tools || []).length + " tool(s) discovered" + (result.kind === "agent" ? " (agent bridge)" : "") + agentNote + ".", !!(h && !h.ok));
              refresh();
            } catch (error) { toast(name + ": " + (error.message || error), true); }
          },
        }),
        el("button", {
          class: "btn small danger", text: "Remove", "data-pro": "",   // deleting a server from tools.json: Professional only
          onclick: async (event) => {
            event.preventDefault();
            if (!window.confirm("Remove server '" + name + "' from tools.json?")) return;
            const cfg = mergedConfig();
            delete cfg.mcpServers[name];
            save(cfg).catch((e) => toast(String(e.message || e), true));
          },
        }));
      card.append(head);
      if (server.error) card.append(el("div", { class: "card-hint", style: "color:var(--err);margin-top:8px;", text: server.error }));
      if (starting) card.append(el("div", { class: "card-hint", style: "margin-top:8px;", text: (health.detail || "starting…") + " — this card updates by itself." }));
      else if (agentDown) card.append(el("div", { class: "card-hint", style: "color:var(--err);margin-top:8px;",
        text: (health.detail || "The bridge is up but the agent itself does not answer.")
          + (refusing ? " She is told it is not taking tasks and will not promise it anything." : " She is told it is unreachable and will not promise it anything.") }));
      if (health && health.ok && health.note) card.append(el("div", { class: "card-hint", style: "margin-top:8px;", text: health.note }));
      if (server.kind === "agent") {
        const startField = el("div", { class: "field", style: "margin-top:10px;", "data-pro": "" },   // Professional only
          el("label", { text: "Start command — run automatically when the agent is not running (leave empty to manage it yourself)" }));
        const startInput = el("input", { type: "text", class: "mono", "data-autostart": name, spellcheck: "false",
          placeholder: name === "nanomuse" ? "C:\\path\\to\\nanomuse\\venv\\Scripts\\nanomuse.exe serve --no-qr --host 127.0.0.1 --port 8787" : "C:\\Users\\you\\AppData\\Local\\hermes\\bin\\hermes.exe gateway" });
        startInput.value = joinCommand(server.autostart);
        startField.append(startInput);
        card.append(startField);
      }
      // what she is told about this server (in character, Chinese like the rest of her prompts)
      const noteField = el("div", { class: "field", style: "margin-top:10px;", "data-pro": "" },   // Professional only
        el("label", { text: "What she knows about it (goes into her prompt when enabled; {user_name} = your name)" }));
      const area = el("textarea", { class: "mono", rows: "3", "data-note": name, spellcheck: "false",
        placeholder: server.kind === "agent" ? "e.g. 赫尔墨斯——{user_name}装在他电脑上的小帮手程序，能替你上网查东西、看他的屏幕、读写邮件、网上下单……" : "e.g. 家里的灯和插座，可以开关、查状态" });
      area.value = server.persona_note || "";
      noteField.append(area);
      card.append(noteField);
      if (server.tools && server.tools.length) {
        const table = el("table", { class: "oa-table", style: "margin-top:10px;" });
        table.append(el("thead", {}, el("tr", {}, el("th", { text: "Tool" }), el("th", { text: "Description" }), el("th", { text: "Policy" }))));
        const body = el("tbody");
        for (const tool of server.tools) {
          const select = el("select", { class: "policy-select", "data-policy-server": name, "data-policy-tool": tool.name });
          for (const policy of ["auto", "confirm", "deny"]) select.append(el("option", { value: policy, text: policy }));
          select.value = tool.policy || "confirm";
          select.addEventListener("change", () => save(mergedConfig()).catch((e) => toast(String(e.message || e), true)));
          body.append(el("tr", {}, el("td", { text: tool.name }), el("td", { text: (tool.description || "").slice(0, 160) }), el("td", {}, select)));
        }
        table.append(body);
        card.append(el("div", { class: "table-scroll", "data-pro": "" }, table));   // per-tool policies: Professional only
      } else if (server.enabled) {
        card.append(el("div", { class: "card-hint", style: "margin-top:8px;", text: "No tools discovered yet — press Test to connect." }));
      }
      // the smart-home server lives inside the Smart Home and IoT panel, above its devices, without its own card chrome
      if (name === "iot") { card.classList.add("embedded"); $("iotServer").replaceChildren(card); }
      else box.append(card);
    }
  }

  function renderLog(s) {
    const body = $("logBody");
    const log = (s.log || []).slice().reverse();
    body.replaceChildren();
    if (!log.length) {
      body.append(el("tr", {}, el("td", { colspan: "5", class: "card-hint", text: "No tool calls yet." })));
      return;
    }
    for (const entry of log) {
      const when = new Date((entry.ts || 0) * 1000);
      const outcome = entry.ok === null ? "pending" : entry.ok ? "ok"
        : (entry.policy === "refused" ? "refused" : entry.policy === "unreachable" ? "not sent" : "error");
      const detail = (entry.result || "").slice(0, 120) + (entry.quote ? "  [you said: " + String(entry.quote).slice(0, 60) + "]" : "");
      body.append(el("tr", {},
        el("td", { text: when.toLocaleTimeString() }),
        el("td", { text: entry.server + "__" + entry.tool }),
        el("td", {}, el("code", { text: JSON.stringify(entry.arguments || {}).slice(0, 80) })),
        el("td", { text: outcome + (entry.policy === "confirmed" ? " (approved)" : "") }),
        el("td", { text: detail })));
    }
  }

  $("toolsSave").addEventListener("click", () => save(mergedConfig()).catch((e) => toast(String(e.message || e), true)));
  $("rawSave").addEventListener("click", () => {
    let raw;
    try { raw = JSON.parse($("rawConfig").value); }
    catch (error) { toast("Raw config is not valid JSON: " + error.message, true); return; }
    save(raw).catch((e) => toast(String(e.message || e), true));
  });

  $("addTransport").addEventListener("change", () => {
    const http = $("addTransport").value === "http";
    $("addTargetLabel").textContent = http ? "Server URL" : "Command + args (first word = command)";
    $("addTarget").placeholder = http ? "http://homeassistant.local:8123/mcp_server/sse" : "python -m openamika_agents hermes";
  });
  $("addBtn").addEventListener("click", () => {
    const name = $("addName").value.trim();
    const target = $("addTarget").value.trim();
    if (!/^[A-Za-z0-9_-]{1,32}$/.test(name)) { toast("Name: letters, digits, - or _ only.", true); return; }
    if (!target) { toast("Target is required.", true); return; }
    const cfg = mergedConfig();
    if (cfg.mcpServers[name]) { toast("A server with that name exists.", true); return; }
    if ($("addTransport").value === "http") {
      cfg.mcpServers[name] = { url: target, enabled: false };
    } else {
      const parts = target.split(/\s+/);
      cfg.mcpServers[name] = { command: parts[0], args: parts.slice(1), enabled: false };
      if (/openamika_agents/.test(target)) { cfg.mcpServers[name].kind = "agent"; cfg.mcpServers[name].cwd = "../servers/openamika-agents"; }
    }
    save(cfg).then(() => { $("addName").value = ""; $("addTarget").value = ""; })
      .catch((e) => toast(String(e.message || e), true));
  });

  // ── smart home: the plugs by hand (brain /api/build_f/iot — she reaches the same server through MCP) ──
  const iot = { timer: null, cards: new Map(), permit: false };
  const iotNum = (v, digits) => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : null);
  function iotAgo(epoch) {
    const s = Math.max(0, Date.now() / 1000 - (Number(epoch) || 0));
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " d ago";
  }
  const iotClock = (epoch) => new Date((Number(epoch) || 0) * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  async function iotPost(url, action, body) {
    try { return await postAction(url, action, body); }
    catch (error) { toast(String(error.message || error), true); return null; }   // 409 while she speaks: the detail says so, just retry
  }
  function iotLight(d) {                 // [dot class, text to show]
    if (!d || !d.enabled) return ["", "the smart-home server is off — flip its switch above (Professional mode adds one if it is missing)"];
    if (!d.connected) return ["err", (d.health && d.health.detail) || "not connected"];
    if (d.health && d.health.ok === false) return ["warn", d.health.detail || d.health.reason || "degraded"];
    return ["ok", ""];
  }
  // one card per device, built once and updated in place so a nickname being typed survives the refresh
  function iotCard(dev) {
    const key = dev.friendly_name;
    const card = el("div", { class: "iot-dev" });
    const name = el("b"), sub = el("div", { class: "card-hint" }), state = el("span", { class: "iot-state" });
    const readings = el("div", { class: "iot-readings mono" }), seen = el("div", { class: "card-hint" });
    const flags = el("div", { class: "card-hint mono", "data-pro": "" });
    const actions = el("div", { class: "iot-actions" });
    const setState = (st) => {
      const s = String(st || "unknown").toUpperCase();
      state.textContent = s;
      state.className = "iot-state " + (s === "ON" ? "on" : s === "OFF" ? "off" : "unknown");
    };
    const switchTo = async (action) => {
      actions.querySelectorAll("button").forEach((b) => { b.disabled = true; });
      setState(action === "toggle" ? (state.textContent === "ON" ? "OFF" : "ON") : action);   // optimistic; the refresh tells the truth
      const result = await iotPost("/api/build_f/iot/device", "iot-device", { device: key, action });
      const r = result && result.result;
      if (r && r.ok === false) toast(r.error || r.message || "The plug did not answer.", true);
      actions.querySelectorAll("button").forEach((b) => { b.disabled = false; });
      iotRefresh();
    };
    for (const [label, action, cls] of [["On", "on", "btn small primary"], ["Off", "off", "btn small"], ["Toggle", "toggle", "btn small"]]) {
      actions.append(el("button", { class: cls, type: "button", text: label, onclick: () => switchTo(action) }));
    }
    const nick = el("input", { type: "text", placeholder: "Nickname — what you call it", maxlength: "40" });
    const note = el("input", { type: "text", placeholder: "What is it? (Amika reads this)", maxlength: "200" });
    for (const input of [nick, note]) input.addEventListener("input", () => { card.dataset.dirty = "yes"; });
    const save = el("button", { class: "btn small", type: "button", text: "Save", onclick: async () => {
      const was = card._dev || {};
      let ok = true;
      if (nick.value.trim() !== (was.nickname || "")) ok = !!(await iotPost("/api/build_f/iot/device", "iot-device", { device: key, nickname: nick.value.trim() })) && ok;
      if (note.value.trim() !== (was.note || "")) ok = !!(await iotPost("/api/build_f/iot/device", "iot-device", { device: key, note: note.value.trim() })) && ok;
      if (ok) { card.dataset.dirty = "no"; toast("Saved — she knows it as “" + (nick.value.trim() || key) + "”."); }
      iotRefresh();
    } });
    card.update = (d) => {
      card._dev = d;
      name.textContent = d.nickname || d.friendly_name;
      sub.textContent = [d.vendor, d.model].filter(Boolean).join(" ") + (d.description ? " — " + d.description : "");
      setState(d.state);
      readings.textContent = [[d.power, 1, " W"], [d.voltage, 0, " V"], [d.current, 2, " A"], [d.energy, 2, " kWh"], [d.temperature, 0, " °C"]]
        .map(([v, digits, unit]) => { const t = iotNum(v, digits); return t === null ? null : t + unit; }).filter(Boolean).join(" · ");
      readings.hidden = !readings.textContent;
      // availability is only known when zigbee2mqtt's availability feature is on; "unknown" would just look broken
      const avail = d.availability === "online" || d.availability === "offline" ? d.availability : null;
      seen.textContent = [avail, d.last_seen ? "seen " + iotAgo(d.last_seen) : null].filter(Boolean).join(" · ");
      flags.textContent = [d.ieee, d.supported === false ? "unsupported" : d.supported ? "supported" : null,
        d.interview_completed === false ? "interview pending" : null].filter(Boolean).join(" · ");
      card.classList.toggle("offline", d.availability === "offline");
      if (card.dataset.dirty !== "yes" && document.activeElement !== nick && document.activeElement !== note) {
        nick.value = d.nickname || "";
        note.value = d.note || "";
      }
    };
    card.append(
      el("div", { class: "iot-head" }, el("div", { class: "iot-name" }, name, sub), state),
      actions, readings, seen,
      el("div", { class: "iot-edit" }, nick, el("div", { class: "row" }, note, save)),
      flags);
    card.update(dev);
    return card;
  }
  function renderIot(d) {
    const [level, detail] = iotLight(d);
    $("iotDot").className = "dot " + level;
    const devices = d && d.enabled && Array.isArray(d.devices) ? d.devices : [];
    const on = devices.filter((x) => String(x.state).toUpperCase() === "ON").length;
    $("iotSub").textContent = !d || !d.enabled ? "no smart-home server enabled" : !d.connected ? "not connected"
      : devices.length + (devices.length === 1 ? " device" : " devices") + (devices.length ? " · " + on + " on" : "");
    const hint = $("iotHint");
    hint.style.color = level === "err" ? "var(--err)" : level === "warn" ? "var(--warn)" : "";
    hint.textContent = !d || !d.enabled ? detail : detail || (!devices.length ? "No devices yet — put a plug into pairing mode and press Find new devices." : "");
    hint.hidden = !hint.textContent;
    const box = $("iotDevices"), present = new Set();
    for (const dev of devices) {
      const key = dev.friendly_name || dev.ieee;
      if (!key) continue;
      present.add(key);
      let card = iot.cards.get(key);
      if (!card) { card = iotCard({ ...dev, friendly_name: key }); iot.cards.set(key, card); box.append(card); }
      else card.update(dev);
    }
    for (const [key, card] of iot.cards) if (!present.has(key)) { card.remove(); iot.cards.delete(key); }
    // pairing: Find opens permit-join for ~4 minutes; the note counts down from permit_join_end
    const disc = (d && d.discovery) || {};
    iot.permit = !!disc.permit_join;
    const left = iot.permit && disc.permit_join_end ? Math.max(0, Math.round(Number(disc.permit_join_end) - Date.now() / 1000)) : 0;
    $("iotPairBtn").hidden = iot.permit;
    $("iotPairBtn").disabled = !d || !d.enabled || !d.connected;
    $("iotPairStop").hidden = !iot.permit;
    $("iotPairNote").textContent = iot.permit
      ? "Pairing is open" + (left ? " — " + Math.floor(left / 60) + ":" + String(left % 60).padStart(2, "0") + " left" : "") + ". Put the new plug into pairing mode now."
      : disc.zigbee2mqtt_online === false && d && d.connected ? "zigbee2mqtt is offline — check the launcher console."
      : "Put a new plug into pairing mode, then press Find.";
    const events = (Array.isArray(disc.events) ? disc.events : []).slice().sort((a, b) => (b.at || 0) - (a.at || 0)).slice(0, 8);
    $("iotEvents").replaceChildren(...events.map((e, i) =>
      el("div", { class: i === 0 ? "fresh" : "", text: [iotClock(e.at), e.type, e.device, e.status, e.description].filter(Boolean).join(" ") })));
  }
  async function iotRefresh() {
    clearTimeout(iot.timer); iot.timer = null;
    if (document.hidden) return;                                   // resumes on visibilitychange
    try { renderIot(await getJSON("/api/build_f/iot")); }
    catch (error) {
      $("iotDot").className = "dot err";
      $("iotSub").textContent = "unavailable";
      const hint = $("iotHint");
      hint.hidden = false; hint.style.color = "var(--err)";
      hint.textContent = "Smart home status unavailable: " + (error.message || error) + " — is the brain running?";
    }
    iot.timer = setTimeout(iotRefresh, iot.permit ? 1500 : 3000);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !iot.timer) iotRefresh(); });
  $("iotPairBtn").addEventListener("click", async () => {
    $("iotPairBtn").disabled = true;
    if (await iotPost("/api/build_f/iot/pairing", "iot-pairing", { enable: true, seconds: 254 })) toast("Pairing is open for about four minutes.");
    iotRefresh();
  });
  $("iotPairStop").addEventListener("click", async () => {
    if (await iotPost("/api/build_f/iot/pairing", "iot-pairing", { enable: false })) toast("Pairing closed.");
    iotRefresh();
  });
  iotRefresh();

  async function refresh() {
    try { apply(await getJSON("/api/build_f/tools")); }
    catch (error) {
      $("serversBox").replaceChildren(el("section", { class: "tech-card" },
        el("div", { class: "card-hint", text: "Tool layer unavailable: " + (error.message || error) + " — is the brain running?" })));
    }
  }
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 5000);
})();
