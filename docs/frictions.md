# Frictions

Tooling friction hit during work, newest last. One line each, past tense: what was tried, what
went wrong, how it got worked around. This is for the *tooling* — a guessed command that
failed, a flag that needed different quoting, a check that confidently returned the wrong
answer — not for problems in the code itself; those go in `docs/revisit.md`.

The same friction three times means fix it (a permission entry, a `CLAUDE.md` line, a script),
not a fourth line here.

- 2026-11-15: `claude -p --tools "" "<prompt>"` failed ("Input must be provided…") — `--tools` swallowed the positional prompt; switched to stdin (now a CLAUDE.md Gotcha).
- 2026-11-15: `curl -G -X POST … -F file=@…` to the whisper `/asr` endpoint returned a non-JSON body — `-G` and `-F` don't combine; put the query string in the URL instead.
- 2026-11-15: Chrome served cached `ui/` files after a container rebuild, twice (Setup "active:" line still showing; Setup stuck at "loading…") — needed a hard reload (now a CLAUDE.md Gotcha).
