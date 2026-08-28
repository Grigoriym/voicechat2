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
model `llama3.1:8b` by default) — not llama.cpp. The model is **per-session, not
fixed at startup**: `GET /api/models` proxies Ollama's `/api/tags` for the UI's
dropdown, and a `{"action": "set_model", "model": "..."}` websocket message
updates `conversation_manager.sessions[id]["model"]`, read fresh by
`generate_llm_response` on every turn. `LLM_MODEL` is only the default.

Port 8000 (upstream's default for the orchestrator) is already held by
something else on this machine, so this fork runs on **8010**.

Ollama keeps a model resident in VRAM for `OLLAMA_KEEP_ALIVE` (default 5m)
after its last request — ~5.5GB for `mistral:7b` on this machine, confirmed
via `rocm-smi`. The "GPU-Speicher freigeben" button in the UI (next to the
model dropdown) calls `POST /api/unload-model`, which asks Ollama's
`/api/ps` what's loaded and unloads each one immediately via
`POST /api/generate` with `keep_alive: 0` — the same mechanism the `ollama
stop <model>` CLI command uses, confirmed by watching VRAM usage drop
immediately after calling it.

## Running

Two ways to run the stack — don't run both at once, they'll fight over
ports 8001/8003/8010.

**Docker (always-on, the normal way now):**

```
docker compose up -d --build   # builds + starts all three, survives reboot
docker compose ps
docker compose logs -f vc2     # or srt / tts
docker compose down            # stop (removes containers; add -v for volumes too)
```

One `Dockerfile`, three services in `docker-compose.yml` (`srt`, `tts`, `vc2`)
sharing that image, `command:` picking which uvicorn target each runs.
`restart: unless-stopped` + `docker.service` already enabled at the systemd
level means the stack comes back on its own after a reboot, as long as it
was up (not manually `docker compose down`'d) when the system went down —
confirmed by killing `vc2`'s process inside its container and watching Docker
restart it within seconds. All three run with `network_mode: host` rather
than Docker's default bridge network, because Ollama only listens on
`127.0.0.1:11434` (confirmed via `ss -tlnp`) — a bridge-networked container
can't reach that even with `host.docker.internal`, since a loopback bind
only accepts connections from the same network namespace. Host networking
sidesteps this entirely and keeps every `localhost:PORT` env var identical
to the non-Docker setup below; it's Linux-only, which is fine here. The
`tts` service bind-mounts `~/data/piper` read-only into the container
(`/piper`) rather than copying it into the image — Piper is a self-contained
native binary (confirmed via `ldd`: only its own bundled `.so`s plus
standard glibc/libstdc++), so mounting avoids duplicating the model file.

**Manual (`.venv`, for local dev/debugging):**

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

## Development

```
pip install -r requirements-dev.txt   # ruff, mypy, pytest, pre-commit
pre-commit install                    # one-time, wires the git hook
ruff check . && ruff format .         # lint + format
mypy .                                # type check
pytest -q                             # unit tests, test/test_*.py
```

`pre-commit run --all-files` runs the full set (ruff, mypy, plus generic
whitespace/EOF/merge-conflict checks) without committing. The same set runs
in CI on push/PR (`.github/workflows/ci.yml`).

The five untouched upstream reference files under `test/` (`voicechat2-monolithic.py`,
`voicechat2-webrtc.py`, `styletts2-server.py`, `melo-server.py`, `piper-server.py`)
are excluded from mypy and from ruff's rule checks (still auto-formatted) — see
`pyproject.toml`. If one of them gets adopted into the active path, drop it
from both exclude lists there and in `.pre-commit-config.yaml`'s mypy hook.

Tests import the hyphenated active-path scripts (`voicechat2.py`, `srt-server.py`,
`tts-server-piper.py`) directly via `importlib` — see `test/conftest.py`'s
fixtures — since they're flat scripts, not a package.

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
umlaut/ß; and 0-byte/silent audio (near-instant push-to-talk, VAD misfire)
crashed `/inference` with an opaque `aiohttp.ContentTypeError` instead of
hitting the existing "empty text" error path — fixed by short-circuiting
on empty `audio_content` before it ever reaches the whisper webservice.
Full diff-level detail in `~/claude/german/voice-setup.md`.

## Client-side recording: MediaRecorder, not the bundled opus-encdec library

The push-to-talk/VAD capture in `ui/index.html` used to go through the CDN
`symbl-opus-encdec` library (still loaded, still used by the VAD path). That
library loads its actual Opus encoder from a hardcoded third-party URL
(a Symbl.ai storage bucket) — now returns 403, and even a live mirror
(jsdelivr) wasn't enough to fix it, so something else in that
2-year-unmaintained chain is also broken. Symptom: `recorder.start()`/
`.stop()` resolved normally and the UI looked fine, but `onstart`/
`ondataavailable`/`onstop` never fired — every recording was silently
0 bytes, with zero console errors, reproduced identically on two different
machines. Debugging that took a full session; see
`~/claude/german/voice-setup.md` for the trace if it recurs.

The push-to-talk path now uses the browser's native `MediaRecorder`
(`audio/webm;codecs=opus`) instead — no external script, no worker, nothing
to rot. **VAD mode still uses the old broken path and is not fixed** (it's
labeled experimental in the UI on purpose); if VAD needs to work, give it
the same MediaRecorder treatment rather than debugging the old library
further.

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
- Removed as dead/unreferenced when the linting/testing setup surfaced them:
  `ui/index-webrtc.py` (actually HTML despite the `.py` extension, nothing
  linked to it) and `voicechat2.py`'s `process_llm_content`/`process_sentence`
  (never called from anywhere in that file — `test/voicechat2-webrtc.py` has
  its own separate copies, unaffected). If you're looking for either, check
  git history.
