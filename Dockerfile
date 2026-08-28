# One image, three services (see docker-compose.yml's `command:` per
# service) - same active path as run.sh, just containerized.
FROM python:3.12-slim

# libstdc++6: needed at runtime by the Piper binary bind-mounted into the
# tts service (see docker-compose.yml) - it's a native ELF binary linking
# libstdc++, not present in the slim base image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-lean.txt .
RUN pip install --no-cache-dir -r requirements-lean.txt

COPY voicechat2.py srt-server.py tts-server-piper.py ./
COPY ui/ ./ui/

EXPOSE 8001 8003 8010
