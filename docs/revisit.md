# Revisit

Real problems found outside the scope of whatever task was running — not fixed inline (that
would make the diff unreviewable) and not just mentioned in chat, since chat isn't persistence.
Numbered `## N. Title` entries, deleted once actually fixed rather than left to accumulate.
Give each one enough evidence (`file:line`, or a short repro) that a cold session can act on it
without re-deriving anything.

## 1. Near-instant push-to-talk (non-zero but audio-less capture) 500s instead of hitting the empty-text path

`srt-server.py:216`'s `if not audio_content:` short-circuit only catches a truly 0-byte body. A
push-to-talk click fast enough that `MediaRecorder` only ever fires one `ondataavailable` with
just container-header bytes (repro: 110 bytes / 0.1s, observed while manually verifying UI
rework step 9 on `ui/chat.html`) is non-empty, so it falls through to
`WhisperWebserviceEngine.transcribe` (`srt-server.py:144-153`). The whisper webservice apparently
returns a non-JSON (`text/plain; charset=utf-8`) body for that input, and `response.json()` at
`srt-server.py:151` raises, surfacing to the browser as a raw 500: `Attempt to decode JSON with
unexpected mimetype: text/plain; charset=utf-8, url='http://localhost:8001/inference'` — the same
class of opaque-crash bug the CLAUDE.md "Diverged from upstream" 0-byte fix addressed, just for a
slightly-non-empty payload instead of an exactly-empty one. Pre-existing on this code path
(unrelated to the UI rework); not a regression from step 9. Likely fix: treat a whisper-webservice
non-JSON/error response the same way as the empty-audio case (empty transcription) rather than
raising, or lower-bound `audio_content` length instead of just checking non-empty.
