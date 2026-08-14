"""Dispatch Worker — thin wrapper for closed-loop PM-1 integration.

Wires task dispatch to the proven run_agent.py path with automatic:
- Handoff event logging
- Context assembly (project state + decisions)
- Completion event recording
- Structured result envelope

Usage:
    from pm_adapter.dispatch import dispatch_worker

    result = dispatch_worker("morse", "Update paper §2.1", agent="fixer")
    print(result["status"], result["content_length"])
"""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .decision_store import DecisionStore
from .encoder import encode_handoff, encode_tool_call, encode_error, _LOG_DIR

# --- Model resolution ---

MODEL_MAP = {
    "fixer": "openai/gpt-5.6-luna",
    "oracle": "openai/gpt-5.6-luna",
    "explorer": "openai/gpt-5.6-luna",
    "designer": "openai/gpt-5.6-luna",
    "librarian": "openai/gpt-5.6-luna",
    "council": "openai/gpt-5.6-luna",
}

# --- Result envelope ---


@dataclass
class WorkerResult:
    """Structured result from a worker dispatch. Never ambiguous."""
    status: str = "unknown"  # completed | empty | error | transport_error
    content: str = ""
    content_length: int = 0
    duration: float = 0.0
    task_id: str = ""
    agent: str = ""
    project: str = ""
    error: str = ""
    events_recorded: int = 0

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "content_length": self.content_length,
            "duration": round(self.duration, 2),
            "task_id": self.task_id,
            "agent": self.agent,
            "project": self.project,
            "error": self.error,
            "events_recorded": self.events_recorded,
        }


# --- Core dispatch function ---


def dispatch_worker(
    project: str,
    task_spec: str,
    agent: str = "fixer",
    model: str | None = None,
    session_id: str = "",
    context_extra: str = "",
    files: list[str] | None = None,
) -> WorkerResult:
    """Dispatch a task to a worker with full PM-1 event lifecycle.

    1. Record handoff event
    2. Assemble context (decisions + project state + files + task)
    3. Dispatch via direct API
    4. Record completion event
    5. Return structured result
    """
    start = time.time()
    task_id = f"dispatch-{int(start)}"
    events_count = 0

    # --- 1. Record handoff event ---
    try:
        _log_event("handoff", {
            "task_id": task_id,
            "from": "orchestrator",
            "to": agent,
            "task": task_spec,
            "project": project,
            "files": files or [],
        })
        events_count += 1
    except Exception:
        pass  # Non-fatal: event logging failure shouldn't block dispatch

    # --- 2. Assemble context packet ---
    context = _assemble_context(project, task_spec, context_extra, files)

    # --- 3. Dispatch via direct API (proven path) ---
    resolved_model = model or MODEL_MAP.get(agent, "openai/gpt-5.6-luna")
    prompt = f"{context}\n\n## Task\n{task_spec}"

    try:
        result = _call_api_direct(resolved_model, prompt)
        events_count += 1
    except Exception as e:
        _log_event("error", {
            "task_id": task_id,
            "type": "dispatch_error",
            "error": str(e),
            "agent": agent,
            "project": project,
        })
        return WorkerResult(
            status="transport_error",
            error=str(e),
            duration=time.time() - start,
            task_id=task_id,
            agent=agent,
            project=project,
            events_recorded=events_count,
        )

    duration = time.time() - start

    # --- 4. Record completion ---
    content = result.get("content", "")
    content_length = len(content)

    if content_length == 0:
        status = "empty"
    elif result.get("error"):
        status = "error"
    else:
        status = "completed"

    _log_event("tool_call", {
        "task_id": task_id,
        "agent": agent,
        "outcome": status,
        "duration": round(duration, 2),
        "content_length": content_length,
        "model": resolved_model,
        "project": project,
    })
    events_count += 1

    # --- 5. Return structured result ---
    return WorkerResult(
        status=status,
        content=content,
        content_length=content_length,
        duration=duration,
        task_id=task_id,
        agent=agent,
        project=project,
        error=result.get("error", ""),
        events_recorded=events_count,
    )


# --- Helpers (internal) ---


def _assemble_context(project: str, task_spec: str, extra: str = "",
                      files: list[str] | None = None) -> str:
    """Auto-assemble context packet: project state + decisions + files + task."""
    parts = [f"## Project: {project}"]

    # Relevant decisions
    try:
        ds = DecisionStore()
        decisions = ds.list_decisions(project)
        active = [d for d in decisions if d.get("status") == "active"]
        if active:
            parts.append("### Decisions")
            for d in active[-5:]:  # Last 5 active decisions
                parts.append(f"- [{d['id']}] {d['decision']}")
    except Exception:
        pass

    # File contents — the missing source
    if files:
        parts.append("### Relevant Files")
        for fpath in files:
            try:
                p = Path(fpath)
                if p.exists() and p.is_file():
                    content = p.read_text(encoding="utf-8", errors="replace")
                    # Truncate huge files: include first 200 + last 50 lines
                    lines = content.splitlines()
                    if len(lines) > 250:
                        truncated = "\n".join(lines[:200]) + f"\n... ({len(lines)-250} lines omitted) ...\n" + "\n".join(lines[-50:])
                        parts.append(f"\n**{p.name}** ({len(lines)} lines, truncated):\n```\n{truncated}\n```")
                    else:
                        parts.append(f"\n**{p.name}** ({len(lines)} lines):\n```\n{content}\n```")
            except Exception:
                pass  # Skip unreadable files

    # Extra context
    if extra:
        parts.append(f"\n{extra}")

    return "\n".join(parts)


def _call_run_agent(model: str, prompt: str, agent: str, task_id: str) -> dict:
    """Call run_agent.py via subprocess (proven path)."""
    import subprocess
    import sys

    script = Path(__file__).resolve().parent.parent / "scripts" / "run_agent.py"
    if not script.exists():
        # Fallback: direct API call if run_agent.py not found
        return _call_api_direct(model, prompt)

    cmd = [
        sys.executable, str(script),
        "--agent", agent,
        "--model", model,
        prompt,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "OPENAI_MODEL": model},
        )
        content = proc.stdout.strip()
        if proc.returncode != 0 and not content:
            return {"content": "", "error": proc.stderr.strip() or "non-zero exit"}
        return {"content": content, "error": ""}
    except subprocess.TimeoutExpired:
        return {"content": "", "error": "timeout"}
    except Exception as e:
        return {"content": "", "error": str(e)}


def _call_api_direct(model: str, prompt: str) -> dict:
    """Direct API call fallback when run_agent.py is unavailable."""
    import urllib.request
    import urllib.error

    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    api_key = os.environ.get("OPENAI_API_KEY", "")

    # Fallback: read from opencode.jsonc if env var not set
    if not api_key:
        api_key = _read_openai_key()

    if not api_key:
        return {"content": "", "error": "OPENAI_API_KEY not set"}

    # Extract model name from provider prefix
    model_name = model.split("/", 1)[-1] if "/" in model else model

    payload = json.dumps({
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": 4096,
    }).encode()

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            return {"content": content or "", "error": ""}
    except Exception as e:
        return {"content": "", "error": str(e)}


def _log_event(schema: str, meta: dict):
    """Append a PM-1 event to the log directory."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    event = {
        "schema": schema,
        "ts": time.time(),
        "meta": meta,
    }
    log_file = _LOG_DIR / f"{meta.get('project', 'global')}.jsonl"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _read_openai_key() -> str:
    """Read OpenAI API key from opencode.jsonc config."""
    config_path = Path.home() / ".config" / "opencode" / "opencode.jsonc"
    if not config_path.exists():
        return ""
    try:
        import re
        text = config_path.read_text(encoding="utf-8")
        # Find the openai provider's apiKey
        # Pattern: look for "openai" block, then find apiKey within it
        match = re.search(r'"openai"\s*:\s*\{[^}]*"apiKey"\s*:\s*"([^"]+)"', text, re.DOTALL)
        if match:
            return match.group(1)
        # Fallback: first sk-proj- key
        match = re.search(r'"(sk-proj-[^"]+)"', text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return ""
