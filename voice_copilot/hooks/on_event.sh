#!/bin/bash
# Voice Copilot — Claude Code Hook Script
# -----------------------------------------
# Claude Code invokes this script as a hook, passing event JSON on stdin.
# We read the JSON and POST it to the local Voice Copilot FastAPI server.
#
# The server port can be overridden via the VOICE_COPILOT_PORT env var.
# If the server is not running the curl will fail silently and the script
# still exits 0 so that Claude Code is never blocked by an error.

set -euo pipefail

INPUT=$(cat)
PORT="${VOICE_COPILOT_PORT:-7865}"

# POST to Voice Copilot server.
# -s  silent (no progress meter)
# -f  fail silently on HTTP errors
# --max-time 5  don't hang if the server is slow
curl -sf --max-time 5 \
  -X POST "http://localhost:${PORT}/event" \
  -H "Content-Type: application/json" \
  -d "$INPUT" > /dev/null 2>&1 || true

exit 0
