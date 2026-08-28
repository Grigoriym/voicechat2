from fastapi.testclient import TestClient


def test_inference_empty_audio_short_circuits(srt_server):
    # Regression test for the bug documented in CLAUDE.md: 0-byte/silent audio
    # (near-instant push-to-talk, VAD misfire) used to crash /inference with
    # an opaque aiohttp.ContentTypeError instead of hitting the "empty text"
    # path. It must return {"text": ""} without ever reaching the engine.
    client = TestClient(srt_server.app)
    response = client.post("/inference", files={"file": ("empty.opus", b"", "audio/ogg")})
    assert response.status_code == 200
    assert response.json() == {"text": ""}


def test_whisper_webservice_engine_strips_result_text(srt_server, monkeypatch):
    engine = srt_server.WhisperWebserviceEngine()

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "  Hallo Welt  ", "segments": []}

    monkeypatch.setattr(engine._requests, "post", lambda *a, **k: FakeResponse())

    text, segments = engine.transcribe(file=None, audio_content=b"fake")

    assert text == "Hallo Welt"
    assert segments == []


def test_whisper_webservice_engine_missing_text_key_returns_empty_string(srt_server, monkeypatch):
    engine = srt_server.WhisperWebserviceEngine()

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {}

    monkeypatch.setattr(engine._requests, "post", lambda *a, **k: FakeResponse())

    text, _ = engine.transcribe(file=None, audio_content=b"fake")

    assert text == ""
