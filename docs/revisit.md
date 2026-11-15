# Revisit

Real problems found outside the scope of whatever task was running — not fixed inline (that
would make the diff unreviewable) and not just mentioned in chat, since chat isn't persistence.
Numbered `## N. Title` entries, deleted once actually fixed rather than left to accumulate.
Give each one enough evidence (`file:line`, or a short repro) that a cold session can act on it
without re-deriving anything.

## 1. `explain_text` fallback leaks "Notes: None" into the result

`voicechat2.py` `explain_text`, fallback branch. When the model (here `llama3.1:8b`) replies with
an unlabeled translation spanning two lines plus a trailing `Notes: None`, the second translation
line becomes the start of `notes` and the `Notes: None` stays in it — the `startswith("Notes:")`
strip only handles a leading prefix. Seen on: "Ach ja, Triple Sum! ... Warum magst du solche
Aufgaben denn nicht?" → Explain showed "Why don't you like these kinds of tasks? Notes: None".
Also: `llama3.1:8b` notes hallucinate ("'Veirweise' is a misspelling of 'vielleicht'" — the user
wrote "veielleicht") and mistranslated a yes/no question ("Hast du davon eine besonders gerne
benutzt?" → "Which one do you like to use the most?"). `mistral:7b`/`qwen2.5-coder:14b` are noted
in `MODEL_NOTES` as following the format cleanly — worth comparing as the explainer default.

## 2. Voice transcripts: no capitalization/punctuation, app-vocab misheard

Same transcript. Every spoken user turn came back all-lowercase with no punctuation (typed turns
were fine), from the whisper container (`large-v3-turbo`, `faster_whisper`, `language=de` via
`srt-server.py` `WhisperWebserviceEngine`). large-v3-turbo normally punctuates, so worth checking
the raw `/asr` response for one clip to see whether it's the model, a webservice param, or a
post-processing step. Mishearings in the same session: "Triple Sum" → "tripos am", "Apps" → "abs",
"alle" → "alt"; downstream tools then explained/corrected the misheard words as if real. A Whisper
`initial_prompt` with a few English loanwords might help, if the webservice exposes it.
