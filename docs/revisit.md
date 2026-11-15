# Revisit

Real problems found outside the scope of whatever task was running — not fixed inline (that
would make the diff unreviewable) and not just mentioned in chat, since chat isn't persistence.
Numbered `## N. Title` entries, deleted once actually fixed rather than left to accumulate.
Give each one enough evidence (`file:line`, or a short repro) that a cold session can act on it
without re-deriving anything.

## 1. Voice transcripts: no capitalization/punctuation, app-vocab misheard

From a real session transcript (2026-11-15): every spoken user turn came back all-lowercase with
no punctuation (typed turns were fine). Mishearings in the same session: "Triple Sum" → "tripos
am", "Apps" → "abs", "alle" → "alt"; downstream tools then explained/corrected the misheard words
as if real.

**Not reproducible with synthetic audio.** Piper-generated German clips (both punctuated and a
long unpunctuated run-on) sent to the whisper container (`large-v3-turbo`, `faster_whisper`,
`language=de`) all came back properly capitalized and punctuated, so it's something about the
real recordings (accent, hesitations, mic, browser webm/opus). Recordings are deleted after each
turn (`voicechat2.py` `transcribe_audio`, `os.remove(temp_file_path)`), so none were available to
replay. Next step needs one real recording: e.g. temporarily skip that `os.remove`, record a turn,
and POST the `/tmp/<session>-<turn>.opus` file to `localhost:9001/asr` directly.

**Don't use `initial_prompt`** as the fix — tested 2026-11-15: with a punctuated German prompt,
Whisper dropped the clip's first sentence ("Mir geht es gut, danke.") because it overlapped the
prompt's wording, and on pure silence it output the prompt text itself ("Ich übe Deutsch.").

## 2. Corrector output sometimes comes back all-lowercase

Seen 2026-11-15 after the corrector prompt change (`ff1183b`, "Ignore capitalization and
punctuation completely"): for the AI turn "Klar, gern! Was interessiert dich denn besonders an der
Geschichte? HISTORICAL FICTION MAG SCHLAGWORTE ?" `gemma2:9b` returned `CORRECTED: klar gern was
interessiert dich denn besonders an der geschichte historisches fiction mag schlagworte` — it
flagged the garbage correctly but stripped all capitals/punctuation from the shown correction.
Likely fix: tell it to ignore capitalization/punctuation when *deciding*, but write the corrected
sentence with proper German capitalization and punctuation. Re-run the scratch bench sentences
after changing it.
