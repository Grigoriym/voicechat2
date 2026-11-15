import asyncio

import aiohttp
import pytest
from fastapi.testclient import TestClient


class _FakeAsyncCM:
    """Wraps a value so `async with x() as y` yields it unchanged."""

    def __init__(self, result):
        self._result = result

    async def __aenter__(self):
        return self._result

    async def __aexit__(self, *exc_info):
        return False


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload

    async def read(self):
        return b""


def _fake_client_session(responses):
    """Returns a fake `aiohttp.ClientSession` factory. `responses` maps
    (method, url) -> JSON payload for `.get`/`.post` calls made against it."""

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def get(self, url, **kwargs):
            return _FakeAsyncCM(_FakeResponse(responses[("GET", url)]))

        def post(self, url, **kwargs):
            return _FakeAsyncCM(_FakeResponse(responses[("POST", url)]))

    return lambda *args, **kwargs: _FakeSession()


def test_create_session_returns_isolated_sessions(voicechat2):
    mgr = voicechat2.ConversationManager()
    a = mgr.create_session()
    b = mgr.create_session()
    assert a != b
    assert mgr.sessions[a] is not mgr.sessions[b]


def test_add_user_message_appends_and_increments_turn(voicechat2):
    mgr = voicechat2.ConversationManager()
    sid = mgr.create_session()
    mgr.add_user_message(sid, "Hallo")
    assert mgr.sessions[sid]["conversation"][-1] == {"role": "user", "content": "Hallo"}
    assert mgr.sessions[sid]["current_turn"] == 1


def test_add_ai_message_appends_and_increments_turn(voicechat2):
    mgr = voicechat2.ConversationManager()
    sid = mgr.create_session()
    mgr.add_ai_message(sid, "Guten Tag")
    assert mgr.sessions[sid]["conversation"][-1] == {"role": "assistant", "content": "Guten Tag"}
    assert mgr.sessions[sid]["current_turn"] == 1


def test_audio_buffer_round_trip(voicechat2):
    mgr = voicechat2.ConversationManager()
    sid = mgr.create_session()
    mgr.add_to_audio_buffer(sid, b"abc")
    mgr.add_to_audio_buffer(sid, b"def")
    assert mgr.get_and_clear_audio_buffer(sid) == b"abcdef"
    assert mgr.sessions[sid]["audio_buffer"] == b""


def test_clean_old_sessions_removes_only_stale_sessions(voicechat2):
    mgr = voicechat2.ConversationManager()
    stale = mgr.create_session()
    fresh = mgr.create_session()
    mgr.sessions[stale]["last_activity"] -= mgr.session_timeout + 1

    mgr.clean_old_sessions()

    assert stale not in mgr.sessions
    assert fresh in mgr.sessions


def test_calculate_latencies(voicechat2):
    mgr = voicechat2.ConversationManager()
    sid = mgr.create_session()
    for metric, value in {
        "start_time": 0,
        "srt_start": 1,
        "srt_end": 2,
        "llm_start": 2,
        "llm_first_token": 3,
        "llm_first_sentence": 4,
        "tts_start": 4,
        "tts_end": 5,
        "first_audio_response": 5,
    }.items():
        mgr.update_latency_metric(sid, metric, value)

    assert mgr.calculate_latencies(sid) == {
        "total_voice_to_voice": 5,
        "srt_duration": 1,
        "llm_ttft": 1,
        "llm_ttfs": 2,
        "tts_duration": 1,
    }


def test_list_models_includes_notes_and_all_defaults(voicechat2, monkeypatch):
    responses = {
        ("GET", "http://localhost:11434/api/tags"): {
            "models": [{"name": "gemma2:9b"}, {"name": "some-untested-model"}]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))
    client = TestClient(voicechat2.app)

    response = client.get("/api/models")

    assert response.json() == {
        "models": [
            {"name": "gemma2:9b", "note": voicechat2.MODEL_NOTES["gemma2:9b"]},
            {"name": "some-untested-model", "note": ""},
        ],
        "default": voicechat2.LLM_MODEL,
        "grammar_default": voicechat2.GRAMMAR_CHECK_MODEL,
        "explainer_default": voicechat2.EXPLAINER_MODEL,
    }


def test_unload_model_asks_ollama_to_unload_whatever_is_loaded(voicechat2, monkeypatch):
    responses = {
        ("GET", "http://localhost:11434/api/ps"): {"models": [{"name": "mistral:7b"}]},
        ("POST", "http://localhost:11434/api/generate"): {"done_reason": "unload"},
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))
    client = TestClient(voicechat2.app)

    response = client.post("/api/unload-model")

    assert response.status_code == 200
    assert response.json() == {"unloaded": ["mistral:7b"]}


def test_unload_model_returns_empty_when_nothing_loaded(voicechat2, monkeypatch):
    responses: dict = {("GET", "http://localhost:11434/api/ps"): {"models": []}}
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))
    client = TestClient(voicechat2.app)

    response = client.post("/api/unload-model")

    assert response.json() == {"unloaded": []}


def test_health_reports_ok_when_all_services_are_up(voicechat2, monkeypatch):
    responses = {
        ("GET", "http://localhost:11434/api/tags"): {"models": []},
        ("GET", "http://localhost:8001/health"): {"status": "ok"},
        ("GET", "http://localhost:8003/health"): {"status": "ok"},
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))
    client = TestClient(voicechat2.app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "ollama": {"status": "ok"},
        "srt": {"status": "ok"},
        "tts": {"status": "ok"},
    }


def test_health_forwards_a_service_reported_error(voicechat2, monkeypatch):
    responses = {
        ("GET", "http://localhost:11434/api/tags"): {"models": []},
        ("GET", "http://localhost:8001/health"): {
            "status": "error",
            "detail": "connection refused",
        },
        ("GET", "http://localhost:8003/health"): {"status": "ok"},
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))
    client = TestClient(voicechat2.app)

    response = client.get("/api/health")

    data = response.json()
    assert data["srt"] == {"status": "error", "detail": "connection refused"}
    assert data["tts"] == {"status": "ok"}


def test_health_reports_error_when_a_service_is_unreachable(voicechat2, monkeypatch):
    class _PartlyFailingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def get(self, url, **kwargs):
            if url == "http://localhost:11434/api/tags":
                return _FakeAsyncCM(_FakeResponse({"models": []}))
            raise aiohttp.ClientConnectionError("boom")

    monkeypatch.setattr(
        voicechat2.aiohttp, "ClientSession", lambda *a, **kw: _PartlyFailingSession()
    )
    client = TestClient(voicechat2.app)

    response = client.get("/api/health")

    data = response.json()
    assert data["ollama"] == {"status": "ok"}
    assert data["srt"] == {"status": "error", "detail": "boom"}
    assert data["tts"] == {"status": "error", "detail": "boom"}


def test_unload_model_reports_error_when_ollama_unreachable(voicechat2, monkeypatch):
    class _FailingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def get(self, *args, **kwargs):
            raise aiohttp.ClientConnectionError("boom")

    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", lambda *a, **kw: _FailingSession())
    client = TestClient(voicechat2.app)

    response = client.post("/api/unload-model")

    data = response.json()
    assert data["unloaded"] == []
    assert "error" in data


def test_custom_scenario_round_trip(voicechat2, tmp_path):
    path = str(tmp_path / "custom_scenarios.json")

    assert voicechat2.load_custom_scenarios(path) == {}

    created = voicechat2.create_custom_scenario("airport", "Airport", "Scenario: ...", path)
    assert created == {"label": "Airport", "prompt": "Scenario: ..."}
    assert voicechat2.load_custom_scenarios(path) == {"airport": created}

    merged = voicechat2.get_all_scenarios(path)
    assert merged["airport"] == created
    assert merged["general"] == voicechat2.SCENARIOS["general"]

    voicechat2.delete_custom_scenario("airport", path)
    assert voicechat2.load_custom_scenarios(path) == {}
    assert "airport" not in voicechat2.get_all_scenarios(path)


def test_create_custom_scenario_rejects_builtin_id(voicechat2, tmp_path):
    path = str(tmp_path / "custom_scenarios.json")

    with pytest.raises(ValueError):
        voicechat2.create_custom_scenario("general", "General", "...", path)
    assert voicechat2.load_custom_scenarios(path) == {}


def test_create_custom_scenario_rejects_duplicate_custom_id(voicechat2, tmp_path):
    path = str(tmp_path / "custom_scenarios.json")
    voicechat2.create_custom_scenario("airport", "Airport", "...", path)

    with pytest.raises(ValueError):
        voicechat2.create_custom_scenario("airport", "Airport again", "...", path)


def test_delete_custom_scenario_missing_id_raises(voicechat2, tmp_path):
    path = str(tmp_path / "custom_scenarios.json")

    with pytest.raises(ValueError):
        voicechat2.delete_custom_scenario("nope", path)


def test_update_custom_scenario_round_trip(voicechat2, tmp_path):
    path = str(tmp_path / "custom_scenarios.json")
    voicechat2.create_custom_scenario("airport", "Airport", "Scenario: ...", path)

    updated = voicechat2.update_custom_scenario("airport", "Airport (edited)", "New prompt", path)

    assert updated == {"label": "Airport (edited)", "prompt": "New prompt"}
    assert voicechat2.load_custom_scenarios(path) == {"airport": updated}


def test_update_custom_scenario_rejects_builtin_id(voicechat2, tmp_path):
    path = str(tmp_path / "custom_scenarios.json")

    with pytest.raises(ValueError):
        voicechat2.update_custom_scenario("general", "General", "...", path)


def test_update_custom_scenario_missing_id_raises(voicechat2, tmp_path):
    path = str(tmp_path / "custom_scenarios.json")

    with pytest.raises(ValueError):
        voicechat2.update_custom_scenario("nope", "Nope", "...", path)


def test_delete_custom_scenario_rejects_builtin_id(voicechat2, tmp_path):
    path = str(tmp_path / "custom_scenarios.json")

    with pytest.raises(ValueError):
        voicechat2.delete_custom_scenario("general", path)


def test_scenarios_endpoint_round_trip(voicechat2, monkeypatch, tmp_path):
    path = str(tmp_path / "custom_scenarios.json")
    monkeypatch.setattr(voicechat2, "CUSTOM_SCENARIOS_PATH", path)
    client = TestClient(voicechat2.app)

    listing = client.get("/api/scenarios").json()
    assert listing["default"] == "general"
    general = next(s for s in listing["scenarios"] if s["id"] == "general")
    assert general["is_builtin"] is True
    assert general["prompt"] == ""

    created = client.post(
        "/api/scenarios", json={"id": "airport", "label": "Airport", "prompt": "Scenario: ..."}
    )
    assert created.status_code == 200
    assert created.json() == {
        "id": "airport",
        "label": "Airport",
        "prompt": "Scenario: ...",
        "is_builtin": False,
    }

    listing = client.get("/api/scenarios").json()
    airport = next(s for s in listing["scenarios"] if s["id"] == "airport")
    assert airport == {
        "id": "airport",
        "label": "Airport",
        "prompt": "Scenario: ...",
        "is_builtin": False,
    }

    edited = client.put(
        "/api/scenarios/airport", json={"label": "Airport (edited)", "prompt": "New prompt"}
    )
    assert edited.status_code == 200
    assert edited.json()["label"] == "Airport (edited)"

    deleted = client.delete("/api/scenarios/airport")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": "airport"}

    listing = client.get("/api/scenarios").json()
    assert not any(s["id"] == "airport" for s in listing["scenarios"])


def test_scenarios_endpoint_rejects_duplicate_id(voicechat2, monkeypatch, tmp_path):
    path = str(tmp_path / "custom_scenarios.json")
    monkeypatch.setattr(voicechat2, "CUSTOM_SCENARIOS_PATH", path)
    client = TestClient(voicechat2.app)
    client.post("/api/scenarios", json={"id": "airport", "label": "Airport", "prompt": "..."})

    response = client.post(
        "/api/scenarios", json={"id": "airport", "label": "Airport again", "prompt": "..."}
    )

    assert response.status_code == 400


def test_scenarios_endpoint_rejects_editing_builtin(voicechat2, monkeypatch, tmp_path):
    path = str(tmp_path / "custom_scenarios.json")
    monkeypatch.setattr(voicechat2, "CUSTOM_SCENARIOS_PATH", path)
    client = TestClient(voicechat2.app)

    response = client.put("/api/scenarios/general", json={"label": "General", "prompt": "x"})

    assert response.status_code == 400


def test_scenarios_endpoint_rejects_deleting_builtin(voicechat2, monkeypatch, tmp_path):
    path = str(tmp_path / "custom_scenarios.json")
    monkeypatch.setattr(voicechat2, "CUSTOM_SCENARIOS_PATH", path)
    client = TestClient(voicechat2.app)

    response = client.delete("/api/scenarios/general")

    assert response.status_code == 400


def test_check_grammar_returns_correct_for_ok_reply(voicechat2, monkeypatch):
    responses = {
        ("POST", "http://localhost:11434/v1/chat/completions"): {
            "choices": [{"message": {"content": "OK\n"}}]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))

    result = asyncio.run(voicechat2.check_grammar("Wie geht es dir?"))

    assert result == {"correct": True, "corrected": None}


def test_check_grammar_returns_correction_for_corrected_reply(voicechat2, monkeypatch):
    responses = {
        ("POST", "http://localhost:11434/v1/chat/completions"): {
            "choices": [{"message": {"content": "CORRECTED: Was möchtest du heute machen?"}}]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))

    result = asyncio.run(voicechat2.check_grammar("Wie kannst du heute sein wolltest du machen?"))

    assert result == {"correct": False, "corrected": "Was möchtest du heute machen?"}


def test_check_grammar_treats_verbatim_echo_as_correct(voicechat2, monkeypatch):
    text = "Hallo! Gerne können wir darüber sprechen."
    responses = {
        ("POST", "http://localhost:11434/v1/chat/completions"): {
            "choices": [{"message": {"content": f"CORRECTED: {text}"}}]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))

    result = asyncio.run(voicechat2.check_grammar(text))

    assert result == {"correct": True, "corrected": None}


def test_check_grammar_treats_case_and_punctuation_only_echo_as_correct(voicechat2, monkeypatch):
    # Speech transcripts arrive lowercase without punctuation; a "correction"
    # that only adds those back isn't a grammar correction.
    responses = {
        ("POST", "http://localhost:11434/v1/chat/completions"): {
            "choices": [{"message": {"content": "CORRECTED: Mir geht es gut, danke."}}]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))

    result = asyncio.run(voicechat2.check_grammar("mir geht es gut danke"))

    assert result == {"correct": True, "corrected": None}


def test_check_grammar_returns_none_for_unparseable_reply(voicechat2, monkeypatch):
    responses = {
        ("POST", "http://localhost:11434/v1/chat/completions"): {
            "choices": [{"message": {"content": "Sure, here's my analysis..."}}]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))

    result = asyncio.run(voicechat2.check_grammar("Hallo!"))

    assert result is None


def test_check_grammar_returns_none_on_request_error(voicechat2, monkeypatch):
    class _FailingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def post(self, *args, **kwargs):
            raise aiohttp.ClientConnectionError("boom")

    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", lambda *a, **kw: _FailingSession())

    result = asyncio.run(voicechat2.check_grammar("Hallo!"))

    assert result is None


def test_check_grammar_sends_given_model_not_the_default(voicechat2, monkeypatch):
    captured = {}

    class _CapturingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def post(self, url, json, **kwargs):
            captured["url"] = url
            captured["json"] = json
            return _FakeAsyncCM(_FakeResponse({"choices": [{"message": {"content": "OK"}}]}))

    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", lambda *a, **kw: _CapturingSession())

    asyncio.run(voicechat2.check_grammar("Hallo!", "some-other-model"))

    assert captured["json"]["model"] == "some-other-model"
    assert captured["json"]["temperature"] == 0


def test_explain_text_parses_translation_and_notes(voicechat2, monkeypatch):
    responses = {
        ("POST", "http://localhost:11434/v1/chat/completions"): {
            "choices": [
                {
                    "message": {
                        "content": (
                            "Translation: How are you?\n"
                            'Notes: Informal "du" form, appropriate between friends.'
                        )
                    }
                }
            ]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))

    result = asyncio.run(voicechat2.explain_text("Wie geht es dir?", "some-model"))

    assert result == {
        "translation": "How are you?",
        "notes": 'Informal "du" form, appropriate between friends.',
    }


def test_explain_text_falls_back_to_first_line_when_format_ignored(voicechat2, monkeypatch):
    # Some models (see MODEL_NOTES) ignore the "Translation:/Notes:" format
    # and just reply with a translation, then a blank line, then a note.
    responses = {
        ("POST", "http://localhost:11434/v1/chat/completions"): {
            "choices": [
                {"message": {"content": ("How are you?\n\nThis is a common German greeting.")}}
            ]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))

    result = asyncio.run(voicechat2.explain_text("Wie geht es dir?", "some-model"))

    assert result == {"translation": "How are you?", "notes": "This is a common German greeting."}


def test_explain_text_fallback_treats_bare_none_as_no_notes(voicechat2, monkeypatch):
    responses = {
        ("POST", "http://localhost:11434/v1/chat/completions"): {
            "choices": [{"message": {"content": "How are you doing?\nNone"}}]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))

    result = asyncio.run(voicechat2.explain_text("Wie geht es dir?", "some-model"))

    assert result == {"translation": "How are you doing?", "notes": ""}


def test_explain_text_fallback_strips_stray_notes_prefix(voicechat2, monkeypatch):
    # Some models only follow the format for the second line, leaving a
    # stray "Notes:" prefix for the fallback path to clean up.
    responses = {
        ("POST", "http://localhost:11434/v1/chat/completions"): {
            "choices": [
                {
                    "message": {
                        "content": (
                            "I don't know, maybe you could suggest something.\n"
                            'Notes: "Vorschlagen" means to suggest.'
                        )
                    }
                }
            ]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))

    result = asyncio.run(voicechat2.explain_text("...", "some-model"))

    assert result == {
        "translation": "I don't know, maybe you could suggest something.",
        "notes": '"Vorschlagen" means to suggest.',
    }


def test_explain_text_handles_inline_notes_after_multiline_translation(voicechat2, monkeypatch):
    # Real llama3.1:8b reply: unlabeled two-line translation with "Notes: None"
    # tacked onto the end of the second line, which used to leak into notes.
    responses = {
        ("POST", "http://localhost:11434/v1/chat/completions"): {
            "choices": [
                {
                    "message": {
                        "content": (
                            "Ah yes, triple sum! You really have to look closely at that one.\n"
                            "Why don't you like these kinds of tasks? Notes: None"
                        )
                    }
                }
            ]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))

    result = asyncio.run(voicechat2.explain_text("...", "some-model"))

    assert result == {
        "translation": (
            "Ah yes, triple sum! You really have to look closely at that one. "
            "Why don't you like these kinds of tasks?"
        ),
        "notes": "",
    }


def test_explain_text_handles_translation_notes_label(voicechat2, monkeypatch):
    # mistral:7b sometimes writes "Translation Notes:" instead of "Notes:".
    responses = {
        ("POST", "http://localhost:11434/v1/chat/completions"): {
            "choices": [
                {
                    "message": {
                        "content": (" That's great!\nTranslation Notes: Informal way to say it.")
                    }
                }
            ]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))

    result = asyncio.run(voicechat2.explain_text("Das ist doch super!", "some-model"))

    assert result == {"translation": "That's great!", "notes": "Informal way to say it."}


def test_explain_text_returns_none_for_empty_reply(voicechat2, monkeypatch):
    responses = {
        ("POST", "http://localhost:11434/v1/chat/completions"): {
            "choices": [{"message": {"content": "   "}}]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))

    result = asyncio.run(voicechat2.explain_text("Hallo!", "some-model"))

    assert result is None


def test_explain_text_returns_none_on_request_error(voicechat2, monkeypatch):
    class _FailingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def post(self, *args, **kwargs):
            raise aiohttp.ClientConnectionError("boom")

    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", lambda *a, **kw: _FailingSession())

    result = asyncio.run(voicechat2.explain_text("Hallo!", "some-model"))

    assert result is None


def test_explain_endpoint_returns_translation_and_notes(voicechat2, monkeypatch):
    responses = {
        ("POST", "http://localhost:11434/v1/chat/completions"): {
            "choices": [{"message": {"content": "Translation: Hello!\nNotes: none"}}]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))
    client = TestClient(voicechat2.app)

    response = client.post("/api/explain", json={"text": "Hallo!", "model": "some-model"})

    assert response.status_code == 200
    assert response.json() == {"translation": "Hello!", "notes": ""}


def test_explain_endpoint_returns_502_on_empty_reply(voicechat2, monkeypatch):
    responses = {
        ("POST", "http://localhost:11434/v1/chat/completions"): {
            "choices": [{"message": {"content": ""}}]
        }
    }
    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", _fake_client_session(responses))
    client = TestClient(voicechat2.app)

    response = client.post("/api/explain", json={"text": "Hallo!", "model": "some-model"})

    assert response.status_code == 502


class _FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


def test_send_grammar_check_sends_message_when_check_grammar_parses(voicechat2, monkeypatch):
    async def fake_check_grammar(text, model):
        assert text == "Ich bin ein Test."
        return {"correct": False, "corrected": "Ich bin ein Test!"}

    monkeypatch.setattr(voicechat2, "check_grammar", fake_check_grammar)
    ws = _FakeWebSocket()

    asyncio.run(voicechat2.send_grammar_check(ws, "user", 3, "Ich bin ein Test."))

    assert ws.sent == [
        {
            "type": "grammar_check",
            "role": "user",
            "turn": 3,
            "correct": False,
            "corrected": "Ich bin ein Test!",
        }
    ]


def test_send_grammar_check_sends_nothing_when_check_grammar_returns_none(voicechat2, monkeypatch):
    async def fake_check_grammar(text, model):
        return None

    monkeypatch.setattr(voicechat2, "check_grammar", fake_check_grammar)
    ws = _FakeWebSocket()

    asyncio.run(voicechat2.send_grammar_check(ws, "assistant", 1, "some text"))

    assert ws.sent == []


def test_websocket_sends_grammar_check_for_user_turn(voicechat2, monkeypatch):
    async def fake_transcribe_audio(audio_data, session_id, turn_id):
        return "Ich bin ein Test."

    monkeypatch.setattr(voicechat2, "transcribe_audio", fake_transcribe_audio)

    async def fake_check_grammar(text, model):
        assert text == "Ich bin ein Test."
        return {"correct": True, "corrected": None}

    monkeypatch.setattr(voicechat2, "check_grammar", fake_check_grammar)

    async def fake_process_and_stream(websocket, session_id, text, turn_id):
        return None

    monkeypatch.setattr(voicechat2, "process_and_stream", fake_process_and_stream)

    client = TestClient(voicechat2.app)
    with client.websocket_connect("/ws") as ws:
        ws.send_bytes(b"fake-audio-bytes")
        ws.send_json({"action": "stop_recording"})

        # Exactly 4 sends happen on this path: transcription, grammar_check
        # (user), latency_metrics, processing_complete.
        messages = [ws.receive_json() for _ in range(4)]

    transcription = next(m for m in messages if m["type"] == "transcription")
    assert transcription == {"type": "transcription", "content": "Ich bin ein Test.", "turn": 0}

    grammar_check = next(m for m in messages if m["type"] == "grammar_check")
    assert grammar_check == {
        "type": "grammar_check",
        "role": "user",
        "turn": 0,
        "correct": True,
        "corrected": None,
    }


def test_websocket_set_grammar_model_is_used_for_grammar_check(voicechat2, monkeypatch):
    async def fake_transcribe_audio(audio_data, session_id, turn_id):
        return "Ich bin ein Test."

    monkeypatch.setattr(voicechat2, "transcribe_audio", fake_transcribe_audio)

    captured = {}

    async def fake_check_grammar(text, model):
        captured["model"] = model
        return {"correct": True, "corrected": None}

    monkeypatch.setattr(voicechat2, "check_grammar", fake_check_grammar)

    async def fake_process_and_stream(websocket, session_id, text, turn_id):
        return None

    monkeypatch.setattr(voicechat2, "process_and_stream", fake_process_and_stream)

    client = TestClient(voicechat2.app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "set_grammar_model", "grammar_model": "custom-model"})
        assert ws.receive_json() == {
            "type": "grammar_model_set",
            "grammar_model": "custom-model",
        }

        ws.send_bytes(b"fake-audio-bytes")
        ws.send_json({"action": "stop_recording"})
        [ws.receive_json() for _ in range(4)]

    assert captured["model"] == "custom-model"


def test_websocket_send_text_processes_typed_message(voicechat2, monkeypatch):
    async def fake_check_grammar(text, model):
        assert text == "Hallo, wie geht's?"
        return {"correct": True, "corrected": None}

    monkeypatch.setattr(voicechat2, "check_grammar", fake_check_grammar)

    async def fake_process_and_stream(websocket, session_id, text, turn_id):
        return None

    monkeypatch.setattr(voicechat2, "process_and_stream", fake_process_and_stream)

    client = TestClient(voicechat2.app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "send_text", "text": "Hallo, wie geht's?"})

        # Same 4 sends as the voice path: transcription (echo), grammar_check
        # (user), latency_metrics, processing_complete.
        messages = [ws.receive_json() for _ in range(4)]

    transcription = next(m for m in messages if m["type"] == "transcription")
    assert transcription == {"type": "transcription", "content": "Hallo, wie geht's?", "turn": 0}

    grammar_check = next(m for m in messages if m["type"] == "grammar_check")
    assert grammar_check == {
        "type": "grammar_check",
        "role": "user",
        "turn": 0,
        "correct": True,
        "corrected": None,
    }
    assert any(m["type"] == "processing_complete" for m in messages)


def test_websocket_send_text_with_blank_text_sends_error(voicechat2, monkeypatch):
    client = TestClient(voicechat2.app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "send_text", "text": "   "})

        messages = [ws.receive_json() for _ in range(2)]

    error = next(m for m in messages if m["type"] == "error")
    assert "empty" in error["message"].lower()
    assert any(m["type"] == "processing_complete" for m in messages)


def test_generate_llm_response_sends_grammar_check_for_assistant_turn(voicechat2, monkeypatch):
    async def fake_check_grammar(text, model):
        assert text == "Hallo."
        return {"correct": False, "corrected": "Hallo!"}

    monkeypatch.setattr(voicechat2, "check_grammar", fake_check_grammar)

    async def fake_generate_and_send_tts(websocket, text):
        return None

    monkeypatch.setattr(voicechat2, "generate_and_send_tts", fake_generate_and_send_tts)

    class _FakeStreamResponse:
        async def _lines(self):
            yield b'data: {"choices":[{"delta":{"content":"Hallo."}}]}\n'
            yield b"data: [DONE]\n"

        @property
        def content(self):
            return self._lines()

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def post(self, url, **kwargs):
            return _FakeAsyncCM(_FakeStreamResponse())

    monkeypatch.setattr(voicechat2.aiohttp, "ClientSession", lambda *a, **kw: _FakeSession())

    session_id = voicechat2.conversation_manager.create_session()
    voicechat2.conversation_manager.add_user_message(session_id, "Wie geht's?")
    ws = _FakeWebSocket()

    async def run():
        await voicechat2.generate_llm_response(ws, session_id, "Wie geht's?", 0)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)

    asyncio.run(run())

    grammar_check = next(m for m in ws.sent if m.get("type") == "grammar_check")
    assert grammar_check == {
        "type": "grammar_check",
        "role": "assistant",
        "turn": 0,
        "correct": False,
        "corrected": "Hallo!",
    }


class _FakeProc:
    def __init__(self, stdout=b"", returncode=0, hang=False):
        self._stdout = stdout
        self.returncode = returncode
        self._hang = hang
        self.stdin_data = b""
        self.killed = False

    async def communicate(self, data=None):
        self.stdin_data = data
        if self._hang:
            await asyncio.sleep(10)
        return self._stdout, b"some error"

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def _stub_claude(voicechat2, monkeypatch, proc):
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        return proc

    monkeypatch.setattr(voicechat2.asyncio, "create_subprocess_exec", fake_exec)
    return captured


def test_check_text_parses_mistakes_and_sends_context(voicechat2, monkeypatch):
    proc = _FakeProc(
        stdout=(
            "Mistakes:\n"
            '- Was kannst du → Kannst du: "Was" is an extra word here.\n'
            "- interessantes → Interessantes: Nominalized adjectives are capitalized.\n"
            "\n"
            "Corrected: Kannst du mir heute etwas Interessantes erzählen?\n"
        ).encode()
    )
    captured = _stub_claude(voicechat2, monkeypatch, proc)
    history = [
        {"role": "assistant", "content": "Hallo! Was möchtest du heute machen?"},
        {"role": "user", "content": "Was kannst du mir heute etwas interessantes erzählen?"},
    ]

    result = asyncio.run(voicechat2.check_text(history))

    assert result == {
        "mistakes": [
            {"wrong": "Was kannst du", "right": "Kannst du", "why": '"Was" is an extra word here.'},
            {
                "wrong": "interessantes",
                "right": "Interessantes",
                "why": "Nominalized adjectives are capitalized.",
            },
        ],
        "corrected": "Kannst du mir heute etwas Interessantes erzählen?",
    }
    sent = proc.stdin_data.decode()
    assert "Conversation partner: Hallo! Was möchtest du heute machen?" in sent
    assert sent.endswith(
        "Line to check (Learner):\nWas kannst du mir heute etwas interessantes erzählen?"
    )
    assert "--system-prompt" in captured["args"]
    assert "--no-session-persistence" in captured["args"]


def test_check_text_none_means_no_mistakes(voicechat2, monkeypatch):
    proc = _FakeProc(stdout=b"Mistakes:\n- none\nCorrected: Hallo, was hast du denn vor?")
    _stub_claude(voicechat2, monkeypatch, proc)

    result = asyncio.run(
        voicechat2.check_text([{"role": "assistant", "content": "Hallo, was hast du denn vor?"}])
    )

    assert result == {"mistakes": [], "corrected": "Hallo, was hast du denn vor?"}
    # Single-turn history: no context block, just the line to check.
    assert "Conversation so far" not in proc.stdin_data.decode()


def test_check_text_keeps_malformed_mistake_line(voicechat2, monkeypatch):
    proc = _FakeProc(stdout=b"Mistakes:\n- missing verb at the end\nCorrected: Ich bin da.")
    _stub_claude(voicechat2, monkeypatch, proc)

    result = asyncio.run(voicechat2.check_text([{"role": "user", "content": "Ich da."}]))

    assert result == {
        "mistakes": [{"wrong": None, "right": None, "why": "missing verb at the end"}],
        "corrected": "Ich bin da.",
    }


def test_check_text_returns_none_for_unparseable_reply(voicechat2, monkeypatch):
    _stub_claude(voicechat2, monkeypatch, _FakeProc(stdout=b"Looks fine to me!"))

    assert asyncio.run(voicechat2.check_text([{"role": "user", "content": "Hallo"}])) is None


def test_check_text_returns_none_on_nonzero_exit(voicechat2, monkeypatch):
    _stub_claude(voicechat2, monkeypatch, _FakeProc(returncode=1))

    assert asyncio.run(voicechat2.check_text([{"role": "user", "content": "Hallo"}])) is None


def test_check_text_returns_none_on_timeout(voicechat2, monkeypatch):
    proc = _FakeProc(hang=True)
    _stub_claude(voicechat2, monkeypatch, proc)
    monkeypatch.setattr(voicechat2, "CHECK_TIMEOUT_S", 0.05)

    assert asyncio.run(voicechat2.check_text([{"role": "user", "content": "Hallo"}])) is None
    assert proc.killed


def test_check_text_returns_none_when_cli_missing(voicechat2, monkeypatch):
    async def missing(*args, **kwargs):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(voicechat2.asyncio, "create_subprocess_exec", missing)

    assert asyncio.run(voicechat2.check_text([{"role": "user", "content": "Hallo"}])) is None


def test_check_endpoint_returns_502_on_failure(voicechat2, monkeypatch):
    _stub_claude(voicechat2, monkeypatch, _FakeProc(returncode=1))
    client = TestClient(voicechat2.app)

    response = client.post("/api/check", json={"history": [{"role": "user", "content": "Hallo"}]})

    assert response.status_code == 502
