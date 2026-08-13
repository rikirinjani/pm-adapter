"""Project Reducer — aggregates session summaries into project state.

Reads saved session states and reduces them into project-level summary.
Project = long-term accumulation of all sessions.

Architecture:
    Session summaries (saved by SessionReducer)
        │
        ▼
    Project Reducer
        │
        ▼
    Project Summary (PM-1 frame)
        │
        ▼
    Context Assembler (for new sessions)
"""

import json
import os
import time
from pathlib import Path
from typing import Any
from collections import defaultdict

_SESSION_DIR = Path(os.path.expanduser("~/.config/opencode/pm1-sessions"))
_PROJECT_DIR = Path(os.path.expanduser("~/.config/opencode/pm1-projects"))

AGENT_MAP = {0: "orchestrator", 1: "fixer", 2: "oracle", 3: "explorer", 4: "librarian", 5: "designer", 6: "council"}
AGENT_REVERSE = {v: k for k, v in AGENT_MAP.items()}

PATTERN_MAP = {"none": 0, "rate_limit": 1, "timeout": 2, "auth": 3, "http": 4, "other": 5}
PATTERN_REVERSE = {v: k for k, v in PATTERN_MAP.items()}


class ProjectState:
    """Long-term accumulated state for a project."""

    def __init__(self, project: str):
        self.project = project
        self.total_sessions: int = 0
        self.total_tasks: int = 0
        self.total_errors: int = 0
        self.total_cost_cents: float = 0
        self.total_tokens: int = 0
        self.sessions: list[dict] = []
        self.error_patterns: dict[str, int] = defaultdict(int)
        self.agent_usage: dict[str, int] = defaultdict(int)
        self.last_session_outcome: str = "success"
        self.first_session_ts: float = 0
        self.last_session_ts: float = 0

    @property
    def error_rate(self) -> float:
        return self.total_errors / self.total_tasks if self.total_tasks > 0 else 0

    @property
    def dominant_pattern(self) -> str:
        if not self.error_patterns:
            return "none"
        return max(self.error_patterns, key=self.error_patterns.get)

    @property
    def health(self) -> str:
        if self.error_rate < 0.05:
            return "healthy"
        elif self.error_rate < 0.20:
            return "degraded"
        elif self.error_rate < 0.50:
            return "unhealthy"
        return "critical"

    @property
    def is_new(self) -> bool:
        return self.total_sessions <= 1

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "total_sessions": self.total_sessions,
            "total_tasks": self.total_tasks,
            "total_errors": self.total_errors,
            "error_rate": round(self.error_rate, 4),
            "total_cost_cents": round(self.total_cost_cents, 4),
            "total_tokens": self.total_tokens,
            "dominant_pattern": self.dominant_pattern,
            "health": self.health,
            "last_session_outcome": self.last_session_outcome,
            "agent_usage": dict(self.agent_usage),
            "error_patterns": dict(self.error_patterns),
            "first_session_ts": self.first_session_ts,
            "last_session_ts": self.last_session_ts,
        }


class ProjectReducer:
    """Reduces session summaries into project-level state.

    The project state is the longest-lived representation.
    It accumulates across sessions and provides the context
    for new session initialization.
    """

    def __init__(self, session_dir: Path | None = None, project_dir: Path | None = None):
        self.session_dir = session_dir or _SESSION_DIR
        self.project_dir = project_dir or _PROJECT_DIR
        self.project_dir.mkdir(parents=True, exist_ok=True)

    def _bucket_error_rate(self, rate: float) -> str:
        if rate < 0.05: return "lt_5pct"
        if rate < 0.20: return "5_20pct"
        if rate < 0.50: return "20_50pct"
        return "gt_50pct"

    def _bucket_cost(self, cents: float) -> str:
        if cents < 10: return "lt_10c"
        if cents < 50: return "10c_50c"
        if cents < 200: return "50c_2d"
        if cents < 1000: return "2d_10d"
        return "gt_10d"

    def reduce_project(self, project: str) -> ProjectState:
        """Reduce all saved sessions into project state."""
        state = ProjectState(project)

        for path in sorted(self.session_dir.glob("*.json")):
            with path.open(encoding="utf-8") as f:
                session = json.load(f)

            if session.get("project") != project:
                continue

            state.total_sessions += 1
            state.total_tasks += session.get("tasks_completed", 0) + session.get("tasks_failed", 0)
            state.total_errors += session.get("total_errors", 0)
            state.total_cost_cents += session.get("total_cost_cents", 0)
            state.total_tokens += session.get("total_tokens", 0)
            state.sessions.append(session)
            state.last_session_outcome = session.get("outcome", "success")

            # Aggregate error patterns
            for etype, count in session.get("error_patterns", {}).items():
                state.error_patterns[etype] += count

            # Track timestamps
            duration = session.get("duration_seconds", 0)
            if duration > 0:
                # Approximate timestamps from duration
                if not state.first_session_ts:
                    state.first_session_ts = time.time() - duration
                state.last_session_ts = time.time()

        return state

    def emit_summary(self, state: ProjectState) -> str:
        """Emit a project_summary PM-1 frame from ProjectState."""
        from .encoder import encode_record
        return encode_record("project_summary", {
            "project": state.project,
            "total_sessions": min(state.total_sessions, 255),
            "total_tasks": min(state.total_tasks, 255),
            "total_errors": min(state.total_errors, 255),
            "error_rate_bucket": self._bucket_error_rate(state.error_rate),
            "total_cost_bucket": self._bucket_cost(state.total_cost_cents),
            "dominant_pattern": state.dominant_pattern,
            "health": state.health,
            "last_session_outcome": state.last_session_outcome,
            "flags": "needs_attention" if state.health in ("unhealthy", "critical") else "none",
        })

    def save_project(self, state: ProjectState):
        """Save project state to disk."""
        path = self.project_dir / f"{state.project}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2)

    def load_project(self, project: str) -> dict | None:
        """Load saved project state."""
        path = self.project_dir / f"{project}.json"
        if path.exists():
            with path.open(encoding="utf-8") as f:
                return json.load(f)
        return None

    def get_failure_memory(self, project: str, min_count: int = 3) -> list[dict]:
        """Extract failure patterns that have occurred across sessions.

        This is the "operational memory" — patterns the system has learned.
        """
        state = self.reduce_project(project)
        patterns = []
        for error_type, count in state.error_patterns.items():
            if count >= min_count:
                patterns.append({
                    "error_type": error_type,
                    "total_count": count,
                    "avg_per_session": round(count / state.total_sessions, 1) if state.total_sessions > 0 else 0,
                })
        return sorted(patterns, key=lambda x: -x["total_count"])

    def get_context_for_new_session(self, project: str, task_domain: str = "") -> str:
        """Generate context packet for initializing a new session.

        Multi-source: operational telemetry + relevant decisions.
        This is what the project state feeds into the context assembler.
        """
        from .decision_store import DecisionStore

        state = self.reduce_project(project)
        lines = []
        lines.append(f"# Project: {state.project}")
        lines.append(f"Sessions: {state.total_sessions} | Tasks: {state.total_tasks} | Errors: {state.total_errors}")
        lines.append(f"Health: {state.health} | Error rate: {state.error_rate:.1%}")
        lines.append(f"Total cost: ${state.total_cost_cents/100:.2f} | Tokens: {state.total_tokens:,}")
        lines.append("")

        # Known failure patterns
        patterns = self.get_failure_memory(project, min_count=2)
        if patterns:
            lines.append("## Known Failure Patterns")
            for p in patterns[:5]:
                lines.append(f"- {p['error_type']}: {p['total_count']}x total, ~{p['avg_per_session']}/session")
            lines.append("")

        # Agent usage
        if state.agent_usage:
            lines.append("## Agent Usage")
            for agent, count in sorted(state.agent_usage.items(), key=lambda x: -x[1]):
                lines.append(f"- {agent}: {count} calls")
            lines.append("")

        # Last session
        lines.append(f"## Last Session")
        lines.append(f"Outcome: {state.last_session_outcome}")
        lines.append("")

        # Decision context (the missing layer)
        try:
            ds = DecisionStore()
            decision_ctx = ds.get_context_packet(project, task_domain=task_domain)
            if decision_ctx:
                lines.append(decision_ctx)
                lines.append("")
        except Exception:
            pass  # Decision store not available, skip silently

        return "\n".join(lines)
