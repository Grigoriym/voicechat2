import asyncio
import io
import os
import re
import wave

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from loguru import logger
from pydantic import BaseModel

# Shells out to the same Piper binary + German voice already used by
# ~/bin/deutsch, instead of the Python `piper` package (which the original
# test/piper-server.py used and which strips all non-ASCII characters,
# destroying German umlauts/ß, plus needs onnxruntime and has known
# CUDA-only quirks that don't apply here anyway on AMD hardware).

app = FastAPI()

PIPER_BIN = os.getenv("PIPER_BIN", "/home/gregory/data/piper/piper/piper")
PIPER_MODEL = os.getenv("PIPER_MODEL", "/home/gregory/data/piper/voices/de_DE-thorsten-high.onnx")
SAMPLE_RATE = int(os.getenv("PIPER_SAMPLE_RATE", "22050"))


class TTSRequest(BaseModel):
    text: str


def clean_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"~+", "!", text)
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"(\*[^*]+\*)|(_[^_]+_)", "", text)
    return text.strip()


@app.post("/tts")
async def text_to_speech(request: TTSRequest):
    text = clean_text(request.text)
    if not text:
        return Response(content=b"", media_type="audio/wav")

    try:
        proc = await asyncio.create_subprocess_exec(
            PIPER_BIN,
            "--model",
            PIPER_MODEL,
            "--output-raw",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        pcm, _ = await proc.communicate(text.encode("utf-8"))
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=f"Piper binary not found at {PIPER_BIN}") from e

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)

    logger.info(f"TTS: {len(text)} chars -> {len(pcm)} PCM bytes")
    return Response(content=buf.getvalue(), media_type="audio/wav")


@app.get("/health")
async def health():
    missing = [p for p in (PIPER_BIN, PIPER_MODEL) if not os.path.exists(p)]
    if missing:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "detail": f"missing: {', '.join(missing)}"},
        )
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)
