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
