# Frictions

Tooling friction hit during work, newest last. One line each, past tense: what was tried, what
went wrong, how it got worked around. This is for the *tooling* — a guessed command that
failed, a flag that needed different quoting, a check that confidently returned the wrong
answer — not for problems in the code itself; those go in `docs/revisit.md`.

The same friction three times means fix it (a permission entry, a `CLAUDE.md` line, a script),
not a fourth line here.

- 2026-08-28 — Chrome browser automation (`mcp__claude-in-chrome__*`) wasn't connected this
  session (`tabs_context_mcp` returned "Browser extension is not connected"), so a UI step's
  `Verify:` line that needs a live browser (OS-theme toggle, console-error check) couldn't run
  as written. Fell back to server-side verification instead (curl for HTTP status/content-type,
  a brace-balance check on the CSS) and left a `Note:` on the checklist step saying the visual
  check is still owed. Check `tabs_context_mcp` early in a UI-heavy session before assuming the
  browser tools are available.
- 2026-11-15: `claude -p --tools "" "<prompt>"` failed ("Input must be provided…") — `--tools` swallowed the positional prompt; switched to stdin (now a CLAUDE.md Gotcha).
- 2026-11-15: `curl -G -X POST … -F file=@…` to the whisper `/asr` endpoint returned a non-JSON body — `-G` and `-F` don't combine; put the query string in the URL instead.
- 2026-11-15: Claude-in-Chrome extension disconnected twice mid-session; UI verification had to wait until it reconnected.
- 2026-11-15: Chrome served cached `ui/` files after a container rebuild, twice (Setup "active:" line still showing; Setup stuck at "loading…") — needed a hard reload (now a CLAUDE.md Gotcha).
