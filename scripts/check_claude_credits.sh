#!/usr/bin/env bash
set -e
KEY=$(cat "$(dirname "$0")/../secrets/claude_key")
python3 - <<'PYEOF'
import sys, anthropic
key = open("secrets/claude_key").read().strip()
c = anthropic.Anthropic(api_key=key)
r = c.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=10,
    messages=[{"role": "user", "content": "hi"}],
)
print("Credits OK —", r.content[0].text)
PYEOF
