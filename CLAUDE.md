# voicechat2 (fork)

Real-time browser voice chat — fork of [lhl/voicechat2](https://github.com/lhl/voicechat2),
adapted to run against this machine's existing local AI stack instead of
upstream's mamba/byobu/llama.cpp setup. Used for spoken German conversation
practice; see `~/claude/german/voice-setup.md` for that context (why this
exists, how it compares to the other practice tools) and the full list of
fixes made here.

## What this file is not

- Rationale for each fix vs upstream, latency numbers → `~/claude/german/voice-setup.md`
- Upstream's own architecture writeup → `README.md` (kept from upstream; several
  of its defaults no longer apply here, see "Diverged from upstream" below)

## Architecture

Three FastAPI servers + a static browser UI (`ui/index.html`), wired by env
vars (see `run.sh`), orchestrated by `run.sh`/`stop.sh`:

| Server | Port | Role |
|---|---|---|
| `voicechat2.py` | 8010 | WebSocket orchestrator, serves `ui/`. Streams LLM output into TTS sentence-by-sentence for lower perceived latency. |
| `srt-server.py` | 8001 | STT. Default engine `WhisperWebserviceEngine` bridges to the already-running Whisper container at `localhost:9001` — no model loaded in-process. |
| `tts-server-piper.py` | 8003 | TTS. Shells out to the Piper binary + German voice under `~/data/piper`, same as `~/bin/deutsch`. |

LLM is Ollama's OpenAI-compatible endpoint (`localhost:11434/v1/chat/completions`,
model `llama3.1:8b` by default) — not llama.cpp.

Port 8000 (upstream's default for the orchestrator) is already held by
something else on this machine, so this fork runs on **8010**.

## Running

```
./run.sh          # starts all three servers, then open http://localhost:8010
./stop.sh
tail -f .run/*.log
```

Runs from its own `.venv` (Python 3.12, not the system default — 3.14 broke
`onnxruntime`/`piper-tts` wheel availability when this was set up, though
moot now since neither package is a dependency of the active path anymore).
Recreate it with `requirements-lean.txt`, not the upstream `requirements.txt`
(see that file's header for why).

## Diverged from upstream

Upstream assumes a CUDA/NVIDIA GPU, a locally-built llama.cpp + GGUF model,
HF Transformers Whisper, mamba + byobu, and the Python `piper` package. None
of that applies on this machine (AMD GPU, Ollama already running, Whisper
already running in Docker). Beyond re-pointing, three real bugs got fixed
along the way, not just config: the LLM endpoint was hardcoded to a
nonexistent `"gpt-3.5-turbo"` model name (every request would have failed
against Ollama); the user's latest turn was being sent to the LLM twice per
request; the reference Piper TTS server (`test/piper-server.py`) stripped
all non-ASCII text, which would have silently deleted every German
umlaut/ß. Full diff-level detail in `~/claude/german/voice-setup.md`.

## Gotchas

- `srt-server.py` and `voicechat2.py` had several **dead top-level imports**
  (`torch`, `transformers`, `soundfile`, `tempfile`, `wave`, `numpy`,
  `mutagen.oggopus.OggOpus`) that forced installing packages nothing in this
  fork's active path actually uses. If you add a real use for one of them,
  reintroduce the import at the point of use, not back at module level —
  that's what made this easy to miss originally.
- The other engines in `srt-server.py` (`TransformersEngine`,
  `FasterWhisperEngine`) and the original `tts-server.py` (Coqui) are left
  intact and still selectable via `SRT_ENGINE`/pointing `TTS_ENDPOINT`
  elsewhere, but are untested on this machine's AMD GPU — treat them as
  upstream reference code, not verified paths.
