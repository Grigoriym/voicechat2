# CHECKLIST

Progress: UI rework 12/12 (done); grammar-check pass 3/3 (done); backlog 3/3 (done) — Current step: none

Executable plan for larger, multi-step changes (the upcoming UI work, mainly) — not required
for a one-off fix or a single well-scoped feature, which can just be done directly. Numbered,
tickable steps, each self-contained with a `Verify:` line. This project is small enough that a
separate done-archive file isn't worth the split — ticked steps just stay here.

When asked to "do step N": read this file, do exactly that step, run its `Verify:` line, then
tick the box, add a one-line `Note:` if anything deviated from the plan, and update the
`Progress` / `Current step` header above.

Don't start a step whose dependencies aren't ticked. Don't expand scope beyond the step — if it
turns up a real, separate problem, write it into `docs/revisit.md` instead of fixing it inline.

## UI rework: setup screen + conversation screen

Goal: replace the current single all-in-one page with two screens — a **Setup** screen
(`ui/index.html`) to pick or create a conversation context, choose a model, and confirm
mic/Ollama/STT/TTS are all working, then a **Conversation** screen (`ui/chat.html`) for the
actual talking. Decided up front (2026-08-28), don't re-litigate:

- **Navigation: two separate HTML pages**, not a single-page hash router. State (chosen
  scenario id + model) crosses the page load via `sessionStorage`. Chosen because it matches
  how the user described the flow ("a screen to pick a topic, then the next screen for the
  convo") and keeps each page's JS smaller — the user has no web background and asked for the
  simpler mental model.
- **Latency metrics stay**, moved into a `<details>` "Debug" panel on the conversation screen,
  collapsed by default.
- **Built-in scenarios are read-only.** Editing one clones it into a new custom scenario
  first; the original preset can't be edited or deleted, only cloned. Custom scenarios are
  fully editable/deletable.
- No CSS framework/CDN dependency — hand-rolled CSS with custom properties for theming. This
  app runs fully local/offline already; the project got burned once by a CDN dependency going
  dead (`symbl-opus-encdec`, see "Client-side recording" in `CLAUDE.md`) and there's no reason
  to add a new one for styling.

## Steps

- [x] 1. Design tokens + base CSS shell
      `ui/css/theme.css`: CSS custom properties for light/dark (`prefers-color-scheme` plus a
      manual `data-theme` override), reset, typography, and reusable button/card/input styles
      shared by both pages.
      Verify: apply to the current `ui/index.html` temporarily, confirm OS theme toggle changes
      colors, no console errors.
      Note: linked `theme.css` before the existing inline `<style>` block so the current page's
      look is unchanged for now (full application happens in step 7's rewrite). Chrome
      automation wasn't connected this session, so verification was server-side instead of a
      live browser toggle: rebuilt+redeployed the `vc2` container, confirmed `/` still returns
      200 with the `<link>` tag present, `theme.css` serves as `text/css` with balanced braces
      (26/26). A real OS-theme-toggle visual check is still owed once step 7 actually uses the
      tokens and a browser session is available.

- [x] 2. Theme toggle control + persistence
      `ui/js/theme.js`: reads/writes `localStorage`, toggles `data-theme` on `<html>`, exposes a
      toggle button's markup + behavior for both pages to include.
      Verify: toggle in the UI, refresh the page, the choice persists.
      Note: Chrome automation still isn't connected this session, so the manual browser pass
      is deferred (same gap as step 1 — real OS-toggle/click verification still owed once a
      browser session is available). Verified instead by: (1) serving checks — `theme.js`
      returns 200 as `text/javascript` from the rebuilt `vc2` container, `index.html` includes
      the `<script>` tag and a `#theme-toggle` button; (2) `node --check` for syntax; (3) running
      the actual module logic under a minimal Node DOM/localStorage stub simulating a click and
      a page refresh — confirmed no stored preference leaves `data-theme` unset (so
      `prefers-color-scheme` still governs), a click sets `data-theme="dark"` and
      `localStorage["vc2-theme"]="dark"` and flips the button label, and on a simulated reload
      the stored value is re-applied to `<html>` before `DOMContentLoaded` (no flash) with the
      button label matching. Wired the button + script into `index.html` temporarily for this
      (same pattern as step 1) — full markup/placement is step 7's job when the page is
      rewritten as the Setup screen.

- [x] 3. Health endpoints on the STT and TTS servers
      `srt-server.py` `GET /health`: probes the configured engine's backing service (for
      `WhisperWebserviceEngine`, a short request to `WHISPER_WEBSERVICE_URL`'s base) and returns
      `{"status": "ok"|"error", "detail": ...}`. `tts-server-piper.py` `GET /health`: checks
      `PIPER_BIN` and `PIPER_MODEL` exist on disk.
      Verify: curl each with the backend up; then stop the whisper container / rename the piper
      binary and confirm each reports unhealthy instead of hanging or 500ing.
      Note: `srt-server.py`'s health check only probes a backing service for
      `WhisperWebserviceEngine` (the active/default engine) — `TransformersEngine` and
      `FasterWhisperEngine` load models in-process with nothing external to check, so they
      trivially report ok, matching the existing `isinstance(engine, ...)` branching style
      already used in `/inference`. Verified live: rebuilt+restarted the `srt`/`tts` containers,
      curled both `/health` with backends up (`{"status":"ok"}` from each); stopped the real
      `whisper` container and curled `srt`'s `/health` again (503 with a connection-refused
      detail, not a hang or 500), then restarted `whisper` and confirmed `/health` went back to
      ok. For the piper-binary case, rather than renaming the real (read-only-mounted, shared
      with `~/bin/deutsch`) binary, ran a disposable one-off container from the same image with
      `PIPER_BIN` pointed at a nonexistent path on a spare port — confirmed 503 with a "missing:"
      detail — then discarded it; the real `tts` container was untouched and still reports ok.
      Also added pytest coverage (`test/test_srt_server.py`, `test/test_tts_piper.py`) for both
      endpoints' ok/error paths; `ruff check`, `ruff format`, `mypy`, and `pytest -q` (22 passed)
      all green.

- [x] 4. Aggregate health endpoint on the orchestrator
      `voicechat2.py` `GET /api/health`: calls Ollama's `/api/tags`, srt-server's `/health`, and
      tts-server's `/health` concurrently, returns per-service ok/error.
      Verify: pytest against a stubbed stack; curl it locally with the full `docker compose`
      stack up.
      Note: srt/tts URLs are derived from `SRT_ENDPOINT`/`TTS_ENDPOINT` (existing env vars) by
      swapping the path to `/health`, rather than adding two new env vars for the same hosts.
      The srt/tts checks forward whatever JSON body their own `/health` returns (they already
      distinguish ok/error with a `detail`); the Ollama check has no such body to forward, so it
      just reports ok/error from whether the request succeeded, matching the no-status-check
      style already used by `/api/models` and `/api/unload-model`. Added 3 pytest cases
      (`test/test_voicechat2.py`): all-ok, one service's own `/health` reporting error, and the
      orchestrator itself unable to reach a service. `ruff check`, `ruff format --check`,
      `mypy`, and `pytest -q` (25 passed) all green. Verified live against the full
      `docker compose` stack: rebuilt+restarted all three containers, curled `/api/health`
      (all ok), stopped the `srt` container and curled again (srt reports the connection-refused
      detail, ollama/tts stay ok), restarted `srt` and confirmed it went back to ok.

- [x] 5. Custom-scenario storage
      Load/save custom scenarios to a JSON file (gitignored) merged with the hardcoded
      `SCENARIOS` at request time. A custom scenario can't reuse a built-in id.
      Verify: pytest covering create/list/delete round-trip against a temp file.
      Note: added `load_custom_scenarios`/`save_custom_scenarios`/`create_custom_scenario`/
      `delete_custom_scenario`/`get_all_scenarios` to `voicechat2.py`, all taking an explicit
      `path` param (default `CUSTOM_SCENARIOS_PATH` env var, default `custom_scenarios.json`)
      so tests can round-trip against a `tmp_path` file without touching a real one. Storage
      only, per this step's scope — the HTTP endpoints, and wiring `build_system_message`/
      `set_scenario` to see custom scenarios, are step 6's job. `create_custom_scenario` raises
      `ValueError` for a built-in id or an already-existing custom id (edits are PUT in step 6,
      not create). Added `custom_scenarios.json` to `.gitignore`. 4 new pytest cases in
      `test/test_voicechat2.py`; `ruff check`, `ruff format --check`, `mypy`, `pytest -q`
      (29 passed) all green.

- [x] 6. Scenario CRUD endpoints
      `POST /api/scenarios` (create), `PUT /api/scenarios/{id}` (edit, custom only),
      `DELETE /api/scenarios/{id}` (delete, custom only). `GET /api/scenarios` now also returns
      the full prompt text and an `is_builtin` flag per scenario.
      Verify: pytest + curl round-trip; editing/deleting a built-in id returns 4xx.
      Note: added `update_custom_scenario` (step 5 only had create/delete) and tightened
      `delete_custom_scenario` to reject a built-in id with a clear message instead of falling
      through to "not found" (it wasn't in the custom store either way, but the message was
      misleading). Also completed the wiring step 5's note flagged as this step's job:
      `build_system_message` and the websocket `set_scenario` handler now check
      `get_all_scenarios()` instead of the hardcoded `SCENARIOS` dict, so a custom scenario is
      actually usable in a conversation, not just creatable. Request bodies use small pydantic
      `BaseModel`s (`ScenarioCreate`, `ScenarioUpdate`) — first use of pydantic directly in this
      file, though it's already a transitive FastAPI dependency. All four ValueError cases
      (duplicate id, built-in edit, built-in delete, missing custom id) map to a flat 400,
      matching the checklist's "4xx" bar without adding 403/404 branching the step didn't ask
      for. 11 new pytest cases in `test/test_voicechat2.py` (function-level round-trips plus
      `TestClient` HTTP round-trips against `/api/scenarios`, monkeypatching
      `voicechat2.CUSTOM_SCENARIOS_PATH` to a `tmp_path` file); `ruff check`, `ruff format
      --check`, `mypy`, `pytest -q` (37 passed) all green. Verified live: rebuilt+restarted the
      `vc2` container, curled the full create → duplicate-reject(400) → edit-builtin-reject(400)
      → delete-builtin-reject(400) → edit → list → delete → delete-missing-reject(400) sequence
      against `localhost:8010`, all behaved as expected; `custom_scenarios.json` lives inside the
      container only (not bind-mounted), confirmed nothing leaked into the repo afterward.

- [x] 7. Setup screen markup + layout (`ui/index.html` rewritten)
      Scenario list (built-ins badged), "New scenario" and "Clone & edit" flows (name + prompt
      textarea), model dropdown, health-check panel, "Start conversation" button (disabled until
      a scenario and model are chosen).
      Verify: manual browser pass — create a scenario, edit a clone, watch the health panel
      react to a killed backend, confirm the Start button gates correctly.
      Note: markup/layout only, per this step's scope — no JS wiring yet (that's step 8's job),
      so scenario list/model dropdown/health badges are static "loading…"/"checking…"
      placeholders and Start stays disabled; the interactive parts of this step's Verify line
      (create/edit-a-clone, health panel reacting to a killed backend, Start gating) are deferred
      to step 8, which is what actually implements that behavior. Added `ui/css/setup.css` for
      this screen's own layout (scenario list/form, health rows, start button) alongside the
      shared `theme.css`; removed the old single-page inline `<style>`/`<script>` and the
      `symbl-opus-encdec`/`onnxruntime-web`/`vad-web` CDN script tags entirely (recording/VAD/
      conversation UI moves to `ui/js/chat.js` + `chat.html` in step 9, not needed here). Kept
      the `lang-badge` subtitle and the existing `#modelSelect`/`#unloadModelBtn`/`#modelStatus`
      element ids so step 8 can reuse the same wiring logic the old page had.
      Chrome automation was connected this session (unlike steps 1–2): rebuilt+redeployed the
      `vc2` container, loaded `localhost:8010`, confirmed the Setup screen renders with no
      console errors, and clicked the theme toggle to confirm dark mode still applies correctly
      on the new markup. `ruff check`, `ruff format --check`, `mypy`, `pytest -q` (37 passed)
      all still green (no Python touched this step, confirmed nothing broke).

- [x] 8. Setup screen wiring (`ui/js/setup.js`)
      Fetch/populate scenarios and models, handle create/clone/edit/delete, run health checks
      (including a `getUserMedia` mic-permission probe), store the chosen scenario id + model in
      `sessionStorage`, navigate to `chat.html` on Start.
      Verify: same manual pass as step 7, plus confirm the `sessionStorage` keys are set
      correctly (devtools).
      Note: reused the old page's model-select/unload-button fetch logic (git history at
      `fc80aab:ui/index.html`) rather than rewriting it from scratch. New-scenario ids are
      generated client-side as `slugify(label)-{Date.now().toString(36)}` (server rejects
      duplicate/built-in ids with 400; the timestamp suffix makes a collision practically
      impossible rather than adding retry logic for it). Start-button gating is scenario+model
      only, matching step 7's markup note — not gated on health-check results. The mic health
      check acquires a real `getUserMedia` stream and drives a live level meter (peak-based,
      `.loud`/`.clip` classes already defined in `setup.css`); the stream is released on
      `beforeunload` since `chat.html` (step 9) will acquire its own. `sessionStorage` keys are
      `vc2-scenario`/`vc2-model`, read back on load so a returning visit keeps the prior choice,
      and written on Start just before navigating to `chat.html` (that page doesn't exist until
      step 9, so the actual navigation wasn't exercised — verified the keys get set instead, see
      below). No JS test harness exists in this project, so verification was the manual browser
      pass the step's Verify line asks for, via Chrome automation (connected this session):
      rebuilt+redeployed the `vc2` container; loaded `localhost:8010` and confirmed scenarios,
      model dropdown (`llama3.1:8b` default selected), and all four health badges (ollama/srt/
      tts/mic) render `ok` with no console errors; exercised the full scenario CRUD loop —
      created a custom scenario (auto-selected after save), edited its name (edit persisted and
      stayed selected), cloned a built-in ("Shopping" → pre-filled "Shopping (copy)" + its
      prompt text, cancelled without saving), then deleted the custom one (`window.confirm`
      stubbed via the JS tool to avoid a blocking native dialog, per the browser-automation
      guidance against triggering alerts) — confirmed via a follow-up `curl /api/scenarios` that
      no custom scenarios leaked past the test. Start button had no `disabled` attribute once a
      scenario+model were selected (true by default, since both auto-select). `ruff check`,
      `ruff format --check`, `mypy`, `pytest -q` (37 passed, unchanged — no Python touched this
      step) all still green.

- [x] 9. Conversation screen (`ui/chat.html`), extracted from the current `index.html`
      Move the recording/transcript/status UI here, trimmed of the model and scenario dropdowns
      (chosen on Setup now). Read scenario + model from `sessionStorage` on load and send
      `set_scenario`/`set_model` on socket open.
      Verify: manual pass — talk through a full turn, confirm the chosen scenario's prompt is
      actually active (check server logs).
      Note: pulled the recording/VAD/websocket/transcript/latency-metrics code from the
      pre-rework page (git history at `fc80aab:ui/index.html`) into `ui/js/chat.js`, with the
      model/scenario `<select>` elements and their fetch/populate logic dropped entirely —
      `chat.js` reads `vc2-scenario`/`vc2-model` from `sessionStorage` (same keys `setup.js`
      writes) and sends `set_model`/`set_scenario` on websocket open, same as the old page did
      from its dropdown values. Kept the VAD toggle (still the old, documented-broken
      `symbl-opus-encdec` path — see "Client-side recording" in CLAUDE.md) and its three CDN
      script tags, since step 7's note explicitly deferred VAD/conversation UI to this step
      rather than dropping it. Added `ui/css/chat.css` (page-scoped, like `setup.css`) for the
      record button, status badge, meter, transcript, and latency table, translated from the old
      inline `<style>` block onto `theme.css`'s custom properties. Added a small `activeContext`
      line ("scenario: X · model: Y") under a "← Setup" back-link — not explicitly asked for, but
      a minimal, obvious echo of the sessionStorage read the step already requires, and the only
      way back to Setup without editing the URL by hand.
      Also added `voicechat2.py`'s `GET /chat.html` route (mirrors the existing `/` → `ui/index.html`
      one). Without it the Start button's `chat.html` navigation (already written in step 8, and
      relative to `/`) 404'd — needed for this step's own page to be reachable at all, not a
      separate feature. `ruff check`, `ruff format --check`, `mypy`, `pytest -q` (37 passed,
      unchanged — no test-covered logic touched) all green.
      Verified live via Chrome automation: rebuilt+redeployed `vc2`; ran the full Setup → Start
      flow (picked "Restaurant", `llama3.1:8b`, all four health checks green) and landed on
      `/chat.html` with `scenario: Restaurant · model: llama3.1:8b` displayed; confirmed via
      `docker compose logs vc2` that the websocket actually received and applied both
      `set_model`/`set_scenario` for that session ("switched to model: llama3.1:8b" /
      "switched to scenario: restaurant") immediately on connect — the step's "check server logs"
      bar. Theme toggle and the "← Setup" link both worked on the new page; no console errors at
      any point. Talking through a full turn with real spoken audio isn't possible through this
      automation (no real microphone input), so exercised push-to-talk with a single click
      instead (near-simultaneous mousedown/mouseup) — this hit a real, pre-existing bug: a
      110-byte/0.1s capture (non-zero bytes, but no actual audio) isn't caught by
      `srt-server.py`'s empty-`audio_content` guard and 500s on `response.json()` decoding a
      non-JSON reply from the whisper webservice. Confirmed via `git diff`/reading `srt-server.py`
      that this code path is untouched by this step and pre-existing, not a regression — logged
      as `docs/revisit.md` #1 rather than fixed here (out of scope for a UI-extraction step).

- [x] 10. Collapsible debug/metrics panel
      Move the latency-metrics table into a `<details>` section on `chat.html`, closed by
      default.
      Verify: manual — panel starts closed, opens on click, still updates with real numbers
      during a turn.
      Note: swapped the `#latencyMetrics` `<div class="card">` for `<details id="latencyMetrics"
      class="card"><summary>Debug: latency metrics</summary>…</details>` — no id/JS changes, so
      `chat.js`'s existing `getElementById` writes into the table cells (`totalVoiceToVoice`,
      `srtDuration`, etc.) are untouched and unaffected by the collapsed/expanded state (`hidden`
      via the browser's native `<details>` behavior, not `display:none`, so DOM writes still land
      while closed). Added `#latencyMetrics summary` styling in `chat.css` (bold, muted color,
      pointer cursor, margin-bottom when open) since no `<details>`/`<summary>` styling existed
      anywhere in the theme yet. Rebuilt+redeployed `vc2` (also rebuilt `srt`/`tts` — same base
      image layer, no code changes to those two this step); verified live via Chrome automation:
      full Setup → Start flow landed on `/chat.html` with the panel showing "▶ Debug: latency
      metrics" collapsed by default, clicking it expanded to "▼" and revealed the table with its
      0ms placeholder values, no console errors on load or after. As step 9 noted, real spoken
      audio isn't exercisable through this automation (no real mic input), so "updates with real
      numbers during a turn" wasn't re-verified live here — the update logic itself is unchanged
      from step 9's working version, only its container markup changed.

- [x] 11. Retire the old single-page markup/JS
      Delete what's now dead from the pre-rework `index.html`/inline script; keep whatever's
      still used (audio recording, websocket handling) factored into `ui/js/chat.js`.
      Verify: `ruff check . && ruff format . && mypy . && pytest -q` still pass; grep confirms
      nothing still references a removed id or function.
      Note: nothing left to delete — steps 7 (index.html rewritten from scratch) and 9
      (chat.html/chat.js assembled fresh, pulling only the still-used pieces from the
      pre-rework page via git history) already retired the old single-page markup/JS as they
      went, rather than leaving dead code for this step to clean up later. Verified rather than
      assumed: diffed the pre-rework `index.html` (`git show 219f120:ui/index.html`, the last
      commit before step 7) against the current pages — its two dropped ids (`scenarioSelect`,
      `scenarioStatus`, replaced by the new scenario-list/form UI) have zero references anywhere
      in `ui/`, `voicechat2.py`, or `test/`; every id in the current `index.html`/`chat.html` has
      a matching `getElementById`/`querySelector` in `setup.js`/`chat.js` and vice versa; every
      function defined in `chat.js`/`setup.js` is called at least once (checked by grepping each
      function name's occurrence count). `ruff check`, `ruff format --check`, `mypy`, `pytest -q`
      (37 passed) all green, unchanged from step 10 since no files needed touching.

- [x] 12. Docker/static-serving check
      Confirm the `/` route and the `/ui` `StaticFiles` mount in `voicechat2.py` still serve the
      right entry file — `index.html` is the Setup screen now, not the conversation screen.
      Verify: `docker compose up -d --build`, browser hit on `localhost:8010`, confirm the Setup
      screen loads first and Start correctly reaches the conversation screen.
      Note: routes were already correct from step 9's `/chat.html` addition — `/` → `ui/index.html`
      (Setup), `/chat.html` → `ui/chat.html` (Conversation), and the `/ui` `StaticFiles` mount
      serves both files directly too (`/ui/index.html`, `/ui/chat.html`). No code changes needed;
      this step was pure verification. `docker compose up -d --build` rebuilt all three
      containers; curled all four paths (`/`, `/chat.html`, `/ui/index.html`, `/ui/chat.html`),
      all 200, with `<title>` confirming the right page each time ("voicechat2 — Setup" /
      "voicechat2 — Conversation"). Verified live via Chrome automation: loaded `localhost:8010`,
      confirmed the Setup screen renders first (scenario list, model dropdown, all four health
      badges green); clicked "Start conversation" and landed on `/chat.html` with
      "scenario: General / free talk · model: llama3.1:8b" correctly carried over via
      `sessionStorage`; no console errors. This closes out the UI rework checklist (12/12).

## Grammar-check pass: inline correctness badge

Goal: after each turn (the user's spoken input and the AI's reply), silently score whether the German
is grammatically correct and show the result inline in the transcript — a checkmark if it's fine, the
corrected sentence directly if not — using the `cas/discolm-mfto-german` German-specialized model
(pulled 2026-08-28) as a dedicated checker, separate from whichever model is driving the conversation.
Decided up front, don't re-litigate:

- **Both user and AI messages get checked.** The AI's own German isn't automatically reliable either —
  this whole feature started from spotting a garbled `llama3.1:8b` reply.
- **Shown inline in the chat bubble**, not tucked in the debug panel: a ✓ badge on a correct message, a
  "→ corrected: ..." line directly under an incorrect one. No click-to-reveal.
- **Runs automatically after every turn**, fired as a background task so it doesn't block the
  perceived voice-to-voice latency of the main pipeline — the badge may land a beat after the
  reply/audio does.
- **Separate, fixed check model** via a new `GRAMMAR_CHECK_MODEL` env var, defaulting to
  `cas/discolm-mfto-german:latest`. Not selectable from the UI — a backend concern like `LLM_MODEL`.
- Prompt/parsing contract was hand-validated against the running model before writing this plan: a
  system prompt asking for exactly `OK` or `CORRECTED: <sentence>` returned a cleanly parseable reply
  for a correct AI-style sentence, a broken AI-style sentence, and a broken user-style sentence (all
  three via direct `curl` to the Ollama endpoint).

Update (2026-08-30): `cas/discolm-mfto-german:latest` turned out unreliable in practice — missed a
real error ("nach eine gute idee", "es ist so nicht") consistently even at `temperature: 0`, and the
grammar-check request wasn't pinning temperature at all, so the same input could get `OK` or a
(sometimes malformed) `CORRECTED:` reply on different runs. Fixed by pinning `temperature: 0` in
`check_grammar()`'s request, and switching the default `GRAMMAR_CHECK_MODEL` to `gemma2:9b`, which
caught the same error cleanly at `temperature: 0` — chosen over `llama3.1:8b` specifically to keep
the checker independent of the conversation model (see "both user and AI messages get checked" above:
`llama3.1:8b` grading its own replies would defeat that).

Update (2026-08-30): the corrector model is now user-selectable per session, same as the conversation
model — a "Grammar corrector model" dropdown on the Setup screen, stored in `session["grammar_model"]`
and set via a new `set_grammar_model` websocket action (mirrors `set_model`/`set_scenario`). Since
manually testing which models are/aren't reliable correctors was exactly how the temperature/model bug
above got found, and that kind of finding is otherwise just tribal knowledge someone has to remember
between sessions, `/api/models` now also returns a short `note` per model from a hardcoded
`MODEL_NOTES` dict in `voicechat2.py` (e.g. "weak as grammar corrector (tested)"), appended directly
into each `<option>`'s label in both dropdowns — visible while picking, not just on hover. Add an
entry to `MODEL_NOTES` whenever a model gets meaningfully tested for either role.

## Grammar-check steps

- [x] 13. `check_grammar()` helper + config (`voicechat2.py`)
      Add `GRAMMAR_CHECK_MODEL` env var (default `cas/discolm-mfto-german:latest`). Add
      `async def check_grammar(text: str) -> dict | None` that posts one non-streaming chat completion
      to `LLM_ENDPOINT` with a fixed system prompt (see above), and parses the reply into
      `{"correct": True, "corrected": None}` or `{"correct": False, "corrected": "..."}`. Returns `None`
      if the reply doesn't cleanly start with `OK` or `CORRECTED:`, or the request errors — the caller
      skips sending an update rather than show a wrong badge.
      Verify: pytest mocking the HTTP call for the OK / CORRECTED / unparseable-reply / request-error
      cases, asserting the right return value (including `None`) for each.
      Note: no async-test plugin exists in this project (`requirements-dev.txt` has plain `pytest`, no
      `pytest-asyncio`), so each new test drives the coroutine with a plain `asyncio.run(...)` inside a
      normal sync test function rather than adding a new dependency for one function. Reused the
      existing `_fake_client_session`/`_FailingSession` helpers already in `test_voicechat2.py`
      unchanged. 4 new pytest cases; `ruff check`, `ruff format --check`, `mypy`, `pytest -q`
      (43 passed) all green.

- [x] 14. Wire into the websocket pipeline (turn ids + background tasks)
      Include the session's current `turn_id` in the existing `{"type": "transcription", ...}` send.
      Right after that send, `asyncio.create_task` a `check_grammar` call for the user's text; in
      `generate_llm_response`, right after `add_ai_message`, do the same for `complete_text`. Each task
      sends `{"type": "grammar_check", "role": "user"|"assistant", "turn": ..., "correct": ...,
      "corrected": ...}` once its check resolves, or sends nothing if `check_grammar` returned `None`.
      Wrap each task's websocket send in a try/except so a client that's since disconnected doesn't
      raise unhandled inside a background task.
      Verify: pytest against a stubbed `check_grammar`, asserting the websocket receives a
      `grammar_check` message with the right `turn`/`role` for both a user turn and an AI turn, and
      that a `None` result sends nothing.
      Note: factored the shared "await check_grammar, then send the result" logic into a new
      `send_grammar_check(websocket, role, turn_id, text)` helper (called via `asyncio.create_task`
      from both call sites) rather than duplicating the parse-and-send/try-except block in two places.
      `process_and_stream` and `generate_llm_response` both gained a `turn_id` parameter to carry it
      from the websocket handler (where it's already computed, pre-existing, for the whisper temp-file
      name) through to the AI-turn task. `turn_id` is the pre-increment `current_turn` captured before
      `add_user_message` runs, so the user and AI grammar-check messages for one exchange share the
      same `turn` value — matches step 15's plan of keying `messageElements` by `` `${turn}-${role}` ``.
      4 new pytest cases in `test/test_voicechat2.py`: two unit tests calling `send_grammar_check`
      directly against a fake websocket (message-sent case, `None`-result-sends-nothing case); one
      through an actual `TestClient.websocket_connect("/ws")` round-trip (transcribe_audio/check_grammar/
      process_and_stream stubbed) asserting the live transcription message carries `turn` and a
      `grammar_check` with `role: "user"` arrives with the same turn; one calling `generate_llm_response`
      directly (aiohttp streaming response and check_grammar stubbed, background tasks drained via
      `asyncio.gather` after the call) asserting the `role: "assistant"` message. `ruff check`,
      `ruff format --check`, `mypy`, `pytest -q` (47 passed) all green.

- [x] 15. Front-end: badge/correction rendering (`ui/js/chat.js`, `ui/css/chat.css`)
      `displayMessage(role, content, turn)` stores `data-turn`/`data-role` on the element and keeps a
      `messageElements` map keyed by `` `${turn}-${role}` ``; the `transcription` handler passes
      `message.turn` and remembers it as `currentTurn` so the AI bubble `updateAIResponse` creates
      carries the same turn. A new `grammar_check` message handler looks up the element and appends a
      ✓ badge (if `correct`) or a muted "→ corrected: ..." line (if not) — skips silently if the
      element's gone.
      Verify: manual browser pass. There's no text-input path (mic only), so after a real push-to-talk
      exchange, send a fabricated `grammar_check` message through the open websocket from the devtools
      console for that turn's user and AI bubbles, confirm the badge/correction appears on the right
      one, styled consistently in light and dark theme.
      Note: `displayMessage`'s `role` param now matches the server's `grammar_check` vocabulary
      ("user"/"assistant") instead of the old display-cased "User"/"AI" strings, via `ROLE_LABEL`/
      `ROLE_CLASS` lookup maps — keeps the `${turn}-${role}` map key consistent with what
      `grammar_check` messages carry, without changing the rendered "User:"/"AI:" text or the existing
      `.user-message`/`.ai-message` CSS classes. Each bubble's text now lives in a child `.message-body`
      span (previously the `<p>`'s own `textContent`/`innerHTML`) so `applyGrammarCheck` can append a
      `.grammar-note` sibling without `updateAIResponse`'s per-token `innerHTML` rewrites wiping it —
      not actually reachable in practice since `grammar_check` for the assistant only fires after
      streaming/`processing_complete` (checked in `voicechat2.py`), but avoids relying on that timing.
      Re-sending a `grammar_check` for an already-annotated turn replaces the old note rather than
      stacking a second one (`existingNote.remove()` before appending). No JS test harness exists in
      this project (same as steps 7-11), so verification was the manual browser pass the step's Verify
      line asks for, via Chrome automation: rebuilt+redeployed `vc2`, ran the full Setup → Start flow
      (all four health checks green) and landed on `/chat.html`. Real spoken audio isn't exercisable
      through this automation (no real mic, per steps 9-10's note), so simulated a full turn by invoking
      the page's own `socket.onmessage` handler from the devtools console with fabricated
      `transcription`/`text`/`processing_complete`/`grammar_check` messages (closer to "through the open
      websocket" than the step's literal suggestion of `socket.send`, which would target the server, not
      the client's own message handler) — confirmed `messageElements` held both `"1-user"` and
      `"1-assistant"` keys, the user bubble showed "→ corrected: Ich habe ein Auto." in muted italic, and
      the AI bubble showed a green ✓, in both dark and light theme (toggled live) with no console errors.
      Also verified live: a second `grammar_check` for the same turn/role left exactly one `.grammar-note`
      element (replaced, not duplicated), and one for an unknown turn (999) was silently ignored, no
      throw. `ruff check`, `ruff format --check`, `mypy`, `pytest -q` (47 passed, unchanged — no Python
      touched this step) all still green. This closes out the grammar-check pass (3/3).

## Backlog

Small, independent follow-ups raised 2026-08-28 — no shared design decisions, no dependencies on
each other or on anything above. Pick either up whenever; each still needs its own up-front
design/scope decision before work starts, per the "Think before coding" working agreement.

- [x] 16. Apache-2.0 compliance check (this repo is a fork)
      Upstream is [lhl/voicechat2](https://github.com/lhl/voicechat2), Apache-2.0 licensed; `LICENSE`
      here still carries the upstream Apache-2.0 text verbatim (confirmed present, not yet re-checked
      for drift). Apache-2.0 §4 requires, for a modified redistribution: (a) give any other recipients a
      copy of the License — satisfied, `LICENSE` is in the repo; (b) carry prominent notices in modified
      files stating that they were changed — not currently done anywhere (CLAUDE.md/README link back to
      upstream, but no in-file "Modified from X" markers); (c) retain all copyright/patent/trademark/
      attribution notices from the Source form — no upstream `NOTICE` file was seen to check against;
      confirm whether upstream ships one; (d) if a NOTICE file is included, forward its contents. None of
      this has been actually checked against upstream's current state — this step is that check.
      Verify: no code changes expected — this is a compliance read. Conclude with either "no action
      needed" (with the §4(a)-(d) reasoning) or a concrete, scoped follow-up (e.g. adding a NOTICE file
      or file-level change markers).
      Note: checked against upstream's current `main` (`lhl/voicechat2`, fetched via `gh api`/raw.
      githubusercontent.com). (a) satisfied — `diff` against upstream's `LICENSE` is byte-identical, no
      drift. (c) and (d) are moot — grepped upstream's `voicechat2.py`/`srt-server.py`/`tts-server.py`/
      `README.md` for "copyright"/"©", no matches, and upstream ships no `NOTICE` file (not in its repo
      root listing), so there's nothing to retain or forward beyond `LICENSE` itself. (b) is **not**
      satisfied: the three files that are substantively rewritten from upstream equivalents —
      `voicechat2.py`, `srt-server.py`, and `tts-server-piper.py` (rebuilt from upstream's
      `test/piper-server.py`, per this file's "Diverged from upstream" section) — carry no in-file
      notice that they were changed; the CLAUDE.md/README explanation lives at the project level, not in
      the files themselves, which is what §4(b) asks for. (Upstream's five untouched `test/*.py`
      reference files and `README.md` also diff from upstream, but only via this repo's own `ruff
      format`/whitespace-strip tooling — cosmetic, not a substantive change — so they're out of scope
      for a change-notice.) Concrete follow-up scoped as step 18 below.

- [x] 17. Persist more of the user's setup choices across browser restarts
      Right now only the theme choice survives a browser restart (`ui/js/theme.js`, `localStorage`).
      Scenario id + model are `sessionStorage` (`ui/js/setup.js`, `ui/js/chat.js`) — deliberately, to
      carry state from Setup to Chat within one visit (see the "Navigation" decision at the top of the
      UI-rework section) — but that also means they're gone every time the tab/browser closes, so the
      user re-picks the same model and scenario every session. Decide what's actually worth carrying
      forward in `localStorage` instead/in addition (last-used model is the obvious one; maybe last
      custom scenario used, VAD-enabled toggle) and how it interacts with the existing sessionStorage
      handoff — this needs its own small design decision before it's a codeable step, not just "switch
      sessionStorage to localStorage" everywhere.
      Verify: TBD once scope is decided.
      Note: scoped to last-used scenario id + model only — mirrored into `localStorage` under the same
      `SCENARIO_STORAGE_KEY`/`MODEL_STORAGE_KEY` constants, written alongside the existing
      `sessionStorage` writes on Start. `sessionStorage` stays the Setup→Chat handoff, unchanged;
      `localStorage` is read as a fallback (`sessionStorage.getItem(key) ?? localStorage.getItem(key)`)
      only for pre-selecting Setup's own radio/dropdown on a fresh page load. Left the VAD toggle out of
      scope — it's the known-broken experimental path (see "Client-side recording" above), not worth
      persisting. Also fixed a pre-existing bug this surfaced: `loadModels()` unconditionally overwrote
      `selectedModel` with the server's bare default after every fetch, silently discarding whatever
      had been restored (from either storage) — scenario restoration already guarded against this via
      `loadScenarios()`'s existing `!scenarios.some(...)` check, model restoration had no equivalent.
      Now mirrors that pattern: keeps the restored model if it's still in the fetched list, falls back
      to the server default otherwise. Verified manually via Chrome automation against the rebuilt `vc2`
      container: cleared both storages and confirmed defaults load; picked a non-default scenario
      (`restaurant`) + model (`mistral:7b`) and hit Start, confirmed both storages held the new values on
      `chat.html`; cleared only `sessionStorage` (simulating a restart) and reloaded Setup, confirmed
      both pre-selected from `localStorage` alone; set `localStorage`'s model to a nonexistent name and
      reloaded, confirmed a clean fallback to the server default with the Start button still enabled, no
      broken dropdown state. `ruff check`, `ruff format --check`, `mypy`, `pytest -q` (47 passed,
      unchanged — no Python touched) all still green (no JS test harness exists, same as prior UI
      steps). This closes out the last item in Backlog (3/3).

- [x] 18. Add Apache-2.0 §4(b) change notices to the three rewritten files
      Follow-up from step 16: `voicechat2.py`, `srt-server.py`, and `tts-server-piper.py` are
      substantively rewritten from their upstream equivalents but carry no in-file notice that they
      were changed, which Apache-2.0 §4(b) requires for a modified redistribution. Add a short comment
      near the top of each of the three files stating it's modified from the corresponding upstream
      `lhl/voicechat2` file (name the upstream file, since `tts-server-piper.py` maps to upstream's
      `test/piper-server.py`, not `tts-server.py`) — one or two lines, not a changelog; the detailed
      "what changed and why" already lives in this file's "Diverged from upstream" section, no need to
      duplicate it inline.
      Verify: `ruff check`, `ruff format --check`, `mypy`, `pytest -q` all still pass (comment-only
      change); manually confirm each notice names the correct upstream source file it diverged from.
      Note: added a two-line `# Modified from upstream lhl/voicechat2's <file> — see this repo's
      CLAUDE.md "Diverged from upstream" section for what changed and why.` comment above the imports
      in `voicechat2.py` and `srt-server.py`, and the same above `tts-server-piper.py`'s imports (naming
      `test/piper-server.py`, since that's the file it was rebuilt from, not upstream's own
      `tts-server.py`) — kept separate from that file's existing rationale comment rather than merging
      the two, since one is the license-required "this changed" notice and the other is a why. `ruff
      check`, `ruff format --check`, `mypy`, `pytest -q` (47 passed) all green. This closes out the
      Apache-2.0 compliance follow-up (backlog 2/3).

## Explain-on-demand (2026-08-30)

A third Setup-screen picker, "Explainer model", plus an "Explain" button on every chat bubble (both
user and AI turns) that POSTs the turn's text to a new `POST /api/explain` endpoint and shows the
English translation + brief notes inline, replacing the button. Unlike grammar-check this is
user-triggered, not automatic, and unlike the corrector model it needed no session/websocket state at
all — the UI just includes its chosen `explainerModelId` (from sessionStorage, same pattern as the
other two pickers) directly in each request body.

`explain_text()` asks for a strict "Translation: .../Notes: ..." two-line reply (`EXPLAIN_SYSTEM_PROMPT`),
but testing found `llama3.1:8b` and `gemma2:9b` both ignore that format and just reply with prose (still
useful content, just unlabeled), while `mistral:7b` and `qwen2.5-coder:14b` follow it cleanly — recorded
in `MODEL_NOTES`. Rather than failing on the ignored-format case, `explain_text()` falls back to
treating the first line as the translation and the rest as notes (stripping a stray `Notes:` prefix and
normalizing a bare "none" to no notes) — a still-useful reply beats a hard error for an on-demand,
user-initiated feature like this. `/api/explain` returns 502 only when the model truly gave nothing
back (empty reply) or the request itself failed.
`ruff check`, `ruff format --check`, `mypy`, `pytest -q` (59 passed) all green; manually verified via
Chrome automation against the rebuilt `vc2` container — both a simulated user bubble and a simulated
completed AI bubble showed a working Explain button that fetched and rendered a translation + notes.

## Typed-message alternative to push-to-talk (2026-08-30)

A text input + "Send" button on `chat.html` (Enter also submits) sends a new `send_text` websocket
action instead of recording audio. Server side, `stop_recording` (voice) and `send_text` (typed) now
share a `run_user_turn(websocket, session_id, get_text)` helper — extracted from what used to be
`stop_recording`'s inline body — that handles the interrupt-in-progress case and then, once
`get_text(turn_id)` resolves (transcription or the typed string), runs add_user_message /
grammar-check / LLM / TTS identically either way. The server echoes the typed text back as the same
`"transcription"` message type voice replies already use, so chat.js's existing handler creates the
chat bubble with zero new client-side message-type handling.

Two bugs found and fixed along the way, both blocking, not just polish:
- The global spacebar push-to-talk shortcut (`keydown`/`keyup` on `document`) was swallowing every
  space typed into the new input and toggling recording instead — fixed by skipping both handlers
  when `event.target === typeInput`.
- `window.onload` awaited `initializeRecorder()` with no try/catch, so on a mic failure (denied
  permission, no device — hit directly while testing this in a Chrome automation profile) the
  unhandled rejection skipped `initializeWebSocketAsync()` entirely, meaning typed input silently
  couldn't work either even though it needs no microphone at all. Fixed by wrapping the recorder
  init in its own try/catch that disables `recordButton` (with a `title` explaining why) and sets a
  new `micAvailable` flag, checked in `socket.onopen` instead of unconditionally re-enabling the
  record button.

`ruff check`, `ruff format --check`, `mypy`, `pytest -q` (61 passed, +2 for `send_text`: happy path
and blank-text-sends-error) all green. Manually verified via Chrome automation against the rebuilt
`vc2` container: with the mic unavailable, confirmed the websocket now still connects and the typed
path still fully works (both Enter and the Send button), through the actual UI, not just a console
bypass — round-tripped a typed German message through transcription echo, grammar-check, and a full
LLM reply with its own grammar-check and Explain button.
