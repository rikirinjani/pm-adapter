#!/usr/bin/env python3
"""Test self-configuring credential path."""
import sys, os

# Clear env vars to simulate OpenCode task tool environment
os.environ.pop("OPENAI_BASE_URL", None)
os.environ.pop("OPENAI_API_KEY", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from run_agent import load_opencode_credentials, load_preset_models, get_api_config

url, key = load_opencode_credentials()
print(f"Resolved URL: {url}")
print(f"Resolved key: {key[:15]}...")

presets = load_preset_models()
oracle_model = presets.get("oracle", "NOT FOUND")
print(f"Oracle model from preset: {oracle_model}")

full_url, full_key, model = get_api_config("oracle")
print(f"Full URL: {full_url}")
print(f"Full key: {full_key[:15]}...")
print(f"Model: {model}")
