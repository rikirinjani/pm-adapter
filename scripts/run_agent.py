#!/usr/bin/env python3
"""
Generalized agent wrapper — calls any specialist agent via dispatch_with_retry()
with budget fix. Bypasses opencode's task tool which doesn't set adequate
max_completion_tokens for reasoning models, causing empty content on session resume.

Usage:
  python run_agent.py --agent oracle "review this code for bugs"
  python run_agent.py --agent council "analyze the architecture"
  python run_agent.py --agent designer "suggest UI improvements"
  python run_agent.py --agent librarian --files src/engine.ts "find docs on WebSocket patterns"
  python run_agent.py --agent fixer --model deepseek-v4-pro "fix the memory leak"

Supported agents: oracle, council, designer, librarian, explorer, fixer

Environment:
  OPENAI_BASE_URL  — API base URL (required)
  OPENAI_API_KEY   — API key (required)
  ORACLE_MODEL     — Override model for oracle (optional)
  COUNCIL_MODEL    — Override model for council (optional)
  DESIGNER_MODEL   — Override model for designer (optional)
"""

import sys
import os
import json
import time
import urllib.request
import urllib.error
import ssl

# PM-1 encoder for compressed logging
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from pm_adapter.encoder import (
        encode_tool_call, encode_handoff, encode_error, encode_cost,
        bucket_tokens, bucket_duration, bucket_cost, bucket_size,
    )
    PM1_AVAILABLE = True
except ImportError:
    PM1_AVAILABLE = False

# Budget constants — same as dispatch.py
MIN_COMPLETION_TOKENS = 2048
MAX_COMPLETION_TOKENS = 32768

# Agent system prompts — role-appropriate, grounded in the system prompt definitions
AGENT_PROMPTS = {
    "oracle": """You are the Oracle — a strategic technical advisor specializing in architecture decisions, complex debugging, code review, simplification, and engineering guidance.

Key capabilities:
- Deep architectural reasoning and system-level trade-off analysis
- Complex debugging with unclear root cause
- Code review and simplification
- Security, scalability, and data integrity decisions

Approach:
- Think deeply before responding
- Consider multiple perspectives
- Provide concrete, actionable recommendations
- Flag risks and trade-offs explicitly
- Be concise but thorough""",

    "council": """You are the Council — a read-only advisory panel that examines codebases and provides independent analysis.

Key capabilities:
- Independent code analysis from multiple perspectives
- Architecture review and trade-off assessment
- Pattern identification and anti-pattern detection
- Risk assessment and mitigation recommendations

Approach:
- Provide balanced, evidence-based analysis
- Consider multiple viewpoints
- Flag both strengths and weaknesses
- Be specific with file references and line numbers
- Summarize consensus and dissent""",

    "designer": """You are the Designer — a UI/UX specialist focused on visual quality, interaction design, and user experience.

Key capabilities:
- UI/UX design, review, and implementation
- Styling, responsive design, component architecture
- Visual polish, animations, micro-interactions
- Accessibility and usability assessment

Approach:
- Focus on user experience and visual quality
- Consider responsive behavior across devices
- Prioritize clarity and usability over cleverness
- Provide specific CSS/styling recommendations
- Reference design systems and best practices""",

    "librarian": """You are the Librarian — an external documentation and library research specialist.

Key capabilities:
- Official documentation lookup and verification
- GitHub examples and pattern research
- Library internals understanding
- API reference and usage guidance

Approach:
- Always cite official documentation sources
- Provide working code examples from real usage
- Verify API compatibility and version-specific behavior
- Flag deprecated patterns and recommend alternatives
- Be concise but complete""",

    "explorer": """You are the Explorer — a fast codebase search and pattern matching specialist.

Key capabilities:
- Fast file discovery and pattern matching
- Codebase structure analysis
- Symbol and reference location
- Pattern detection across large codebases

Approach:
- Be fast and efficient
- Provide exact file paths and line numbers
- Summarize findings concisely
- Flag patterns and anomalies
- Use glob, grep, and AST search effectively""",

    "fixer": """You are the Fixer — a fast implementation specialist for well-defined tasks.

Key capabilities:
- Fast, targeted code changes
- Bug fixes and small feature implementations
- Code refactoring and cleanup
- Test updates and maintenance

Approach:
- Execute efficiently without over-engineering
- Make minimal, focused changes
- Preserve existing patterns and conventions
- Verify changes compile/pass tests
- Be concise in explanations""",
}

CONFIG_PATH = os.path.expanduser("~/.config/opencode/oh-my-opencode-slim.json")

# PM-1 trace log path
PM1_LOG_DIR = os.path.expanduser("~/.config/opencode/pm1-logs")
os.makedirs(PM1_LOG_DIR, exist_ok=True)

# Fallback defaults if config can't be read
FALLBACK_MODELS = {
    "oracle": "deepseek-v4-pro",
    "council": "deepseek-v4-pro",
    "designer": "deepseek-v4-pro",
    "librarian": "deepseek-v4-flash",
    "explorer": "deepseek-v4-flash",
    "fixer": "deepseek-v4-flash",
}


def load_preset_models():
    """Read oh-my-opencode-slim config and return {agent: model} for the active preset."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        preset_name = config.get("preset", "")
        agents = config.get("presets", {}).get(preset_name, {})
        return {agent: info.get("model", "") for agent, info in agents.items() if info.get("model")}
    except Exception:
        return {}


def get_api_config(agent):
    """Get API configuration from environment variables and oh-my-opencode-slim config."""
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    api_key = os.environ.get("OPENAI_API_KEY", "")

    # Check for agent-specific model override (env > config > fallback)
    env_model_key = f"{agent.upper()}_MODEL"
    env_model = os.environ.get(env_model_key)
    if env_model:
        model = env_model
    else:
        preset_models = load_preset_models()
        model = preset_models.get(agent, FALLBACK_MODELS.get(agent, "deepseek-v4-pro"))

    # Strip provider prefix if present (e.g., "deepseek/deepseek-v4-pro" -> "deepseek-v4-pro")
    if "/" in model:
        model = model.split("/")[-1]

    return base_url.rstrip("/"), api_key, model


def estimate_tokens(text):
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def pm1_log(frame: str, schema_name: str, metadata: dict | None = None):
    """Append a PM-1 frame to the daily log file."""
    if not PM1_AVAILABLE:
        return
    import datetime
    today = datetime.date.today().isoformat()
    log_path = os.path.join(PM1_LOG_DIR, f"{today}.jsonl")
    entry = {"frame": frame, "schema": schema_name, "ts": time.time()}
    if metadata:
        entry["meta"] = metadata
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_tool_call(agent: str, tool: str, outcome: str, duration: float,
                  size_bytes: int = 0, tokens_in: int = 0, tokens_out: int = 0):
    """Log a tool call as PM-1 compressed frame."""
    if not PM1_AVAILABLE:
        return
    frame = encode_tool_call(
        agent=agent, tool=tool, outcome=outcome,
        duration_bucket=bucket_duration(duration),
        size_bucket=bucket_size(size_bytes) if size_bytes else "<1KB",
        tokens_in=min(tokens_in // 100, 255),  # compress to byte
        tokens_out=min(tokens_out // 100, 255),
    )
    pm1_log(frame, "tool_call", {"agent": agent, "tool": tool, "outcome": outcome})


def log_handoff(from_agent: str, to_agent: str, reason: str,
                confidence: str = "medium", files_touched: int = 0,
                tokens_used: int = 0, duration: float = 0):
    """Log a handoff as PM-1 compressed frame."""
    if not PM1_AVAILABLE:
        return
    dur_bucket = "30s-2m"
    if duration < 30: dur_bucket = "<30s"
    elif duration < 600: dur_bucket = "2-10m"
    elif duration < 1800: dur_bucket = "10-30m"
    else: dur_bucket = ">30m"

    frame = encode_handoff(
        from_agent=from_agent, to_agent=to_agent, reason=reason,
        confidence=confidence, files_touched=min(files_touched, 255),
        tokens_used=min(tokens_used // 1000, 255),
        duration_bucket=dur_bucket,
    )
    pm1_log(frame, "handoff", {"from": from_agent, "to": to_agent, "reason": reason})


def log_error(agent: str, error_type: str, severity: str = "error",
              retry_count: int = 0, http_code: int = 0, duration: float = 0,
              recovery: str = "none"):
    """Log an error as PM-1 compressed frame."""
    if not PM1_AVAILABLE:
        return
    frame = encode_error(
        agent=agent, error_type=error_type, severity=severity,
        retry_count=min(retry_count, 255), http_code=min(http_code, 255),
        duration_bucket=bucket_duration(duration), recovery=recovery,
    )
    pm1_log(frame, "error", {"agent": agent, "type": error_type, "severity": severity})


def log_cost(agent: str, model_tier: str, input_tokens: int, output_tokens: int,
             cost_cents: float, duration: float, task_count: int = 1):
    """Log cost as PM-1 compressed frame."""
    if not PM1_AVAILABLE:
        return
    frame = encode_cost(
        agent=agent, model_tier=model_tier,
        input_bucket=bucket_tokens(input_tokens),
        output_bucket=bucket_tokens(output_tokens),
        cost_bucket=bucket_cost(cost_cents),
        duration_bucket=bucket_duration(duration),
        task_count=min(task_count, 255),
    )
    pm1_log(frame, "cost", {"agent": agent, "model": model_tier, "cost_cents": cost_cents})


def dispatch_with_retry(messages, model, base_url, api_key, max_retries=3):
    """Budget-aware OpenAI-compatible API dispatch with retry logic."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    input_tokens = sum(estimate_tokens(m.get("content", "")) for m in messages)
    budget = max(MIN_COMPLETION_TOKENS, MAX_COMPLETION_TOKENS - input_tokens)

    payload = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": budget,
        "temperature": 0.7,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )

            t_req = time.time()
            with urllib.request.urlopen(req, context=ctx, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            duration = time.time() - t_req

            content = data["choices"][0]["message"]["content"]
            finish_reason = data["choices"][0].get("finish_reason", "unknown")
            usage = data.get("usage", {})
            actual_completion = usage.get("completion_tokens", 0)
            prompt_tokens = usage.get("prompt_tokens", 0)

            # PM-1: log successful tool call
            log_tool_call(
                agent=agent, tool="api_call", outcome="ok",
                duration=duration, size_bytes=len(content),
                tokens_in=prompt_tokens, tokens_out=actual_completion,
            )

            if finish_reason == "length" or actual_completion >= budget * 0.95:
                if attempt < max_retries - 1:
                    budget = min(budget * 2, MAX_COMPLETION_TOKENS)
                    payload["max_completion_tokens"] = budget
                    print(f"[{agent}] Truncation detected (attempt {attempt + 1}), retrying with budget={budget}", file=sys.stderr)
                    # PM-1: log retry
                    log_error(agent=agent, error_type="timeout", severity="warn",
                              retry_count=attempt + 1, duration=duration, recovery="retry_ok")
                    time.sleep(2)
                    continue

            return {
                "content": content,
                "finish_reason": finish_reason,
                "usage": usage,
                "model": model,
                "budget_used": f"{actual_completion}/{budget}",
                "pm1_frame": encode_tool_call(
                    agent=agent, tool="api_call", outcome="ok",
                    duration_bucket=bucket_duration(duration),
                    size_bucket=bucket_size(len(content)),
                    tokens_in=min(prompt_tokens // 100, 255),
                    tokens_out=min(actual_completion // 100, 255),
                ) if PM1_AVAILABLE else None,
            }

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            duration = time.time() - t_req if 't_req' in dir() else 0
            if e.code == 429:
                retry_after = int(e.headers.get("Retry-After", 10))
                print(f"[{agent}] Rate limited, waiting {retry_after}s (attempt {attempt + 1})", file=sys.stderr)
                log_error(agent=agent, error_type="rate_limit", severity="warn",
                          retry_count=attempt + 1, http_code=429, duration=duration, recovery="retry_ok")
                time.sleep(retry_after)
                continue
            elif e.code in (502, 503, 504) and attempt < max_retries - 1:
                print(f"[{agent}] Server error {e.code}, retrying in 5s (attempt {attempt + 1})", file=sys.stderr)
                log_error(agent=agent, error_type="http", severity="error",
                          retry_count=attempt + 1, http_code=e.code, duration=duration, recovery="retry_ok")
                time.sleep(5)
                continue
            else:
                log_error(agent=agent, error_type="http", severity="critical",
                          retry_count=attempt + 1, http_code=e.code, duration=duration, recovery="abort")
                return {"content": "", "error": f"HTTP {e.code}: {body[:500]}", "model": model}

        except Exception as e:
            if attempt < max_retries - 1:
                print(f"[{agent}] Error: {e}, retrying in 5s (attempt {attempt + 1})", file=sys.stderr)
                log_error(agent=agent, error_type="unknown", severity="error",
                          retry_count=attempt + 1, duration=duration, recovery="retry_ok")
                time.sleep(5)
                continue
            log_error(agent=agent, error_type="unknown", severity="critical",
                      retry_count=attempt + 1, duration=duration, recovery="abort")
            return {"content": "", "error": str(e), "model": model}

    return {"content": "", "error": "Max retries exceeded", "model": model}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generalized agent wrapper with budget-aware dispatch")
    parser.add_argument("--agent", required=True, choices=list(AGENT_PROMPTS.keys()),
                        help="Agent type: oracle, council, designer, librarian, explorer, fixer")
    parser.add_argument("prompt", help="The prompt for the agent")
    parser.add_argument("--files", nargs="*", help="File paths to include as context")
    parser.add_argument("--model", help="Override model ID")
    parser.add_argument("--system-prompt", help="Override system prompt")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    agent = args.agent
    base_url, api_key, model = get_api_config(agent)
    if args.model:
        model = args.model.split("/")[-1] if "/" in args.model else args.model

    if not base_url or not api_key:
        print("Error: Set OPENAI_BASE_URL and OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)

    # Build context from files
    file_context = ""
    if args.files:
        for fpath in args.files:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                file_context += f"\n\n--- File: {fpath} ---\n{content}\n--- End: {fpath} ---"
            except Exception as e:
                print(f"[{agent}] Warning: Could not read {fpath}: {e}", file=sys.stderr)

    # Build messages
    system = args.system_prompt or AGENT_PROMPTS[agent]
    user_content = args.prompt
    if file_context:
        user_content += f"\n\n{file_context}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]

    # Dispatch
    t0 = time.time()
    result = dispatch_with_retry(messages, model, base_url, api_key)
    elapsed = time.time() - t0

    # PM-1: log cost summary
    if PM1_AVAILABLE and not result.get("error"):
        usage = result.get("usage", {})
        prompt_t = usage.get("prompt_tokens", 0)
        comp_t = usage.get("completion_tokens", 0)
        # Rough cost estimate (deepseek pricing: ~$0.14/1M input, ~$0.28/1M output)
        cost_cents = (prompt_t * 0.14 + comp_t * 0.28) / 100
        model_tier = "flash" if "flash" in model else ("pro" if "pro" in model else "free")
        log_cost(agent=agent, model_tier=model_tier,
                 input_tokens=prompt_t, output_tokens=comp_t,
                 cost_cents=cost_cents, duration=elapsed)

    if args.json:
        result["elapsed_seconds"] = round(elapsed, 1)
        result["agent"] = agent
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if result.get("error"):
            print(f"ERROR: {result['error']}", file=sys.stderr)
            sys.exit(1)
        print(result["content"])
        print(f"\n[{agent}] model={result['model']} budget={result['budget_used']} elapsed={elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
