#!/bin/bash
# Launches the voicechat2 stack against local infra already running on this
# machine: the Whisper ASR container (localhost:9001), Ollama (localhost:11434),
# and the German Piper voice — instead of the upstream README's mamba/byobu/
# llama.cpp setup. See ../../../claude/german/voice-setup.md for the full story.

set -euo pipefail
cd "$(dirname "$0")"

PIDDIR="$(pwd)/.run"
mkdir -p "$PIDDIR"

if [ -f "$PIDDIR/vc2.pid" ] && kill -0 "$(cat "$PIDDIR/vc2.pid")" 2>/dev/null; then
  echo "Already running (port 8010). Run ./stop.sh first."
  exit 1
fi

source .venv/bin/activate

export SRT_ENGINE="webservice"
export WHISPER_WEBSERVICE_URL="http://localhost:9001/asr"
export WHISPER_LANGUAGE="de"
export LLM_ENDPOINT="http://localhost:11434/v1/chat/completions"
export LLM_MODEL="llama3.1:8b"
export PIPER_BIN="/home/gregory/data/piper/piper/piper"
export PIPER_MODEL="/home/gregory/data/piper/voices/de_DE-thorsten-high.onnx"

uvicorn srt-server:app --host 127.0.0.1 --port 8001 > "$PIDDIR/srt.log" 2>&1 &
echo $! > "$PIDDIR/srt.pid"

uvicorn tts-server-piper:app --host 127.0.0.1 --port 8003 > "$PIDDIR/tts.log" 2>&1 &
echo $! > "$PIDDIR/tts.pid"

# 8000 is taken by something else on this machine; voicechat2 runs on 8010.
uvicorn voicechat2:app --host 127.0.0.1 --port 8010 > "$PIDDIR/vc2.log" 2>&1 &
echo $! > "$PIDDIR/vc2.pid"

sleep 2
echo "voicechat2 running: http://localhost:8010"
echo "Logs in $PIDDIR/*.log — stop with ./stop.sh"
