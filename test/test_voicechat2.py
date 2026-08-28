import aiohttp
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
