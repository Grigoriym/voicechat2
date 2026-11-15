# Revisit

Real problems found outside the scope of whatever task was running — not fixed inline (that
would make the diff unreviewable) and not just mentioned in chat, since chat isn't persistence.
Numbered `## N. Title` entries, deleted once actually fixed rather than left to accumulate.
Give each one enough evidence (`file:line`, or a short repro) that a cold session can act on it
without re-deriving anything.

## 1. Voice transcripts: no capitalization/punctuation, app-vocab misheard

Same transcript. Every spoken user turn came back all-lowercase with no punctuation (typed turns
were fine), from the whisper container (`large-v3-turbo`, `faster_whisper`, `language=de` via
`srt-server.py` `WhisperWebserviceEngine`). large-v3-turbo normally punctuates, so worth checking
the raw `/asr` response for one clip to see whether it's the model, a webservice param, or a
post-processing step. Mishearings in the same session: "Triple Sum" → "tripos am", "Apps" → "abs",
"alle" → "alt"; downstream tools then explained/corrected the misheard words as if real. A Whisper
`initial_prompt` with a few English loanwords might help, if the webservice exposes it.
