// Setup screen wiring (ui/index.html). Populates scenarios and models,
// handles scenario create/clone/edit/delete, runs the health checks
// (including a getUserMedia mic-permission probe with a live level meter),
// and on Start stores the chosen scenario id + the active panel's (Local or
// Claude) conversation / grammar corrector / explainer models + the Check
// model in sessionStorage before navigating to chat.html. Every choice is
// also saved to localStorage as soon as it's made, so the last-used choices
// pre-select themselves on a fresh visit instead of falling back to the
// server's bare defaults.

const SCENARIO_STORAGE_KEY = "vc2-scenario";
const MODEL_STORAGE_KEY = "vc2-model";
const GRAMMAR_MODEL_STORAGE_KEY = "vc2-grammar-model";
const EXPLAINER_MODEL_STORAGE_KEY = "vc2-explainer-model";
const CHECK_MODEL_STORAGE_KEY = "vc2-check-model";
const PROVIDER_STORAGE_KEY = "vc2-provider";

// The three roles each panel (Local / Claude) picks a model for. `key` is
// what chat.js reads from sessionStorage — Start writes the active panel's
// picks there. Each panel also remembers its own picks in localStorage, so
// switching panels back and forth (or restarting the browser) keeps both:
// the Local panel under the original keys (so picks saved before the
// Claude panel existed carry over), the Claude panel under "vc2-claude-*".
const ROLES = [
    { key: MODEL_STORAGE_KEY, defaultKey: "default", local: "modelSelect", claude: "claudeModelSelect" },
    {
        key: GRAMMAR_MODEL_STORAGE_KEY,
        defaultKey: "grammar_default",
        local: "grammarModelSelect",
        claude: "claudeGrammarModelSelect",
    },
    {
        key: EXPLAINER_MODEL_STORAGE_KEY,
        defaultKey: "explainer_default",
        local: "explainerModelSelect",
        claude: "claudeExplainerModelSelect",
    },
];

function panelStorageKey(provider, role) {
    return provider === "local" ? role.key : role.key.replace("vc2-", "vc2-claude-");
}

let scenarios = [];
let selectedScenarioId = null;
let provider = "local";
// picks[provider][role.key] = model name; checkModel is shared by both.
const picks = { local: {}, claude: {} };
let checkModel = null;

let formMode = null; // "create" | "clone" | "edit" | null
let formEditingId = null;

let micStream = null;
let micAudioContext = null;
let micRafId = null;
let micAnalyser = null;
let micAnalyserData = null;

function slugify(text) {
    return text.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/(^-+|-+$)/g, "") || "scenario";
}

function updateStartButton() {
    document.getElementById("startBtn").disabled = !(
        selectedScenarioId &&
        ROLES.every((role) => picks[provider][role.key]) &&
        checkModel
    );
}

// ---- Scenarios ----

function renderScenarios() {
    const container = document.getElementById("scenarioList");
    container.innerHTML = "";
    if (!scenarios.length) {
        container.textContent = "(no scenarios available)";
        return;
    }

    for (const scenario of scenarios) {
        const item = document.createElement("div");
        item.className = "scenario-item";

        const radioLabel = document.createElement("label");
        radioLabel.className = "scenario-radio";

        const radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "scenario";
        radio.value = scenario.id;
        radio.checked = scenario.id === selectedScenarioId;
        radio.addEventListener("change", () => {
            selectedScenarioId = scenario.id;
            updateStartButton();
        });

        const name = document.createElement("span");
        name.className = "scenario-name";
        name.textContent = scenario.label;

        radioLabel.appendChild(radio);
        radioLabel.appendChild(name);
        if (scenario.is_builtin) {
            const badge = document.createElement("span");
            badge.className = "badge badge-neutral";
            badge.textContent = "built-in";
            radioLabel.appendChild(badge);
        }

        const actions = document.createElement("div");
        actions.className = "scenario-actions";
        if (scenario.is_builtin) {
            const cloneBtn = document.createElement("button");
            cloneBtn.type = "button";
            cloneBtn.className = "btn-secondary";
            cloneBtn.textContent = "Clone & edit";
            cloneBtn.addEventListener("click", () => openScenarioForm("clone", scenario));
            actions.appendChild(cloneBtn);
        } else {
            const editBtn = document.createElement("button");
            editBtn.type = "button";
            editBtn.className = "btn-secondary";
            editBtn.textContent = "Edit";
            editBtn.addEventListener("click", () => openScenarioForm("edit", scenario));

            const deleteBtn = document.createElement("button");
            deleteBtn.type = "button";
            deleteBtn.className = "btn-danger";
            deleteBtn.textContent = "Delete";
            deleteBtn.addEventListener("click", () => deleteScenario(scenario));

            actions.appendChild(editBtn);
            actions.appendChild(deleteBtn);
        }

        item.appendChild(radioLabel);
        item.appendChild(actions);
        container.appendChild(item);
    }
}

async function loadScenarios() {
    const container = document.getElementById("scenarioList");
    try {
        const resp = await fetch("/api/scenarios");
        const data = await resp.json();
        scenarios = data.scenarios;
        if (!selectedScenarioId || !scenarios.some((s) => s.id === selectedScenarioId)) {
            selectedScenarioId = data.default;
        }
        renderScenarios();
    } catch (error) {
        container.textContent = `(scenario list unavailable: ${error.message})`;
    }
    updateStartButton();
}

function openScenarioForm(mode, scenario) {
    formMode = mode;
    formEditingId = mode === "edit" ? scenario.id : null;

    const titles = { create: "New scenario", clone: "Clone & edit scenario", edit: "Edit scenario" };
    document.getElementById("scenarioFormTitle").textContent = titles[mode];

    const nameInput = document.getElementById("scenarioNameInput");
    const promptInput = document.getElementById("scenarioPromptInput");
    if (mode === "create") {
        nameInput.value = "";
        promptInput.value = "";
    } else if (mode === "clone") {
        nameInput.value = `${scenario.label} (copy)`;
        promptInput.value = scenario.prompt;
    } else {
        nameInput.value = scenario.label;
        promptInput.value = scenario.prompt;
    }

    document.getElementById("scenarioForm").hidden = false;
    nameInput.focus();
}

function closeScenarioForm() {
    formMode = null;
    formEditingId = null;
    document.getElementById("scenarioForm").hidden = true;
}

async function saveScenarioForm() {
    const label = document.getElementById("scenarioNameInput").value.trim();
    const prompt = document.getElementById("scenarioPromptInput").value;
    if (!label) {
        alert("Name is required.");
        return;
    }

    try {
        let saved;
        if (formMode === "edit") {
            const resp = await fetch(`/api/scenarios/${encodeURIComponent(formEditingId)}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ label, prompt }),
            });
            if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
            saved = await resp.json();
        } else {
            const id = `${slugify(label)}-${Date.now().toString(36)}`;
            const resp = await fetch("/api/scenarios", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ id, label, prompt }),
            });
            if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
            saved = await resp.json();
        }
        selectedScenarioId = saved.id;
        closeScenarioForm();
        await loadScenarios();
    } catch (error) {
        alert(`Couldn't save scenario: ${error.message}`);
    }
}

async function deleteScenario(scenario) {
    if (!confirm(`Delete "${scenario.label}"?`)) return;
    try {
        const resp = await fetch(`/api/scenarios/${encodeURIComponent(scenario.id)}`, { method: "DELETE" });
        if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
        if (selectedScenarioId === scenario.id) selectedScenarioId = null;
        await loadScenarios();
    } catch (error) {
        alert(`Couldn't delete scenario: ${error.message}`);
    }
}

// ---- Model ----

function showProvider() {
    document.getElementById("localPanel").hidden = provider !== "local";
    document.getElementById("claudePanel").hidden = provider !== "claude";
    for (const radio of document.querySelectorAll('input[name="provider"]')) {
        radio.checked = radio.value === provider;
    }
}

// Fills one dropdown from `models` ({name, note}), selecting `current` if
// it's still in the list, else `fallback`. Each option's note (past testing
// findings) is appended to its label so it's visible while picking. Returns
// the selected name.
function fillSelect(select, models, current, fallback) {
    const names = models.map((m) => m.name);
    const chosen = current && names.includes(current) ? current : fallback;
    select.innerHTML = "";
    for (const model of models) {
        const opt = document.createElement("option");
        opt.value = model.name;
        opt.textContent = model.note ? `${model.name} — ${model.note}` : model.name;
        if (model.name === chosen) opt.selected = true;
        select.appendChild(opt);
    }
    return chosen;
}

// Wires a dropdown to save its pick immediately (not only on Start), so a
// refresh keeps it, and pauses the mic meter while its popup is open (see
// CLAUDE.md Gotchas: the meter's per-frame repaint dismissed the popup).
function watchSelect(select, storageKey, onPick) {
    select.addEventListener("change", () => {
        if (select.value) {
            onPick(select.value);
            localStorage.setItem(storageKey, select.value);
        }
        updateStartButton();
    });
    select.addEventListener("focus", pauseMicMeter);
    select.addEventListener("blur", resumeMicMeter);
}

async function loadModels() {
    try {
        const resp = await fetch("/api/models");
        const data = await resp.json();
        for (const role of ROLES) {
            for (const [panel, models, defaults] of [
                ["local", data.models, data],
                ["claude", data.claude_models, data.claude_defaults],
            ]) {
                const storageKey = panelStorageKey(panel, role);
                picks[panel][role.key] = fillSelect(
                    document.getElementById(role[panel]),
                    models,
                    localStorage.getItem(storageKey),
                    defaults[role.defaultKey]
                );
            }
        }
        checkModel = fillSelect(
            document.getElementById("checkModelSelect"),
            data.claude_models,
            localStorage.getItem(CHECK_MODEL_STORAGE_KEY),
            data.check_default
        );
    } catch (error) {
        for (const id of ["modelStatus", "claudeModelStatus", "checkModelStatus"]) {
            document.getElementById(id).textContent = `Error loading models: ${error.message}`;
        }
    }
    updateStartButton();

    for (const role of ROLES) {
        for (const panel of ["local", "claude"]) {
            watchSelect(document.getElementById(role[panel]), panelStorageKey(panel, role), (v) => {
                picks[panel][role.key] = v;
            });
        }
    }
    watchSelect(document.getElementById("checkModelSelect"), CHECK_MODEL_STORAGE_KEY, (v) => {
        checkModel = v;
    });
    for (const radio of document.querySelectorAll('input[name="provider"]')) {
        radio.addEventListener("change", () => {
            provider = radio.value;
            localStorage.setItem(PROVIDER_STORAGE_KEY, provider);
            showProvider();
            updateStartButton();
        });
    }
}

function initUnloadModelButton() {
    const btn = document.getElementById("unloadModelBtn");
    const statusEl = document.getElementById("modelStatus");
    btn.addEventListener("click", async () => {
        btn.disabled = true;
        const previousStatus = statusEl.textContent;
        statusEl.textContent = "unloading…";
        try {
            const resp = await fetch("/api/unload-model", { method: "POST" });
            const data = await resp.json();
            if (data.error) {
                statusEl.textContent = `Error: ${data.error}`;
            } else if (data.unloaded.length) {
                statusEl.textContent = `unloaded: ${data.unloaded.join(", ")}`;
            } else {
                statusEl.textContent = "no model loaded";
            }
        } catch (error) {
            statusEl.textContent = `Error: ${error.message}`;
        } finally {
            btn.disabled = false;
            setTimeout(() => {
                statusEl.textContent = previousStatus;
            }, 4000);
        }
    });
}

// ---- Health checks ----

function setBadge(id, status, detail) {
    const badge = document.getElementById(id);
    badge.className = `badge badge-${status === "ok" ? "ok" : status === "checking" ? "neutral" : "error"}`;
    badge.textContent = status === "ok" ? "ok" : status === "checking" ? "checking…" : "error";
    badge.title = detail || "";
}

async function runHealthChecks() {
    setBadge("healthOllamaBadge", "checking");
    setBadge("healthSrtBadge", "checking");
    setBadge("healthTtsBadge", "checking");
    try {
        const resp = await fetch("/api/health");
        const data = await resp.json();
        setBadge("healthOllamaBadge", data.ollama.status, data.ollama.detail);
        setBadge("healthSrtBadge", data.srt.status, data.srt.detail);
        setBadge("healthTtsBadge", data.tts.status, data.tts.detail);
    } catch (error) {
        setBadge("healthOllamaBadge", "error", error.message);
        setBadge("healthSrtBadge", "error", error.message);
        setBadge("healthTtsBadge", "error", error.message);
    }
}

function stopMic() {
    pauseMicMeter();
    micAnalyser = null;
    micAnalyserData = null;
    if (micAudioContext) {
        micAudioContext.close();
        micAudioContext = null;
    }
    if (micStream) {
        micStream.getTracks().forEach((track) => track.stop());
        micStream = null;
    }
}

// Runs every animation frame while active, which continuously repaints the
// meter bar. A native <select> popup (the model dropdown) shares the same
// compositor and gets dismissed by that repaint mid-interaction — the user
// sees it flash open and immediately pick whatever option was under the
// cursor. pauseMicMeter()/resumeMicMeter() bracket the model select's
// focus so the meter stops animating for as long as that dropdown is open.
function meterTick() {
    const fill = document.getElementById("micMeterFill");
    micAnalyser.getByteTimeDomainData(micAnalyserData);
    let peak = 0;
    for (const sample of micAnalyserData) {
        peak = Math.max(peak, Math.abs(sample - 128) / 128);
    }
    const pct = Math.min(100, Math.round(peak * 100));
    fill.style.width = `${pct}%`;
    fill.classList.toggle("loud", pct >= 70 && pct < 95);
    fill.classList.toggle("clip", pct >= 95);
    micRafId = requestAnimationFrame(meterTick);
}

function pauseMicMeter() {
    if (micRafId !== null) {
        cancelAnimationFrame(micRafId);
        micRafId = null;
    }
}

function resumeMicMeter() {
    if (micAnalyser && micRafId === null) {
        meterTick();
    }
}

function startMicMeter(stream) {
    micAudioContext = new AudioContext();
    const source = micAudioContext.createMediaStreamSource(stream);
    micAnalyser = micAudioContext.createAnalyser();
    micAnalyser.fftSize = 2048;
    source.connect(micAnalyser);
    micAnalyserData = new Uint8Array(micAnalyser.fftSize);

    document.getElementById("micMeterHint").textContent = "Speak to test your microphone.";
    resumeMicMeter();
}

async function initMic() {
    setBadge("healthMicBadge", "checking");
    document.getElementById("micMeterWrap").hidden = true;
    try {
        micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        setBadge("healthMicBadge", "ok");
        document.getElementById("micMeterWrap").hidden = false;
        startMicMeter(micStream);
    } catch (error) {
        setBadge("healthMicBadge", "error", error.message);
    }
}

// ---- Init ----

document.addEventListener("DOMContentLoaded", () => {
    selectedScenarioId = sessionStorage.getItem(SCENARIO_STORAGE_KEY) ?? localStorage.getItem(SCENARIO_STORAGE_KEY);
    provider = localStorage.getItem(PROVIDER_STORAGE_KEY) === "claude" ? "claude" : "local";
    showProvider();

    loadScenarios();
    loadModels();
    initUnloadModelButton();
    runHealthChecks();
    initMic();

    document.getElementById("newScenarioBtn").addEventListener("click", () => openScenarioForm("create"));
    document.getElementById("scenarioFormSave").addEventListener("click", saveScenarioForm);
    document.getElementById("scenarioFormCancel").addEventListener("click", closeScenarioForm);
    document.getElementById("recheckHealthBtn").addEventListener("click", () => {
        runHealthChecks();
        stopMic();
        initMic();
    });

    document.getElementById("startBtn").addEventListener("click", () => {
        sessionStorage.setItem(SCENARIO_STORAGE_KEY, selectedScenarioId);
        localStorage.setItem(SCENARIO_STORAGE_KEY, selectedScenarioId);
        // The active panel's picks become what chat.js uses; also saved as
        // that panel's picks, in case they were untouched server defaults.
        for (const role of ROLES) {
            const model = picks[provider][role.key];
            sessionStorage.setItem(role.key, model);
            localStorage.setItem(panelStorageKey(provider, role), model);
        }
        sessionStorage.setItem(CHECK_MODEL_STORAGE_KEY, checkModel);
        localStorage.setItem(CHECK_MODEL_STORAGE_KEY, checkModel);
        localStorage.setItem(PROVIDER_STORAGE_KEY, provider);
        window.location.href = "chat.html";
    });

    // Release the mic on navigation — chat.html acquires its own stream.
    window.addEventListener("beforeunload", stopMic);
});
