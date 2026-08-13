# Oracle Dispatch Workaround (2026-08-14)

## Issue
oh-my-opencode-slim plugin caches config. New oracle sessions fail with 402/403 errors because the plugin overrides the oracle model with non-working providers (deepseek insufficient balance, opencode-go blocked).

## Root Cause
- Plugin reads its own `oh-my-opencode-slim.json` config
- Config edits to `opencode.jsonc` don't override the plugin
- New sessions re-read the plugin config (which may be cached/stale)
- Reusable sessions continue with the model from when they were originally created

## Workaround
Use reusable sessions only. Do NOT create new oracle sessions until plugin cache clears.

### Working Sessions
- `ora-2` ✅ (ses_004aed8f4ffeNHy8B29TxFiXhT) — completed, reconciled
- `ora-3` ✅ (ses_004ae66a4ffeoXLdOxIrCbfrsT) — completed, reconciled

### Fallback
If ora-2/3 fail, use `vis-1` (OpenAI vision agent, confirmed working with `openai/gpt-5.6-luna`).

## Config Fix Applied
Changed oracle model in `oh-my-opencode-slim.json` from `opencode/deepseek-v4-flash-free` to `openai/gpt-5.6-luna`. May take effect after plugin cache clears.

## Status
- pm-adapter architecture: ✅ ready
- oracle direct path: ✅ working (reusable sessions)
- oracle via OpenCode task tool: ⚠️ external plugin issue
- production pilot: proceeding with workaround

## Last Verified
2026-08-14
