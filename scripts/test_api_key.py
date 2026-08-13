#!/usr/bin/env python3
"""Test OpenAI API key — reads from opencode.jsonc config."""
import sys, io, os
sys.path.insert(0, r"C:\Users\think\Project\pm-adapter")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from pm_adapter.dispatch import _read_openai_key, _call_api_direct

api_key = _read_openai_key()
if not api_key:
    print("ERROR: No API key found in opencode.jsonc")
    sys.exit(1)

print(f"Key found: {len(api_key)} chars")

result = _call_api_direct("openai/gpt-5.6-luna", "Say hello in one word")
print(f"Status: {result.get('error', 'ok') or 'ok'}")
print(f"Content: {result['content']}")
