"""PM-1 State Reducer — event history → current state.

Reads PM-1 JSONL logs and reduces events into current project state.
The event history is the source of truth; state is derived.

Architecture:
    PM-1 JSONL → State Reducer → Current State → Context Assembler → Worker Packet

Usage:
    from pm_adapter.state_reducer import StateReducer

    reducer = StateReducer(project="kronos")
    state = reducer.reduce()  # -> ProjectState dict
    packet = reducer.assemble_packet(state, task="review code")
"""

import json
import os
import time
from pathlib import Path
from typing import Any
from collections import defaultdict

_LOG_DIR = Path(os.path.expanduser("~/.config/opencode/pm1-logs"))

# Schema field mappings (byte value -> label)
AGENT_MAP = {0: "orchestrator", 1: "fixer", 2: "oracle", 3: "explorer", 4: "librarian", 5: "designer", 6: "council"}
TOOL_MAP = {0: "read", 1: "write", 2: "edit", 3: "grep", 4: "glob", 5: "bash", 6: "task", 7: "skill", 8: "other"}
OUTCOME_MAP = {0: "ok", 1: "error", 2: "timeout", 3: "retry"}
ERROR_MAP = {0: "http", 1: "timeout", 2: "rate_limit", 3: "auth", 4: "parse", 5: "dns", 6: "unknown"}
SEVERITY_MAP = {0: "info", 1: "warn", 2: "error", 3: "critical"}
RECOVERY_MAP = {0: "none", 1: "retry_ok", 2: "fallback", 3: "abort"}
REASON_MAP = {0: "task_complete", 1: "need_review", 2: "escalation", 3: "delegation", 4: "error_handoff"}
MODEL_MAP = {0: "flash", 1: "pro", 2: "max", 3: "free"}


class ProjectState:
    """Current state for a project, derived from PM-1 event history."""

    def __init__(self, project: str):
        self.project = project
        self.agents_active: dict[str, dict] = {}  # agent -> {last_seen, tool_count, error_count}
        self.tools: dict[str, int] = defaultdict(int)  # tool -> count
        self.errors: list[dict] = []  # recent errors (last 20)
        self.error_patterns: dict[str, int] = defaultdict(int)  # error_type -> count
        self.cost_summary: dict[str, Any] = {
            "total_calls": 0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "by_agent": defaultdict(lambda: {"calls": 0, "tokens": 0}),
        }
        self.handoffs: list[dict] = []  # recent handoffs (last 10)
        self.tasks: dict[int, dict] = {}  # task_id -> {events, status}
        self.last_event_ts: float = 0
        self.event_count: int = 0

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict."""
        return {
            "project": self.project,
            "agents_active": self.agents_active,
            "tools": dict(self.tools),
            "errors": self.errors[-20:],
            "error_patterns": dict(self.error_patterns),
            "cost_summary": {
                "total_calls": self.cost_summary["total_calls"],
                "total_tokens_in": self.cost_summary["total_tokens_in"],
                "total_tokens_out": self.cost_summary["total_tokens_out"],
                "by_agent": dict(self.cost_summary["by_agent"]),
            },
            "handoffs": self.handoffs[-10:],
            "tasks": {str(k): v for k, v in self.tasks.items()},
            "last_event_ts": self.last_event_ts,
            "event_count": self.event_count,
        }


class StateReducer:
    """Reduces PM-1 event history into current project state.

    The event history is the source of truth. State is derived.
    If state is corrupted, re-reduce from events.
    """

    def __init__(self, project: str = "global", log_dir: Path | None = None):
        self.project = project
        self.log_dir = log_dir or _LOG_DIR

    def _decode_frame(self, frame: str, schema_fields: list) -> dict:
        """Decode a morse frame back to field values using schema."""
        try:
            from pro_memoria import morse_to_bits, decode_bytes
            bits = morse_to_bits(frame)
            data = decode_bytes(bits)
        except ImportError:
            # Fallback: hex decode
            data = bytes.fromhex(frame)

        result = {}
        for field in schema_fields:
            byte_idx = field["byte"]
            if byte_idx < len(data):
                raw = data[byte_idx]
                values = field.get("values", {})
                result[field["name"]] = values.get(str(raw), raw)
        return result

    def _load_schema(self, name: str) -> dict:
        schema_path = Path(__file__).resolve().parent / "schemas" / f"{name}.json"
        with schema_path.open(encoding="utf-8") as f:
            return json.load(f)

    def _reduce_tool_call(self, event: dict, state: ProjectState):
        meta = event.get("meta", {})
        agent = meta.get("agent", "unknown")
        tool = meta.get("tool", "unknown")
        outcome = meta.get("outcome", "unknown")

        # Track active agents
        if agent not in state.agents_active:
            state.agents_active[agent] = {"tool_count": 0, "error_count": 0, "last_ts": 0}
        state.agents_active[agent]["tool_count"] += 1
        state.agents_active[agent]["last_ts"] = event.get("ts", 0)

        # Track tool usage
        state.tools[tool] += 1

        # Track errors
        if outcome == "error":
            state.agents_active[agent]["error_count"] += 1

    def _reduce_error(self, event: dict, state: ProjectState):
        meta = event.get("meta", {})
        error_type = meta.get("type", "unknown")
        severity = meta.get("severity", "error")

        state.error_patterns[error_type] += 1
        state.errors.append({
            "type": error_type,
            "severity": severity,
            "agent": meta.get("agent", "unknown"),
            "ts": event.get("ts", 0),
        })
        # Keep last 20
        if len(state.errors) > 20:
            state.errors = state.errors[-20:]

    def _reduce_cost(self, event: dict, state: ProjectState):
        meta = event.get("meta", {})
        agent = meta.get("agent", "unknown")
        cost_cents = meta.get("cost_cents", 0)

        state.cost_summary["total_calls"] += 1
        state.cost_summary["total_tokens_in"] += meta.get("input_tokens", 0)
        state.cost_summary["total_tokens_out"] += meta.get("output_tokens", 0)

        agent_summary = state.cost_summary["by_agent"][agent]
        agent_summary["calls"] += 1
        agent_summary["tokens"] += meta.get("input_tokens", 0) + meta.get("output_tokens", 0)

    def _reduce_handoff(self, event: dict, state: ProjectState):
        meta = event.get("meta", {})
        task_id = meta.get("task_id", 0)

        handoff = {
            "from": meta.get("from", "unknown"),
            "to": meta.get("to", "unknown"),
            "reason": meta.get("reason", "unknown"),
            "ts": event.get("ts", 0),
        }
        state.handoffs.append(handoff)
        if len(state.handoffs) > 10:
            state.handoffs = state.handoffs[-10:]

        # Track tasks
        if task_id:
            if task_id not in state.tasks:
                state.tasks[task_id] = {"events": 0, "handoffs": [], "status": "active"}
            state.tasks[task_id]["handoffs"].append(handoff)
            if meta.get("reason") == "task_complete":
                state.tasks[task_id]["status"] = "complete"

    def reduce(self, since_ts: float = 0) -> ProjectState:
        """Reduce all PM-1 events for this project into current state.

        Args:
            since_ts: Only process events after this timestamp (0 = all)

        Returns:
            ProjectState with current derived state
        """
        state = ProjectState(self.project)

        # Read all JSONL files
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

                    # Filter by project
                    meta = event.get("meta", {})
                    if meta.get("project", "global") != self.project and self.project != "global":
                        continue

                    # Filter by timestamp
                    ts = event.get("ts", 0)
                    if ts < since_ts:
                        continue

                    # Route to reducer
                    schema = event.get("schema", "")
                    if schema == "tool_call":
                        self._reduce_tool_call(event, state)
                    elif schema == "error":
                        self._reduce_error(event, state)
                    elif schema == "cost":
                        self._reduce_cost(event, state)
                    elif schema == "handoff":
                        self._reduce_handoff(event, state)

                    state.last_event_ts = max(state.last_event_ts, ts)
                    state.event_count += 1

        return state

    def assemble_packet(self, state: ProjectState, task: str = "",
                        max_tokens: int = 4000) -> str:
        """Assemble a compact context packet from current state.

        This is the "semantic projection" of the event history.
        Designed to fit in a worker's context window.
        """
        lines = []
        lines.append(f"# Project State: {state.project}")
        lines.append(f"Events reduced: {state.event_count}")
        lines.append(f"Last activity: {time.strftime('%Y-%m-%d %H:%M', time.localtime(state.last_event_ts)) if state.last_event_ts else 'never'}")
        lines.append("")

        # Active agents
        if state.agents_active:
            lines.append("## Active Agents")
            for agent, info in sorted(state.agents_active.items()):
                lines.append(f"- {agent}: {info['tool_count']} tools, {info['error_count']} errors")
            lines.append("")

        # Tool usage
        if state.tools:
            lines.append("## Tool Usage")
            for tool, count in sorted(state.tools.items(), key=lambda x: -x[1])[:10]:
                lines.append(f"- {tool}: {count}")
            lines.append("")

        # Error patterns
        if state.error_patterns:
            lines.append("## Error Patterns")
            for etype, count in sorted(state.error_patterns.items(), key=lambda x: -x[1]):
                lines.append(f"- {etype}: {count} occurrences")
            lines.append("")

        # Cost summary
        cs = state.cost_summary
        if cs["total_calls"] > 0:
            lines.append("## Cost Summary")
            lines.append(f"- Total calls: {cs['total_calls']}")
            lines.append(f"- Tokens in: {cs['total_tokens_in']:,}")
            lines.append(f"- Tokens out: {cs['total_tokens_out']:,}")
            for agent, info in cs["by_agent"].items():
                lines.append(f"- {agent}: {info['calls']} calls, {info['tokens']:,} tokens")
            lines.append("")

        # Recent handoffs
        if state.handoffs:
            lines.append("## Recent Handoffs")
            for h in state.handoffs[-5:]:
                lines.append(f"- {h['from']} → {h['to']}: {h['reason']}")
            lines.append("")

        # Active tasks
        active_tasks = {k: v for k, v in state.tasks.items() if v["status"] == "active"}
        if active_tasks:
            lines.append("## Active Tasks")
            for tid, info in active_tasks.items():
                lines.append(f"- Task {tid}: {len(info['handoffs'])} handoffs")
            lines.append("")

        # Task context
        if task:
            lines.append(f"## Current Task")
            lines.append(task)
            lines.append("")

        packet = "\n".join(lines)

        # Truncate if over budget
        if len(packet) // 4 > max_tokens:
            packet = packet[:max_tokens * 4]

        return packet

    def get_failure_patterns(self, min_count: int = 2) -> list[dict]:
        """Extract failure patterns that have occurred multiple times.

        This is the "operational memory" — patterns the system can learn from.
        """
        state = self.reduce()
        patterns = []
        for error_type, count in state.error_patterns.items():
            if count >= min_count:
                # Find recovery patterns for this error type
                recovery_counts = defaultdict(int)
                for err in state.errors:
                    if err["type"] == error_type:
                        recovery_counts[err.get("recovery", "unknown")] += 1
                patterns.append({
                    "error_type": error_type,
                    "count": count,
                    "recoveries": dict(recovery_counts),
                    "dominant_recovery": max(recovery_counts, key=recovery_counts.get) if recovery_counts else "unknown",
                })
        return sorted(patterns, key=lambda x: -x["count"])
