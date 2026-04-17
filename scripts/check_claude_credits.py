#!/usr/bin/env python3
from pathlib import Path
import anthropic

key = (Path(__file__).parent.parent / "secrets/claude_key").read_text().strip()
c = anthropic.Anthropic(api_key=key)
r = c.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=10,
    messages=[{"role": "user", "content": "hi"}],
)
print("Credits OK —", r.content[0].text)
