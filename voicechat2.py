# Modified from upstream lhl/voicechat2's voicechat2.py — see this repo's
# CLAUDE.md "Diverged from upstream" section for what changed and why.

import asyncio
import json
import logging
import os
import re
import time
import traceback
import uuid
from collections import deque
from urllib.parse import urlsplit

import aiohttp
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# External endpoints
SRT_ENDPOINT = os.getenv("SRT_ENDPOINT", "http://localhost:8001/inference")
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://localhost:11434/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.1:8b")
OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://localhost:11434")
TTS_ENDPOINT = os.getenv("TTS_ENDPOINT", "http://localhost:8003/tts")

# Grammar-check pass: a separate model (independent of whichever model is
# driving the conversation, though also user-selectable per session) used to
# silently score each turn's German and offer a correction. See
# docs/CHECKLIST.md "Grammar-check pass".
GRAMMAR_CHECK_MODEL = os.getenv("GRAMMAR_CHECK_MODEL", "gemma2:9b")

# Explain-on-demand: user clicks "Explain" on a chat bubble to get an English
# translation + brief notes for that turn, via a separate, user-selectable
# model (unlike grammar-check, this isn't fired automatically — no session
# state needed, the UI just sends its chosen model with each request).
EXPLAINER_MODEL = os.getenv("EXPLAINER_MODEL", "qwen2.5-coder:14b")

# Check-on-demand: the one feature that leaves the machine. Runs Claude via
# the `claude -p` CLI on the user's own subscription (not the API), because
# every local model benchmarked either invented mistakes in correct German
# or explained real ones wrongly — see docs/CHECKLIST.md "Check-on-demand".
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")
CLAUDE_TIMEOUT_S = 90

# Claude can also serve every other role (Setup's Local/Claude switch).
# Claude models travel through the same model strings as Ollama ones, as
# "claude:<alias>" — set_model/set_grammar_model/explain need no new fields.
CLAUDE_PREFIX = "claude:"
CLAUDE_MODELS: dict[str, str] = {
    "claude:haiku": "fastest; reply starts in ~1s",
    "claude:sonnet": "Check default; 9/9 on the Check benchmark; reply starts in ~1.5s",
    "claude:opus": "most capable, slowest, uses the most of your plan",
}
CLAUDE_DEFAULTS = {
    "default": "claude:sonnet",
    "grammar_default": "claude:sonnet",
    "explainer_default": "claude:sonnet",
}
CHECK_MODEL = os.getenv("CHECK_MODEL", "claude:sonnet")

# Free-text notes shown next to each model in the Setup screen's dropdowns —
# past testing findings (e.g. which models are unreliable as a grammar
# corrector) so they aren't lost tribal knowledge. Keyed by exact Ollama
# model name; a model with no entry here just shows unadorned in the UI.
MODEL_NOTES: dict[str, str] = {
    "llama3.1:8b": (
        "conversation default; weak as grammar corrector; weak explainer, can drop "
        "half a long translation (all tested)"
    ),
    "gemma2:9b": (
        "grammar-check default; catches real errors reliably; explainer notes can "
        "invent words the speaker never said (all tested)"
    ),
    "mistral:7b": (
        "weak as grammar corrector; explainer translates fully but notes can be made up (tested)"
    ),
    "qwen2.5-coder:14b": (
        "explainer default; cleanest format, most complete and accurate translations (tested)"
    ),
    "cas/discolm-mfto-german:latest": (
        "German-tuned, but weak as grammar corrector even at temperature 0 (tested)"
    ),
}

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI()
app.mount("/ui", StaticFiles(directory="ui"), name="ui")

BASE_SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "This is a live spoken German conversation practice session, transcribed by "
    "Whisper and read aloud by Piper TTS. Reply in plain conversational German "
    "prose only: no markdown, no asterisks, no bullet points, no headers, no "
    "emoji. Keep replies natural and conversationally short unless more detail "
    "is asked for. Correct mistakes briefly and conversationally, in German, "
    "then keep the conversation going.",
)

# Scenario picker: lets the UI prime the model with a situational role-play on
# top of BASE_SYSTEM_PROMPT, instead of every session starting from a blank
# "free talk" context. "general" keeps the old behavior (no addition).
DEFAULT_SCENARIO = "general"
SCENARIOS = {
    "general": {
        "label": "General / free talk",
        "prompt": "",
    },
    "ai_leads": {
        "label": "AI opens & drives",
        "prompt": (
            "Scenario: start the conversation yourself with a natural opening "
            "line — don't wait for the user to speak first. Keep it moving "
            "throughout: ask follow-up questions, and whenever a topic runs "
            "dry, introduce a new related one so the user always has "
            "something easy to respond to."
        ),
    },
}


# Custom scenarios created via the UI (step 6), stored as a gitignored JSON
# file of {id: {"label": ..., "prompt": ...}} merged with the hardcoded
# SCENARIOS above at request time. A custom scenario can't reuse a built-in id.
CUSTOM_SCENARIOS_PATH = os.getenv("CUSTOM_SCENARIOS_PATH", "custom_scenarios.json")


def load_custom_scenarios(path: str = CUSTOM_SCENARIOS_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save_custom_scenarios(scenarios: dict, path: str = CUSTOM_SCENARIOS_PATH) -> None:
    with open(path, "w") as f:
        json.dump(scenarios, f, indent=2)


def create_custom_scenario(
    scenario_id: str, label: str, prompt: str, path: str = CUSTOM_SCENARIOS_PATH
) -> dict:
    if scenario_id in SCENARIOS:
        raise ValueError(f"'{scenario_id}' is a built-in scenario id")
    custom = load_custom_scenarios(path)
    if scenario_id in custom:
        raise ValueError(f"custom scenario '{scenario_id}' already exists")
    custom[scenario_id] = {"label": label, "prompt": prompt}
    save_custom_scenarios(custom, path)
    return custom[scenario_id]


def update_custom_scenario(
    scenario_id: str, label: str, prompt: str, path: str = CUSTOM_SCENARIOS_PATH
) -> dict:
    if scenario_id in SCENARIOS:
        raise ValueError(f"'{scenario_id}' is a built-in scenario and can't be edited")
    custom = load_custom_scenarios(path)
    if scenario_id not in custom:
        raise ValueError(f"custom scenario '{scenario_id}' not found")
    custom[scenario_id] = {"label": label, "prompt": prompt}
    save_custom_scenarios(custom, path)
    return custom[scenario_id]


def delete_custom_scenario(scenario_id: str, path: str = CUSTOM_SCENARIOS_PATH) -> None:
    if scenario_id in SCENARIOS:
        raise ValueError(f"'{scenario_id}' is a built-in scenario and can't be deleted")
    custom = load_custom_scenarios(path)
    if scenario_id not in custom:
        raise ValueError(f"custom scenario '{scenario_id}' not found")
    del custom[scenario_id]
    save_custom_scenarios(custom, path)


def get_all_scenarios(path: str = CUSTOM_SCENARIOS_PATH) -> dict:
    merged = dict(SCENARIOS)
    merged.update(load_custom_scenarios(path))
    return merged


def build_system_message(scenario: str) -> dict:
    all_scenarios = get_all_scenarios(CUSTOM_SCENARIOS_PATH)
    scenario_prompt = all_scenarios.get(scenario, all_scenarios[DEFAULT_SCENARIO])["prompt"]
    content = BASE_SYSTEM_PROMPT
    if scenario_prompt:
        content = f"{BASE_SYSTEM_PROMPT}\n\n{scenario_prompt}"
    return {"role": "system", "content": content}


class ConversationManager:
    def __init__(self):
        self.sessions = {}
        self.session_timeout = 3600  # 1 hour timeout for sessions

    def create_session(self):
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = {
            "conversation": [build_system_message(DEFAULT_SCENARIO)],
            "scenario": DEFAULT_SCENARIO,
            "model": LLM_MODEL,
            "grammar_model": GRAMMAR_CHECK_MODEL,
            "llm_output_sentences": deque(),
            "current_turn": 0,
            "is_processing": False,
            "audio_buffer": b"",  # New: Buffer to accumulate audio data
            "last_activity": time.time(),
            "first_audio_sent": False,
            "latency_metrics": {
                "start_time": 0,
                "srt_start": 0,
                "srt_end": 0,
                "llm_start": 0,
                "llm_first_token": 0,
                "llm_first_sentence": 0,
                "tts_start": 0,
                "tts_end": 0,
                "first_audio_response": 0,
            },
        }
        return session_id

    def reset_latency_metrics(self, session_id):
        self.sessions[session_id]["latency_metrics"] = {
            "start_time": time.time(),
            "srt_start": 0,
            "srt_end": 0,
            "llm_start": 0,
            "llm_first_token": 0,
            "llm_first_sentence": 0,
            "tts_start": 0,
            "tts_end": 0,
            "first_audio_response": 0,
        }

    def update_latency_metric(self, session_id, metric, value):
        self.sessions[session_id]["latency_metrics"][metric] = value

    def calculate_latencies(self, session_id):
        metrics = self.sessions[session_id]["latency_metrics"]
        start_time = metrics["start_time"]

        return {
            "total_voice_to_voice": metrics["first_audio_response"] - start_time,
            "srt_duration": metrics["srt_end"] - metrics["srt_start"],
            "llm_ttft": metrics["llm_first_token"] - metrics["llm_start"],
            "llm_ttfs": metrics["llm_first_sentence"] - metrics["llm_start"],
            "tts_duration": metrics["tts_end"] - metrics["tts_start"],
        }

    def add_user_message(self, session_id, message):
        self.sessions[session_id]["conversation"].append({"role": "user", "content": message})
        self.sessions[session_id]["current_turn"] += 1
        self.sessions[session_id]["last_activity"] = time.time()

    def add_ai_message(self, session_id, message):
        self.sessions[session_id]["conversation"].append({"role": "assistant", "content": message})
        self.sessions[session_id]["current_turn"] += 1
        self.sessions[session_id]["last_activity"] = time.time()

    def get_conversation(self, session_id):
        return self.sessions[session_id]["conversation"]

    def set_scenario(self, session_id, scenario):
        """Switching scenario re-primes the system prompt and starts a fresh
        conversation — mixing an old scenario's history into a new role-play
        would confuse the model."""
        self.sessions[session_id]["scenario"] = scenario
        self.sessions[session_id]["conversation"] = [build_system_message(scenario)]
        self.sessions[session_id]["current_turn"] = 0
        self.sessions[session_id]["llm_output_sentences"].clear()
        self.sessions[session_id]["last_activity"] = time.time()

    def clean_old_sessions(self):
        current_time = time.time()
        sessions_to_remove = [
            session_id
            for session_id, session_data in self.sessions.items()
            if current_time - session_data["last_activity"] > self.session_timeout
        ]
        for session_id in sessions_to_remove:
            del self.sessions[session_id]
        logger.info(f"Cleaned up {len(sessions_to_remove)} old sessions")

    def add_to_audio_buffer(self, session_id, audio_data):
        self.sessions[session_id]["audio_buffer"] += audio_data

    def get_and_clear_audio_buffer(self, session_id):
        audio_data = self.sessions[session_id]["audio_buffer"]
        self.sessions[session_id]["audio_buffer"] = b""
        return audio_data


conversation_manager = ConversationManager()


async def transcribe_audio(audio_data, session_id, turn_id):
    conversation_manager.update_latency_metric(session_id, "srt_start", time.time())
    try:
        temp_file_path = f"/tmp/{session_id}-{turn_id}.opus"
        with open(temp_file_path, "wb") as temp_file:
            temp_file.write(audio_data)

        # Add a small delay to ensure the file is fully written
        await asyncio.sleep(0.1)

        with open(temp_file_path, "rb") as audio_file:
            data = aiohttp.FormData()
            data.add_field("file", audio_file, filename=f"/tmp/{session_id}-{turn_id}.opus")
            data.add_field("temperature", "0.0")
            data.add_field("temperature_inc", "0.2")
            data.add_field("response_format", "json")

            async with (
                aiohttp.ClientSession() as session,
                session.post(SRT_ENDPOINT, data=data) as response,
            ):
                result = await response.json()

        # Optionally, you can remove the temporary file here if you don't need it for debugging
        os.remove(temp_file_path)

        # logging
        conversation_manager.update_latency_metric(session_id, "srt_end", time.time())

        logger.debug(result)
        return result["text"]
    except Exception as e:
        logger.error(f"Transcription error: {str(e)}")
        logger.error(traceback.format_exc())
        raise


def is_claude(model: str) -> bool:
    return model.startswith(CLAUDE_PREFIX)


def _claude_args(model: str, system_prompt: str, stream: bool) -> list[str]:
    # --system-prompt replaces Claude Code's own default; no tools, no saved
    # session. Not --bare: it refuses subscription (OAuth) auth.
    args = [CLAUDE_BIN, "-p", "--model", model.removeprefix(CLAUDE_PREFIX)]
    args += ["--system-prompt", system_prompt, "--no-session-persistence"]
    if stream:
        args += ["--output-format", "stream-json", "--include-partial-messages", "--verbose"]
    return args + ["--tools", ""]


async def _spawn_claude(args: list[str]):
    return await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # Outside the repo, so a host (non-Docker) run doesn't pick up this
        # project's CLAUDE.md as context.
        cwd="/tmp",
    )


async def _run_claude(model: str, system_prompt: str, user_text: str) -> str | None:
    """One non-streaming `claude -p` call; None if the CLI is missing,
    fails, or times out."""
    try:
        proc = await _spawn_claude(_claude_args(model, system_prompt, stream=False))
    except OSError as e:
        logger.error(f"Couldn't start {CLAUDE_BIN!r}: {e}")
        return None
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(user_text.encode()), CLAUDE_TIMEOUT_S
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        logger.error(f"claude -p timed out after {CLAUDE_TIMEOUT_S}s")
        return None
    if proc.returncode != 0:
        logger.error(f"claude -p exited {proc.returncode}: {stderr.decode()[-500:]}")
        return None
    return stdout.decode().strip()


async def _stream_claude(model: str, system_prompt: str, user_text: str):
    """Async generator of text deltas from one streaming `claude -p` call.
    Raises on a missing CLI or a non-zero exit, like a failed Ollama
    request would."""
    proc = await _spawn_claude(_claude_args(model, system_prompt, stream=True))
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    proc.stdin.write(user_text.encode())
    await proc.stdin.drain()
    proc.stdin.close()
    async for line in proc.stdout:
        try:
            event = json.loads(line).get("event", {})
        except json.JSONDecodeError:
            continue
        delta = event.get("delta", {}) if event.get("type") == "content_block_delta" else {}
        if delta.get("type") == "text_delta" and delta.get("text"):
            yield delta["text"]
    if await proc.wait() != 0:
        stderr = (await proc.stderr.read()).decode()[-500:]
        raise RuntimeError(f"claude -p exited {proc.returncode}: {stderr}")


async def _ollama_complete(model: str, system_prompt: str, user_text: str) -> str | None:
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                LLM_ENDPOINT,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text},
                    ],
                    "temperature": 0,
                    "stream": False,
                },
            ) as response,
        ):
            data = await response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Ollama request error ({model}): {str(e)}")
        return None


async def complete(model: str, system_prompt: str, user_text: str) -> str | None:
    """One-shot system+user completion on either provider (temperature 0 on
    Ollama; the claude CLI has no temperature setting). None on failure."""
    if is_claude(model):
        return await _run_claude(model, system_prompt, user_text)
    return await _ollama_complete(model, system_prompt, user_text)


# Tuned against real-session sentences (see docs/revisit.md history): the
# earlier "strict ... natural German" wording made gemma2:9b restyle correct
# German ("denn" dropped, "sorry" -> "entschuldige"), add words that changed
# the meaning ("deshalb"), and flag every lowercase speech transcript.
GRAMMAR_CHECK_SYSTEM_PROMPT = (
    "You check German for a language learner. You will be given one utterance "
    "from a spoken conversation, often a speech-to-text transcript.\n"
    "Only fix real errors: grammar, word order, gender, case, conjugation, "
    "wrong or misspelled words.\n"
    "Do NOT change anything that is already correct German, even if you would "
    'phrase it differently. "gerne" and "gern" are both correct; never swap one '
    'for the other. Particles like "denn", "doch", "ja", "mal" and colloquial '
    'words like "sorry" are correct; never remove them.\n'
    "Ignore capitalization and punctuation completely; never count them as "
    "errors.\n"
    "Keep the speaker's words and meaning. Do not add, drop, or reword content, "
    'do not add new connecting words like "deshalb" or "weil", and do not '
    "invent words the speaker did not say.\n"
    "If the utterance quotes another sentence, correct the speaker's own "
    "sentence and leave the quote as quoted.\n"
    "If there are no real errors, reply with exactly: OK\n"
    "Otherwise reply with exactly: CORRECTED: <the full utterance with only the "
    "errors fixed>\n"
    "Reply with nothing else, no explanation."
)


def _normalize_for_compare(text: str) -> str:
    """Lowercase, punctuation stripped, whitespace collapsed — the prompt
    tells the checker to ignore capitalization/punctuation, so a
    "correction" that only differs in those is no correction."""
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


async def check_grammar(text: str, model: str = GRAMMAR_CHECK_MODEL) -> dict | None:
    """Scores one utterance's German via `model`. Returns {"correct": True,
    "corrected": None} or {"correct": False, "corrected": "..."}, or None if
    the reply didn't parse or the request failed — the caller skips sending
    an update rather than show a wrong badge."""
    reply = await complete(model, GRAMMAR_CHECK_SYSTEM_PROMPT, text)
    if reply is None:
        return None

    if reply == "OK":
        return {"correct": True, "corrected": None}
    if reply.startswith("CORRECTED:"):
        corrected = reply[len("CORRECTED:") :].strip()
        if _normalize_for_compare(corrected) == _normalize_for_compare(text):
            # Model sometimes echoes the input under "CORRECTED:" (verbatim or
            # with only capitalization/punctuation changed) instead of
            # replying "OK" — treat that as correct so the UI doesn't show a
            # no-op "correction" that just repeats the text.
            return {"correct": True, "corrected": None}
        return {"correct": False, "corrected": corrected}
    logger.warning(f"Unparseable grammar-check reply: {reply!r}")
    return None


async def send_grammar_check(websocket, role, turn_id, text, model: str = GRAMMAR_CHECK_MODEL):
    """Runs check_grammar and, if it parsed, sends the result over
    websocket. Meant to be fired via asyncio.create_task so it doesn't block
    the caller; swallows send errors since the client may have disconnected
    by the time the check resolves."""
    result = await check_grammar(text, model)
    if result is None:
        return
    try:
        await websocket.send_json(
            {
                "type": "grammar_check",
                "role": role,
                "turn": turn_id,
                "correct": result["correct"],
                "corrected": result["corrected"],
            }
        )
    except Exception as e:
        logger.warning(f"Failed to send grammar_check (client likely disconnected): {e}")


EXPLAIN_SYSTEM_PROMPT = (
    "You are a German-English language helper for a spoken German conversation "
    "practice app. You will be given one German utterance. Reply with exactly "
    "two lines, nothing else:\n"
    "Translation: <the English translation>\n"
    "Notes: <1-2 short sentences on any notable grammar, idiom, or word choice "
    'worth understanding, or "none" if there is nothing notable>'
)


_EXPLAIN_NOTES_MARKER = re.compile(r"(?:Translation\s+)?Notes:", re.IGNORECASE)


async def explain_text(text: str, model: str) -> dict | None:
    """Translates + briefly annotates one utterance via `model`. Returns
    {"translation": "...", "notes": "..."}, or None if the request failed or
    came back empty — the caller surfaces that as an error, unlike
    check_grammar this is a user-triggered, on-demand request. Prefers a
    reply in the requested "Translation:/Notes:" format but falls back to
    treating an unlabeled reply as translation-then-notes rather than
    failing on a still-useful answer (see MODEL_NOTES: several models don't
    follow the format)."""
    reply = await complete(model, EXPLAIN_SYSTEM_PROMPT, text)
    if reply is None:
        return None

    if not reply:
        return None

    # Split at the first "Notes:" marker wherever it is — models put it on
    # its own line, inline after a multi-line translation ("...tasks?
    # Notes: None"), or as "Translation Notes:" (mistral:7b). Without a
    # marker, fall back to first line = translation, rest = notes: several
    # models (see MODEL_NOTES) skip the labels but still give a usable reply.
    marker = _EXPLAIN_NOTES_MARKER.search(reply)
    if marker:
        translation, notes = reply[: marker.start()], reply[marker.end() :]
    else:
        translation, _, notes = reply.partition("\n")
    translation = " ".join(translation.split())
    if translation.lower().startswith("translation:"):
        translation = translation[len("translation:") :].strip()
    notes = notes.strip()
    # "None", or "None (some aside)", means nothing notable.
    if notes.lower().startswith("none"):
        notes = ""
    return {"translation": translation, "notes": notes}


CHECK_SYSTEM_PROMPT = (
    "You are a German teacher checking one utterance written or spoken by a "
    "learner. It may be a speech-to-text transcript. You may also be given "
    "the conversation so far, for context only: check only the line marked "
    "as the one to check.\n"
    "Go through it carefully and find every real mistake: grammar, word "
    "order, gender, case, conjugation, a wrong or extra word, a misspelling, "
    "or a sentence that does not make sense as German.\n"
    "Ignore capitalization and punctuation. Do not count style or word choice "
    "as a mistake when the German is already correct and natural; particles "
    'like "denn", "doch", "mal" are fine.\n'
    "Reply in this exact format and nothing else:\n"
    "Mistakes:\n"
    "- <wrong part> → <correct part>: <one short sentence in English "
    "explaining why>\n"
    '(one line per mistake, or the single line "- none")\n'
    "Corrected: <the full corrected utterance, or the original unchanged if "
    "there are no mistakes>"
)

_CHECK_SPEAKER = {"user": "Learner", "assistant": "Conversation partner"}
_CHECK_MISTAKE_LINE = re.compile(r"^-\s*(.+?)\s*→\s*(.+?):\s+(.+)$")


def _build_check_message(history: list[dict]) -> str:
    """Earlier turns as labeled context, then the last turn as the one to
    check."""
    *context, target = history
    parts = []
    if context:
        lines = [f"{_CHECK_SPEAKER.get(t['role'], t['role'])}: {t['content']}" for t in context]
        parts.append("Conversation so far (context only, do not check):\n" + "\n".join(lines))
    speaker = _CHECK_SPEAKER.get(target["role"], target["role"])
    parts.append(f"Line to check ({speaker}):\n{target['content']}")
    return "\n\n".join(parts)


def _parse_check_reply(reply: str) -> dict | None:
    mistakes = []
    corrected = None
    for raw in reply.splitlines():
        line = raw.strip()
        if line.startswith("Corrected:"):
            corrected = line[len("Corrected:") :].strip()
        elif line.startswith("-"):
            if line.lstrip("- ").lower().startswith("none"):
                continue
            m = _CHECK_MISTAKE_LINE.match(line)
            if m:
                mistakes.append({"wrong": m[1], "right": m[2], "why": m[3]})
            else:
                # Keep a mistake line that missed the "a → b: why" shape
                # rather than silently dropping a real finding.
                mistakes.append({"wrong": None, "right": None, "why": line.lstrip("- ")})
    if corrected is None:
        return None
    return {"mistakes": mistakes, "corrected": corrected}


async def check_text(history: list[dict], model: str = CHECK_MODEL) -> dict | None:
    """Checks the last turn of `history` (earlier turns are context) via
    `claude -p` on `model`. Returns {"mistakes": [{"wrong", "right", "why"}],
    "corrected": "..."} or None if the CLI is missing, fails, times out, or
    replies in an unparseable shape."""
    if not history:
        return None
    reply = await _run_claude(model, CHECK_SYSTEM_PROMPT, _build_check_message(history))
    if reply is None:
        return None
    result = _parse_check_reply(reply)
    if result is None:
        logger.warning(f"Check: unparseable reply: {reply!r}")
    return result


async def run_user_turn(websocket: WebSocket, session_id, get_text):
    """Shared by the stop_recording (voice) and send_text (typed) websocket
    actions: handles the interrupt-in-progress case, then once we have the
    user's text (from `get_text(turn_id)` — transcription or the typed
    string) runs it through the same add_user_message/grammar-check/LLM/TTS
    pipeline either input method goes through identically. Always ends by
    sending processing_complete, even on error."""
    conversation_manager.reset_latency_metrics(session_id)
    if conversation_manager.sessions[session_id]["is_processing"]:
        logger.warning("Interrupting ongoing processing")
        conversation_manager.sessions[session_id]["llm_output_sentences"].clear()
        conversation_manager.sessions[session_id]["is_processing"] = False
        await websocket.send_json({"type": "interrupted"})
        return

    conversation_manager.sessions[session_id]["is_processing"] = True
    turn_id = conversation_manager.sessions[session_id]["current_turn"]
    try:
        text = await get_text(turn_id)
        conversation_manager.add_user_message(session_id, text)

        # Echo the user's turn back — chat.js's "transcription" handler is
        # what actually creates the chat bubble, for both voice and typed
        # input alike.
        await websocket.send_json({"type": "transcription", "content": text, "turn": turn_id})
        asyncio.create_task(
            send_grammar_check(
                websocket,
                "user",
                turn_id,
                text,
                conversation_manager.sessions[session_id]["grammar_model"],
            )
        )

        await process_and_stream(websocket, session_id, text, turn_id)

        latencies = conversation_manager.calculate_latencies(session_id)
        await websocket.send_json({"type": "latency_metrics", "metrics": latencies})
    except Exception as e:
        logger.error(f"Error during processing: {str(e)}")
        logger.error(traceback.format_exc())
        await websocket.send_json({"type": "error", "message": str(e)})
    finally:
        conversation_manager.sessions[session_id]["is_processing"] = False
        await websocket.send_json({"type": "processing_complete"})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_id = conversation_manager.create_session()
    logger.info(f"New WebSocket connection established. Session ID: {session_id}")

    try:
        while True:
            message = await websocket.receive()
            # logger.debug(f"Received message: {message}")

            if "bytes" in message:
                audio_data = message["bytes"]
                logger.debug(f"Received audio data. Size: {len(audio_data)} bytes")
                conversation_manager.sessions[session_id]["audio_buffer"] = audio_data
            elif "text" in message:
                logger.debug(f"Received text message: {message['text']}")
                try:
                    data = json.loads(message["text"])
                    logger.debug(f"Parsed JSON data: {data}")
                    if data.get("type") == "ping":
                        # Immediately send a pong response
                        await websocket.send_json({"type": "pong"})
                    elif data.get("action") == "set_model":
                        model = data.get("model")
                        if model:
                            conversation_manager.sessions[session_id]["model"] = model
                            logger.info(f"Session {session_id} switched to model: {model}")
                            await websocket.send_json({"type": "model_set", "model": model})
                    elif data.get("action") == "set_grammar_model":
                        grammar_model = data.get("grammar_model")
                        if grammar_model:
                            conversation_manager.sessions[session_id]["grammar_model"] = (
                                grammar_model
                            )
                            logger.info(
                                f"Session {session_id} switched to grammar model: {grammar_model}"
                            )
                            await websocket.send_json(
                                {"type": "grammar_model_set", "grammar_model": grammar_model}
                            )
                    elif data.get("action") == "set_scenario":
                        scenario = data.get("scenario")
                        if scenario in get_all_scenarios(CUSTOM_SCENARIOS_PATH):
                            conversation_manager.set_scenario(session_id, scenario)
                            logger.info(f"Session {session_id} switched to scenario: {scenario}")
                            await websocket.send_json(
                                {"type": "scenario_set", "scenario": scenario}
                            )
                        else:
                            logger.warning(f"Unknown scenario requested: {scenario}")
                    elif data.get("action") == "stop_recording":
                        logger.info("Stop recording message received. Processing audio...")

                        async def get_text_from_audio(turn_id):
                            audio_data = conversation_manager.sessions[session_id]["audio_buffer"]
                            logger.info(f"Processing audio data. Size: {len(audio_data)} bytes")
                            text = await transcribe_audio(audio_data, session_id, turn_id)
                            if not text:
                                raise ValueError("Transcription resulted in empty text")
                            logger.info(f"Transcription result: {text}")
                            return text

                        await run_user_turn(websocket, session_id, get_text_from_audio)
                    elif data.get("action") == "send_text":
                        logger.info("Typed text message received. Processing...")
                        typed_text = (data.get("text") or "").strip()

                        async def get_typed_text(turn_id, typed_text=typed_text):
                            if not typed_text:
                                raise ValueError("Typed message was empty")
                            return typed_text

                        await run_user_turn(websocket, session_id, get_typed_text)
                    else:
                        logger.warning(f"Received unexpected action: {data.get('action')}")
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse JSON from text message: {message['text']}")
            else:
                logger.warning(f"Received message with unexpected format: {message}")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session_id}")
    except Exception as e:
        logger.error(f"Unexpected error in WebSocket endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        await websocket.close(code=1011, reason=str(e))


async def process_and_stream(websocket: WebSocket, session_id, text, turn_id):
    try:
        # We interleave LLM and TTS output here
        await generate_llm_response(websocket, session_id, text, turn_id)
    finally:
        conversation_manager.sessions[session_id]["is_processing"] = False
        conversation_manager.sessions[session_id]["first_audio_sent"] = False


# A sentence end is ., ! or ? followed by whitespace or the end of the text
# so far. Claude's stream deltas are multi-word and can end mid-word, so the
# old "does this delta end with punctuation" test would hold every sentence
# back until a delta happened to end exactly on one.
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def _split_complete_sentences(text: str) -> tuple[str, str]:
    """Splits `text` into (complete sentences, unfinished remainder)."""
    ends = [m.end() for m in _SENTENCE_END.finditer(text)]
    if not ends:
        return "", text
    return text[: ends[-1]], text[ends[-1] :]


async def _ollama_deltas(model: str, conversation: list[dict]):
    """Async generator of text deltas from Ollama's streaming
    OpenAI-compatible endpoint."""
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            LLM_ENDPOINT, json={"model": model, "messages": conversation, "stream": True}
        ) as response,
    ):
        async for line in response.content:
            line_text = line.decode("utf-8").strip()
            if not line_text.startswith("data: "):
                continue
            data_str = line_text[6:]
            if data_str.lower() == "[done]":
                break
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse JSON: {line_text}")
                continue
            if data.get("choices"):
                content = data["choices"][0]["delta"].get("content", "")
                if content:
                    yield content


def _claude_conversation_input(conversation: list[dict]) -> tuple[str, str]:
    """(system prompt, user text) for one `claude -p` call: `claude -p`
    takes a single prompt, not a message list, so earlier turns go in as a
    labeled transcript."""
    system = "\n\n".join(m["content"] for m in conversation if m["role"] == "system")
    lines = [
        f"{'Learner' if m['role'] == 'user' else 'You'}: {m['content']}"
        for m in conversation
        if m["role"] != "system"
    ]
    text = (
        "Conversation so far:\n"
        + "\n".join(lines)
        + "\n\nWrite your next reply to the learner: only the reply itself, no speaker label."
    )
    return system, text


async def generate_llm_response(websocket, session_id, text, turn_id):
    conversation_manager.update_latency_metric(session_id, "llm_start", time.time())
    try:
        # conversation already ends with this turn's user message (added by the
        # caller via add_user_message before process_and_stream was invoked)
        conversation = conversation_manager.get_conversation(session_id)
        model = conversation_manager.sessions[session_id]["model"]
        if is_claude(model):
            deltas = _stream_claude(model, *_claude_conversation_input(conversation))
        else:
            deltas = _ollama_deltas(model, conversation)

        complete_text = ""
        accumulated_text = ""
        first_token_received = False
        first_sentence_received = False

        async def speak(sentences: str):
            nonlocal first_sentence_received
            if not first_sentence_received:
                conversation_manager.update_latency_metric(
                    session_id, "llm_first_sentence", time.time()
                )
                first_sentence_received = True
                conversation_manager.update_latency_metric(session_id, "tts_start", time.time())
            await generate_and_send_tts(websocket, sentences)
            if not conversation_manager.sessions[session_id]["first_audio_sent"]:
                logger.debug("first_audio_response")
                conversation_manager.update_latency_metric(
                    session_id, "first_audio_response", time.time()
                )
                await websocket.send_json({"type": "first_audio_response"})
                conversation_manager.sessions[session_id]["first_audio_sent"] = True

        async for content in deltas:
            try:
                if not first_token_received:
                    conversation_manager.update_latency_metric(
                        session_id, "llm_first_token", time.time()
                    )
                    first_token_received = True
                complete_text += content
                accumulated_text += content
                await websocket.send_json({"type": "text", "content": content})
                ready, accumulated_text = _split_complete_sentences(accumulated_text)
                if ready:
                    await speak(ready)
            except Exception as e:
                logger.error(f"Error processing chunk: {e}")

        # Send any remaining text
        if accumulated_text:
            logger.debug(f"Remaining text: {accumulated_text}")
            await speak(accumulated_text)

        # Finished sending TTS
        conversation_manager.update_latency_metric(session_id, "tts_end", time.time())

        conversation_manager.add_ai_message(session_id, complete_text)
        logger.debug(complete_text)
        asyncio.create_task(
            send_grammar_check(
                websocket,
                "assistant",
                turn_id,
                complete_text,
                conversation_manager.sessions[session_id]["grammar_model"],
            )
        )

    except Exception as e:
        logger.error(f"LLM error: {str(e)}")
        logger.error(traceback.format_exc())
        raise


async def generate_and_send_tts(websocket, text):
    async with (
        aiohttp.ClientSession() as session,
        session.post(TTS_ENDPOINT, json={"text": text}) as response,
    ):
        opus_data = await response.read()
    await websocket.send_bytes(opus_data)


@app.get("/api/models")
async def list_models():
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                f"{OLLAMA_BASE}/api/tags", timeout=aiohttp.ClientTimeout(total=5)
            ) as response,
        ):
            data = await response.json()
        names = sorted(m["name"] for m in data.get("models", []))
    except Exception as e:
        logger.error(f"Failed to list Ollama models: {str(e)}")
        names = [LLM_MODEL]
    return {
        "models": [{"name": name, "note": MODEL_NOTES.get(name, "")} for name in names],
        "default": LLM_MODEL,
        "grammar_default": GRAMMAR_CHECK_MODEL,
        "explainer_default": EXPLAINER_MODEL,
        "claude_models": [{"name": name, "note": note} for name, note in CLAUDE_MODELS.items()],
        "claude_defaults": CLAUDE_DEFAULTS,
        "check_default": CHECK_MODEL,
    }


class CheckTurn(BaseModel):
    role: str
    content: str


class CheckRequest(BaseModel):
    history: list[CheckTurn]
    model: str = CHECK_MODEL


@app.post("/api/check")
async def check(req: CheckRequest):
    if not is_claude(req.model):
        raise HTTPException(status_code=400, detail="Check only runs on Claude models")
    result = await check_text([t.model_dump() for t in req.history], req.model)
    if result is None:
        raise HTTPException(status_code=502, detail="Couldn't get a check result from Claude")
    return result


class ExplainRequest(BaseModel):
    text: str
    model: str


@app.post("/api/explain")
async def explain(req: ExplainRequest):
    result = await explain_text(req.text, req.model)
    if result is None:
        raise HTTPException(status_code=502, detail="Couldn't get an explanation from the model")
    return result


class ScenarioCreate(BaseModel):
    id: str
    label: str
    prompt: str = ""


class ScenarioUpdate(BaseModel):
    label: str
    prompt: str = ""


@app.get("/api/scenarios")
async def list_scenarios():
    all_scenarios = get_all_scenarios(CUSTOM_SCENARIOS_PATH)
    return {
        "scenarios": [
            {
                "id": scenario_id,
                "label": scenario["label"],
                "prompt": scenario["prompt"],
                "is_builtin": scenario_id in SCENARIOS,
            }
            for scenario_id, scenario in all_scenarios.items()
        ],
        "default": DEFAULT_SCENARIO,
    }


@app.post("/api/scenarios")
async def create_scenario(scenario: ScenarioCreate):
    try:
        created = create_custom_scenario(
            scenario.id, scenario.label, scenario.prompt, CUSTOM_SCENARIOS_PATH
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"id": scenario.id, **created, "is_builtin": False}


@app.put("/api/scenarios/{scenario_id}")
async def edit_scenario(scenario_id: str, scenario: ScenarioUpdate):
    try:
        updated = update_custom_scenario(
            scenario_id, scenario.label, scenario.prompt, CUSTOM_SCENARIOS_PATH
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"id": scenario_id, **updated, "is_builtin": False}


@app.delete("/api/scenarios/{scenario_id}")
async def remove_scenario(scenario_id: str):
    try:
        delete_custom_scenario(scenario_id, CUSTOM_SCENARIOS_PATH)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"deleted": scenario_id}


@app.get("/api/health")
async def health():
    """Aggregate health check for the UI's setup screen: Ollama plus the
    srt-server/tts-server /health endpoints added in the previous checklist
    step. Each check is independent, so one service being down doesn't fail
    the others."""

    async def check_ollama():
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    f"{OLLAMA_BASE}/api/tags", timeout=aiohttp.ClientTimeout(total=5)
                ) as response,
            ):
                await response.json()
            return {"status": "ok"}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    async def check_service(url):
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response,
            ):
                return await response.json()
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    srt_url = urlsplit(SRT_ENDPOINT)._replace(path="/health", query="", fragment="").geturl()
    tts_url = urlsplit(TTS_ENDPOINT)._replace(path="/health", query="", fragment="").geturl()

    ollama, srt, tts = await asyncio.gather(
        check_ollama(), check_service(srt_url), check_service(tts_url)
    )
    return {"ollama": ollama, "srt": srt, "tts": tts}


@app.post("/api/unload-model")
async def unload_model():
    """Frees the GPU memory Ollama holds by asking it to unload whatever's
    currently loaded (keep_alive: 0), rather than waiting out its idle
    timeout. See CLAUDE.md."""
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                f"{OLLAMA_BASE}/api/ps", timeout=aiohttp.ClientTimeout(total=5)
            ) as response,
        ):
            data = await response.json()
        loaded = [m["name"] for m in data.get("models", [])]

        for name in loaded:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    f"{OLLAMA_BASE}/api/generate",
                    json={"model": name, "keep_alive": 0},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response,
            ):
                await response.read()

        return {"unloaded": loaded}
    except Exception as e:
        logger.error(f"Failed to unload Ollama models: {str(e)}")
        return {"unloaded": [], "error": str(e)}


@app.get("/")
def read_root():
    return FileResponse("ui/index.html")


@app.get("/chat.html")
def read_chat():
    return FileResponse("ui/chat.html")


# Run session cleanup periodically
"""
@app.on_event("startup")
@app.on_event("shutdown")
async def cleanup_sessions():
    while True:
        conversation_manager.clean_old_sessions()
        await asyncio.sleep(3600)  # Run cleanup every hour
"""

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
