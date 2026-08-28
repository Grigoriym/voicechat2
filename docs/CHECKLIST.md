# CHECKLIST

Progress: 2/12 — Current step: none

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

- [ ] 3. Health endpoints on the STT and TTS servers
      `srt-server.py` `GET /health`: probes the configured engine's backing service (for
      `WhisperWebserviceEngine`, a short request to `WHISPER_WEBSERVICE_URL`'s base) and returns
      `{"status": "ok"|"error", "detail": ...}`. `tts-server-piper.py` `GET /health`: checks
      `PIPER_BIN` and `PIPER_MODEL` exist on disk.
      Verify: curl each with the backend up; then stop the whisper container / rename the piper
      binary and confirm each reports unhealthy instead of hanging or 500ing.

- [ ] 4. Aggregate health endpoint on the orchestrator
      `voicechat2.py` `GET /api/health`: calls Ollama's `/api/tags`, srt-server's `/health`, and
      tts-server's `/health` concurrently, returns per-service ok/error.
      Verify: pytest against a stubbed stack; curl it locally with the full `docker compose`
      stack up.

- [ ] 5. Custom-scenario storage
      Load/save custom scenarios to a JSON file (gitignored) merged with the hardcoded
      `SCENARIOS` at request time. A custom scenario can't reuse a built-in id.
      Verify: pytest covering create/list/delete round-trip against a temp file.

- [ ] 6. Scenario CRUD endpoints
      `POST /api/scenarios` (create), `PUT /api/scenarios/{id}` (edit, custom only),
      `DELETE /api/scenarios/{id}` (delete, custom only). `GET /api/scenarios` now also returns
      the full prompt text and an `is_builtin` flag per scenario.
      Verify: pytest + curl round-trip; editing/deleting a built-in id returns 4xx.

- [ ] 7. Setup screen markup + layout (`ui/index.html` rewritten)
      Scenario list (built-ins badged), "New scenario" and "Clone & edit" flows (name + prompt
      textarea), model dropdown, health-check panel, "Start conversation" button (disabled until
      a scenario and model are chosen).
      Verify: manual browser pass — create a scenario, edit a clone, watch the health panel
      react to a killed backend, confirm the Start button gates correctly.

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
