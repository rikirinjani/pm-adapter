"""Session Reducer — aggregates task-level events into session summaries.

Reads PM-1 JSONL task events and reduces them into a session summary.
Session = a bounded unit of work (one run_agent.py invocation or one opencode session).

Architecture:
    Task events (tool_call, error, cost, handoff)
        │
        ▼
    Session Reducer
        │
        ▼
    Session Summary (PM-1 frame)
        │
        ▼
    Project Reducer
"""

import json
import os
import time
from pathlib import Path
from typing import Any
from collections import defaultdict

_LOG_DIR = Path(os.path.expanduser("~/.config/opencode/pm1-logs"))
_SESSION_DIR = Path(os.path.expanduser("~/.config/opencode/pm1-sessions"))

AGENT_MAP = {0: "orchestrator", 1: "fixer", 2: "oracle", 3: "explorer", 4: "librarian", 5: "designer", 6: "council"}
AGENT_REVERSE = {v: k for k, v in AGENT_MAP.items()}


class SessionState:
    """Aggregated state for a single session."""

    def __init__(self, session_id: str, project: str):
        self.session_id = session_id
        self.project = project
        self.tasks_completed: int = 0
        self.tasks_failed: int = 0
        self.total_tool_calls: int = 0
        self.total_errors: int = 0
        self.agent_calls: dict[str, int] = defaultdict(int)
        self.total_cost_cents: float = 0
        self.total_tokens: int = 0
        self.start_ts: float = 0
        self.end_ts: float = 0
        self.error_patterns: dict[str, int] = defaultdict(int)
        self.handoffs: list[dict] = []
        self.outcome: str = "success"  # success, partial, failed, abandoned

    @property
    def duration_seconds(self) -> float:
        return self.end_ts - self.start_ts if self.end_ts and self.start_ts else 0

    @property
    def dominant_agent(self) -> str:
        if not self.agent_calls:
            return "orchestrator"
        return max(self.agent_calls, key=self.agent_calls.get)

    @property
    def error_rate(self) -> float:
        total = self.tasks_completed + self.tasks_failed
        return self.tasks_failed / total if total > 0 else 0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "project": self.project,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "total_tool_calls": self.total_tool_calls,
            "total_errors": self.total_errors,
            "dominant_agent": self.dominant_agent,
            "total_cost_cents": round(self.total_cost_cents, 4),
            "total_tokens": self.total_tokens,
            "duration_seconds": round(self.duration_seconds, 1),
            "error_patterns": dict(self.error_patterns),
            "outcome": self.outcome,
        }


class SessionReducer:
    """Reduces task-level events into session summaries.

    A session is a bounded unit of work. This reducer:
    1. Reads task events for a session
    2. Aggregates them into a SessionState
    3. Emits a session_summary PM-1 frame
    """

    def __init__(self, log_dir: Path | None = None, session_dir: Path | None = None):
        self.log_dir = log_dir or _LOG_DIR
        self.session_dir = session_dir or _SESSION_DIR
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def _bucket_duration(self, seconds: float) -> str:
        if seconds < 60: return "<1m"
        if seconds < 300: return "1-5m"
        if seconds < 1800: return "5-30m"
        if seconds < 7200: return "30m-2h"
        return ">2h"

    def _bucket_cost(self, cents: float) -> str:
        if cents < 1: return "lt_1c"
        if cents < 5: return "1c_5c"
        if cents < 20: return "5c_20c"
        if cents < 100: return "20c_1d"
        return "gt_1d"

    def _bucket_error_rate(self, rate: float) -> str:
        if rate < 0.05: return "lt_5pct"
        if rate < 0.20: return "5_20pct"
        if rate < 0.50: return "20_50pct"
        return "gt_50pct"

    def reduce_session(self, session_id: str, project: str = "global",
                       since_ts: float = 0, until_ts: float = 0) -> SessionState:
        """Reduce all task events for a session into SessionState."""
        state = SessionState(session_id, project)

        for log_file in sorted(self.log_dir.glob("*.jsonl")):
            with log_file.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    meta = event.get("meta", {})
                    ts = event.get("ts", 0)

                    # Filter by project
                    if meta.get("project", "global") != project and project != "global":
                        continue

                    # Filter by time window
                    if since_ts and ts < since_ts:
                        continue
                    if until_ts and ts > until_ts:
                        continue

                    schema = event.get("schema", "")

                    if schema == "tool_call":
                        state.total_tool_calls += 1
                        agent = meta.get("agent", "unknown")
                        state.agent_calls[agent] += 1
                        if not state.start_ts:
                            state.start_ts = ts
                        state.end_ts = max(state.end_ts, ts)

                        if meta.get("outcome") == "error":
                            state.total_errors += 1

                    elif schema == "error":
                        state.total_errors += 1
                        error_type = meta.get("type", "unknown")
                        state.error_patterns[error_type] += 1

                    elif schema == "cost":
                        state.total_cost_cents += meta.get("cost_cents", 0)
                        state.total_tokens += meta.get("input_tokens", 0) + meta.get("output_tokens", 0)

                    elif schema == "handoff":
                        state.handoffs.append(meta)

        # Determine outcome
        if state.tasks_failed > 0 and state.tasks_completed == 0:
            state.outcome = "failed"
        elif state.tasks_failed > 0:
            state.outcome = "partial"
        elif state.total_errors > state.total_tool_calls * 0.5:
            state.outcome = "partial"

        return state

    def emit_summary(self, state: SessionState) -> str:
        """Emit a session_summary PM-1 frame from SessionState."""
        from .encoder import encode_record
        return encode_record("session_summary", {
            "project": state.project,
            "tasks_completed": min(state.tasks_completed, 255),
            "tasks_failed": min(state.tasks_failed, 255),
            "total_tool_calls": min(state.total_tool_calls, 255),
            "total_errors": min(state.total_errors, 255),
            "dominant_agent": state.dominant_agent,
            "cost_bucket": self._bucket_cost(state.total_cost_cents),
            "duration_bucket": self._bucket_duration(state.duration_seconds),
            "outcome": state.outcome,
            "flags": "has_failures" if state.total_errors > 0 else "none",
        })

    def save_session(self, state: SessionState):
        """Save session state to disk for project-level aggregation."""
        path = self.session_dir / f"{state.session_id}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2)

    def load_session(self, session_id: str) -> dict | None:
        """Load a saved session state."""
        path = self.session_dir / f"{session_id}.json"
        if path.exists():
            with path.open(encoding="utf-8") as f:
                return json.load(f)
        return None

    def list_sessions(self, project: str = "global") -> list[dict]:
        """List all saved sessions for a project."""
        sessions = []
        for path in sorted(self.session_dir.glob("*.json")):
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
            if data.get("project") == project or project == "global":
                sessions.append(data)
        return sessions
