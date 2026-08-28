# CHECKLIST

Progress: 7/12 — Current step: none

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

- [ ] 8. Setup screen wiring (`ui/js/setup.js`)
      Fetch/populate scenarios and models, handle create/clone/edit/delete, run health checks
      (including a `getUserMedia` mic-permission probe), store the chosen scenario id + model in
      `sessionStorage`, navigate to `chat.html` on Start.
      Verify: same manual pass as step 7, plus confirm the `sessionStorage` keys are set
      correctly (devtools).

- [ ] 9. Conversation screen (`ui/chat.html`), extracted from the current `index.html`
      Move the recording/transcript/status UI here, trimmed of the model and scenario dropdowns
      (chosen on Setup now). Read scenario + model from `sessionStorage` on load and send
      `set_scenario`/`set_model` on socket open.
      Verify: manual pass — talk through a full turn, confirm the chosen scenario's prompt is
      actually active (check server logs).

- [ ] 10. Collapsible debug/metrics panel
      Move the latency-metrics table into a `<details>` section on `chat.html`, closed by
      default.
      Verify: manual — panel starts closed, opens on click, still updates with real numbers
      during a turn.

- [ ] 11. Retire the old single-page markup/JS
      Delete what's now dead from the pre-rework `index.html`/inline script; keep whatever's
      still used (audio recording, websocket handling) factored into `ui/js/chat.js`.
      Verify: `ruff check . && ruff format . && mypy . && pytest -q` still pass; grep confirms
      nothing still references a removed id or function.

- [ ] 12. Docker/static-serving check
      Confirm the `/` route and the `/ui` `StaticFiles` mount in `voicechat2.py` still serve the
      right entry file — `index.html` is the Setup screen now, not the conversation screen.
      Verify: `docker compose up -d --build`, browser hit on `localhost:8010`, confirm the Setup
      screen loads first and Start correctly reaches the conversation screen.
