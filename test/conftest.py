import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def tts_piper():
    """The active TTS server (tts-server-piper.py) as a module."""
    return _load("tts_server_piper", "tts-server-piper.py")


@pytest.fixture(scope="session")
def srt_server():
    """The STT server (srt-server.py) as a module. Default SRT_ENGINE
    (webservice) does no network I/O at import time."""
    return _load("srt_server", "srt-server.py")


@pytest.fixture(scope="session")
def voicechat2():
    """The orchestrator (voicechat2.py) as a module. Imported with cwd
    temporarily switched to the repo root since it mounts ./ui as static
    files at import time."""
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        return _load("voicechat2", "voicechat2.py")
    finally:
        os.chdir(cwd)
