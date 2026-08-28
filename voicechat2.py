import asyncio
import json
import logging
import os
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

# Grammar-check pass: a separate, fixed model (independent of whichever model
# is driving the conversation) used to silently score each turn's German and
# offer a correction. See docs/CHECKLIST.md "Grammar-check pass".
GRAMMAR_CHECK_MODEL = os.getenv("GRAMMAR_CHECK_MODEL", "cas/discolm-mfto-german:latest")

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
    "small_talk": {
        "label": "Small talk",
        "prompt": (
            "Scenario: you've just met the user at a casual social event. Make "
            "small talk with them — weather, weekend plans, hobbies, how their "
            "day is going. Keep it light and casual."
        ),
    },
    "doctor": {
        "label": "Doctor's appointment",
        "prompt": (
            "Scenario: you are a doctor (Ärztin/Arzt) and the user is a patient "
            "who has come in with a complaint. Ask about their symptoms, how "
            "long they've had them, and give simple advice, the way a German "
            "doctor would during a Sprechstunde."
        ),
    },
    "restaurant": {
        "label": "Restaurant",
        "prompt": (
            "Scenario: you are a waiter (Kellner/Kellnerin) at a German "
            "restaurant. Greet the user, describe menu items if asked, take "
            "their order, and handle the usual back-and-forth of a restaurant "
            "visit."
        ),
    },
    "shopping": {
        "label": "Shopping",
        "prompt": (
            "Scenario: you are a shop assistant (Verkäufer/Verkäuferin) in a "
            "German store. Help the user find what they're looking for, "
            "answer questions about sizes and prices, and handle a typical "
            "shopping interaction."
        ),
    },
    "job_interview": {
        "label": "Job interview",
        "prompt": (
            "Scenario: you are interviewing the user for a job, in German. "
            "Ask about their background, experience, and motivation, one "
            "question at a time, the way a real Vorstellungsgespräch would go."
        ),
    },
    "directions": {
        "label": "Asking for directions",
        "prompt": (
            "Scenario: the user is a tourist asking you for directions in a "
            "German city. Answer as a helpful local, giving directions and "
            "simple landmarks."
        ),
    },
    "hotel": {
        "label": "Hotel check-in",
        "prompt": (
            "Scenario: you work the front desk (Rezeption) of a German "
            "hotel. Handle the user's check-in, answer questions about the "
            "room, breakfast, and Wi-Fi, and the usual hotel small talk."
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


GRAMMAR_CHECK_SYSTEM_PROMPT = (
    "You are a strict German grammar checker. You will be given one short "
    "German utterance from a spoken conversation. If it is grammatically "
    "correct, natural German, reply with exactly: OK\n"
    "If it has a grammar or word-order mistake, reply with exactly: "
    "CORRECTED: <the corrected sentence>\n"
    "Reply with nothing else, no explanation."
)


async def check_grammar(text: str) -> dict | None:
    """Scores one utterance's German via GRAMMAR_CHECK_MODEL. Returns
    {"correct": True, "corrected": None} or {"correct": False, "corrected":
    "..."}, or None if the reply didn't parse or the request failed — the
    caller skips sending an update rather than show a wrong badge."""
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                LLM_ENDPOINT,
                json={
                    "model": GRAMMAR_CHECK_MODEL,
                    "messages": [
                        {"role": "system", "content": GRAMMAR_CHECK_SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "stream": False,
                },
            ) as response,
        ):
            data = await response.json()
        reply = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Grammar check error: {str(e)}")
        return None

    if reply == "OK":
        return {"correct": True, "corrected": None}
    if reply.startswith("CORRECTED:"):
        return {"correct": False, "corrected": reply[len("CORRECTED:") :].strip()}
    logger.warning(f"Unparseable grammar-check reply: {reply!r}")
    return None


async def send_grammar_check(websocket, role, turn_id, text):
    """Runs check_grammar and, if it parsed, sends the result over
    websocket. Meant to be fired via asyncio.create_task so it doesn't block
    the caller; swallows send errors since the client may have disconnected
    by the time the check resolves."""
    result = await check_grammar(text)
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
                        conversation_manager.reset_latency_metrics(session_id)
                        if conversation_manager.sessions[session_id]["is_processing"]:
                            logger.warning("Interrupting ongoing processing")
                            conversation_manager.sessions[session_id][
                                "llm_output_sentences"
                            ].clear()
                            conversation_manager.sessions[session_id]["is_processing"] = False
                            await websocket.send_json({"type": "interrupted"})
                        else:
                            conversation_manager.sessions[session_id]["is_processing"] = True
                            turn_id = conversation_manager.sessions[session_id]["current_turn"]
                            try:
                                audio_data = conversation_manager.sessions[session_id][
                                    "audio_buffer"
                                ]
                                logger.info(f"Processing audio data. Size: {len(audio_data)} bytes")
                                text = await transcribe_audio(audio_data, session_id, turn_id)
                                if not text:
                                    raise ValueError("Transcription resulted in empty text")
                                logger.info(f"Transcription result: {text}")
                                conversation_manager.add_user_message(session_id, text)

                                # Send transcribed text to client
                                await websocket.send_json(
                                    {"type": "transcription", "content": text, "turn": turn_id}
                                )
                                asyncio.create_task(
                                    send_grammar_check(websocket, "user", turn_id, text)
                                )

                                await process_and_stream(websocket, session_id, text, turn_id)

                                latencies = conversation_manager.calculate_latencies(session_id)
                                await websocket.send_json(
                                    {"type": "latency_metrics", "metrics": latencies}
                                )
                            except Exception as e:
                                logger.error(f"Error during processing: {str(e)}")
                                logger.error(traceback.format_exc())
                                await websocket.send_json({"type": "error", "message": str(e)})
                            finally:
                                conversation_manager.sessions[session_id]["is_processing"] = False
                                await websocket.send_json({"type": "processing_complete"})
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


async def generate_llm_response(websocket, session_id, text, turn_id):
    conversation_manager.update_latency_metric(session_id, "llm_start", time.time())
    try:
        # conversation already ends with this turn's user message (added by the
        # caller via add_user_message before process_and_stream was invoked)
        conversation = conversation_manager.get_conversation(session_id)
        model = conversation_manager.sessions[session_id]["model"]

        async with (
            aiohttp.ClientSession() as session,
            session.post(
                LLM_ENDPOINT, json={"model": model, "messages": conversation, "stream": True}
            ) as response,
        ):
            complete_text = ""
            accumulated_text = ""
            first_token_received = False
            first_sentence_received = False
            async for line in response.content:
                if line:
                    try:
                        line_text = line.decode("utf-8").strip()
                        if line_text.startswith("data: "):
                            data_str = line_text[6:]
                            if data_str.lower() == "[done]":
                                break
                            data = json.loads(data_str)
                            if "choices" in data and len(data["choices"]) > 0:
                                content = data["choices"][0]["delta"].get("content", "")
                                if content:
                                    if not first_token_received:
                                        conversation_manager.update_latency_metric(
                                            session_id, "llm_first_token", time.time()
                                        )
                                        first_token_received = True
                                    complete_text += content
                                    accumulated_text += content
                                    await websocket.send_json({"type": "text", "content": content})

                                    # Check if we have a complete sentence
                                    if content.endswith((".", "!", "?")):
                                        if not first_sentence_received:
                                            conversation_manager.update_latency_metric(
                                                session_id, "llm_first_sentence", time.time()
                                            )
                                            first_sentence_received = True
                                            conversation_manager.update_latency_metric(
                                                session_id, "tts_start", time.time()
                                            )
                                        await generate_and_send_tts(websocket, accumulated_text)
                                        accumulated_text = ""

                                        if not conversation_manager.sessions[session_id][
                                            "first_audio_sent"
                                        ]:
                                            logger.debug("first_audio_response")
                                            conversation_manager.update_latency_metric(
                                                session_id, "first_audio_response", time.time()
                                            )
                                            await websocket.send_json(
                                                {"type": "first_audio_response"}
                                            )
                                            conversation_manager.sessions[session_id][
                                                "first_audio_sent"
                                            ] = True
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse JSON: {line_text}")
                    except Exception as e:
                        logger.error(f"Error processing line: {e}")

            # Send any remaining text
            if accumulated_text:
                logger.debug(f"Remaining text: {accumulated_text}")
                if not first_sentence_received:
                    conversation_manager.update_latency_metric(
                        session_id, "llm_first_sentence", time.time()
                    )
                    first_sentence_received = True
                    conversation_manager.update_latency_metric(session_id, "tts_start", time.time())
                await generate_and_send_tts(websocket, accumulated_text)

                if not conversation_manager.sessions[session_id]["first_audio_sent"]:
                    logger.debug("first_audio_response")
                    conversation_manager.update_latency_metric(
                        session_id, "first_audio_response", time.time()
                    )
                    await websocket.send_json({"type": "first_audio_response"})
                    conversation_manager.sessions[session_id]["first_audio_sent"] = True

            # Finished sending TTS
            conversation_manager.update_latency_metric(session_id, "tts_end", time.time())

            conversation_manager.add_ai_message(session_id, complete_text)
            logger.debug(complete_text)
            asyncio.create_task(send_grammar_check(websocket, "assistant", turn_id, complete_text))

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
        models = sorted(m["name"] for m in data.get("models", []))
        return {"models": models, "default": LLM_MODEL}
    except Exception as e:
        logger.error(f"Failed to list Ollama models: {str(e)}")
        return {"models": [LLM_MODEL], "default": LLM_MODEL}


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
