#!/bin/bash
cd "$(dirname "$0")"
PIDDIR="$(pwd)/.run"

for name in srt tts vc2; do
  pidfile="$PIDDIR/$name.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    if kill "$pid" 2>/dev/null; then
      echo "stopped $name ($pid)"
    fi
    rm -f "$pidfile"
  fi
done
