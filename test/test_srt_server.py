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


def test_whisper_webservice_engine_non_json_response_returns_empty_string(srt_server, monkeypatch):
    # A 200 response with a non-JSON body instead of {"text": ...} must fall
    # back to an empty transcription rather than raising ValueError out of
    # response.json().
    engine = srt_server.WhisperWebserviceEngine()

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError(
                "Attempt to decode JSON with unexpected mimetype: text/plain; charset=utf-8"
            )

    monkeypatch.setattr(engine._requests, "post", lambda *a, **k: FakeResponse())

    text, segments = engine.transcribe(file=None, audio_content=b"fake")

    assert text == ""
    assert segments == []


def test_whisper_webservice_engine_http_error_returns_empty_string(srt_server, monkeypatch):
    # Regression test for docs/revisit.md #1: a header-only capture (non-zero
    # bytes, but no actual audio, e.g. a near-instant push-to-talk click)
    # makes the webservice itself reply with a 500, which raise_for_status()
    # turns into requests.exceptions.HTTPError. Unhandled, that used to
    # surface to the browser as a raw 500 (aiohttp.ContentTypeError on the
    # orchestrator side, decoding the resulting plain-text error body as
    # JSON). It must fall back to an empty transcription instead, same as
    # the 0-byte short-circuit in /inference.
    engine = srt_server.WhisperWebserviceEngine()

    class FakeResponse:
        def raise_for_status(self):
            raise engine._requests.exceptions.HTTPError("500 Server Error")

        def json(self):
            raise AssertionError("should not be reached")

    monkeypatch.setattr(engine._requests, "post", lambda *a, **k: FakeResponse())

    text, segments = engine.transcribe(file=None, audio_content=b"fake")

    assert text == ""
    assert segments == []


def test_health_ok_when_whisper_webservice_reachable(srt_server, monkeypatch):
    monkeypatch.setattr(srt_server.engine, "url", "http://localhost:9001/asr")
    monkeypatch.setattr(srt_server.engine._requests, "get", lambda *a, **k: None)

    client = TestClient(srt_server.app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_error_when_whisper_webservice_unreachable(srt_server, monkeypatch):
    class FakeRequestException(Exception):
        pass

    def raise_connection_error(*a, **k):
        raise FakeRequestException("connection refused")

    monkeypatch.setattr(
        srt_server.engine._requests.exceptions, "RequestException", FakeRequestException
    )
    monkeypatch.setattr(srt_server.engine._requests, "get", raise_connection_error)

    client = TestClient(srt_server.app)
    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "detail": "connection refused"}
