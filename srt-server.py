import os
from abc import ABC, abstractmethod
from urllib.parse import unquote

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

app = FastAPI()


class TranscriptionEngine(ABC):
    @abstractmethod
    def transcribe(self, file, audio_content, **kwargs):
        pass


class TransformersEngine(TranscriptionEngine):
    def __init__(self):
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
        from transformers.utils import is_flash_attn_2_available

        if torch.cuda.is_available():
            device = "cuda"
            torch_dtype = torch.float16
        elif torch.backends.mps.is_available():
            device = "mps"
            torch_dtype = torch.float16
        else:
            device = "cpu"
            torch_dtype = torch.float32

        # 400ms
        model_id = "openai/whisper-large-v2"
        # 220ms
        model_id = "distil-whisper/large-v2"

        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_id,
            torch_dtype=torch_dtype,
            low_cpu_mem_usage=True,
            use_safetensors=True,
            attn_implementation="flash_attention_2" if is_flash_attn_2_available() else "sdpa",
        ).to(device)

        processor = AutoProcessor.from_pretrained(model_id)

        self.pipe = pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            max_new_tokens=128,
            chunk_length_s=30,
            batch_size=16,
            return_timestamps=True,
            torch_dtype=torch_dtype,
            device=device,
            model_kwargs={"attn_implementation": "flash_attention_2"}
            if is_flash_attn_2_available()
            else {"attn_implementation": "sdpa"},
        )

    def transcribe(self, file, audio_content, **kwargs):
        result = self.pipe(audio_content, **kwargs)
        return result["text"], result.get("chunks", [])


class FasterWhisperEngine(TranscriptionEngine):
    def __init__(self):
        from faster_whisper import WhisperModel

        # 350ms
        model_id = "large-v2"
        # 300ms
        model_id = "distil-large-v2"
        # 280ms
        model_id = "distil-medium.en"
        # 300ms
        model_id = "distil-large-v3"

        self.model = WhisperModel(model_id, device="cuda", compute_type="float16")

    def transcribe(self, file, audio_content, **kwargs):
        segments, _ = self.model.transcribe(unquote(file.filename), beam_size=5)

        full_text = "".join(segment.text for segment in segments)
        logger.info(full_text)

        return full_text, [{"start": s.start, "end": s.end, "text": s.text} for s in segments]


"""
WIP - ffmpeg fails
"""


class SenseVoiceEngine(TranscriptionEngine):
    def __init__(self):
        import torch
        from funasr import AutoModel

        device = "cuda" if torch.cuda.is_available() else "cpu"

        # git clone https://huggingface.co/FunAudioLLM/SenseVoiceSmall
        model_id = "FunAudioLLM/SenseVoiceSmall"

        self.model = AutoModel(
            model=model_id,
            vad_kwargs={"max_single_segment_time": 30000},
            device=device,
            hub="hf",
        )

    def transcribe(self, file, audio_content, **kwargs):
        res = self.model.generate(
            input=unquote(file.filename),
            cache={},
            language="auto",  # "zh", "en", "yue", "ja", "ko", "nospeech"
            use_itn=True,
            batch_size_s=60,
            merge_length_s=15,
        )
        from funasr.utils.postprocess_utils import rich_transcription_postprocess

        text = rich_transcription_postprocess(res[0]["text"])
        logger.info(text)
        return text, []


class WhisperWebserviceEngine(TranscriptionEngine):
    """Bridges to an already-running openai-whisper-asr-webservice container
    instead of loading a model in-process. Zero torch/transformers dependency."""

    def __init__(self):
        import requests

        self._requests = requests
        self.url = os.getenv("WHISPER_WEBSERVICE_URL", "http://localhost:9001/asr")
        self.language = os.getenv("WHISPER_LANGUAGE", "de")

    def transcribe(self, file, audio_content, **kwargs):
        params = {"output": "json"}
        if self.language:
            params["language"] = self.language
        files = {"audio_file": ("audio.opus", audio_content, "audio/ogg")}
        response = self._requests.post(self.url, params=params, files=files, timeout=60)
        response.raise_for_status()
        result = response.json()
        text = (result.get("text") or "").strip()
        return text, result.get("segments", [])


# SRT_ENGINE: "webservice" (default, bridges to an existing whisper container,
# no local model/torch needed), "transformers", or "faster-whisper"
SRT_ENGINE = os.getenv("SRT_ENGINE", "webservice")

engine: TranscriptionEngine
if SRT_ENGINE == "transformers":
    engine = TransformersEngine()
elif SRT_ENGINE == "faster-whisper":
    engine = FasterWhisperEngine()
else:
    engine = WhisperWebserviceEngine()
logger.info(f"Using {engine.__class__.__name__}")


class TranscriptionResponse(BaseModel):
    text: str


@app.post("/v1/audio/transcriptions", response_model=TranscriptionResponse)
async def create_transcription(
    file: UploadFile = File(...),
    model: str = Form(...),
    language: str = Form(None),
    prompt: str = Form(None),
    response_format: str = Form("json"),
    temperature: float = Form(0.0),
):
    audio_content = await file.read()
    text, _ = engine.transcribe(
        file, audio_content, generate_kwargs={"language": language, "task": "transcribe"}
    )
    response = {"text": text}
    return JSONResponse(content=response, media_type="application/json")


@app.post("/v1/audio/translations", response_model=TranscriptionResponse)
async def create_translation(
    file: UploadFile = File(...),
    model: str = Form(...),
    prompt: str = Form(None),
    response_format: str = Form("json"),
    temperature: float = Form(0.0),
):
    # Read the audio file
    audio_content = await file.read()
    text, _ = engine.transcribe(file, audio_content, generate_kwargs={"task": "translate"})
    response = {"text": text}
    return JSONResponse(content=response, media_type="application/json")


@app.post("/inference")
async def inference(
    file: UploadFile = File(...),
    temperature: float = Form(0.0),
    temperature_inc: float = Form(0.0),
    response_format: str = Form("json"),
):
    # Read the audio file
    audio_content = await file.read()

    if not audio_content:
        return JSONResponse(content={"text": ""}, media_type="application/json")

    temperature += temperature_inc

    text, segments = engine.transcribe(
        file,
        audio_content,
        generate_kwargs={"temperature": temperature, "do_sample": True}
        if isinstance(engine, TransformersEngine)
        else {"beam_size": 5, "temperature": temperature},
    )

    # segments are available (see engine.transcribe above) but not yet surfaced
    # in the response for either format
    response = {"text": text}

    return JSONResponse(content=response, media_type="application/json")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
