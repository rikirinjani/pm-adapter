"""Decision Store — first-class durable memory for architectural decisions.

Decisions are first-class PM-1 objects with provenance, not fields in session summaries.
They answer: "Why is it this way?" — separate from operational telemetry ("What is happening?").

Storage: append-only JSONL file at ~/.config/opencode/pm1-decisions/{project}.jsonl
Retrieval: by project, tags, status, domain — for context assembly.
Supersession: decisions can be replaced (old ones marked superseded).

Architecture:
    PM-1 event log (tool calls, errors, etc.)
        │
        ▼
    Task/Session/Project reducers (operational telemetry)
        │
        ▼
    Decision Store (knowledge memory)
        │
        ▼
    Context Compiler (multi-source: telemetry + decisions)
        │
        ▼
    Worker Packet (~40 tok baseline + relevant decisions)
"""

import json
import os
import time
from pathlib import Path
from typing import Any
from datetime import datetime

_DECISION_DIR = Path(os.path.expanduser("~/.config/opencode/pm1-decisions"))

AGENT_MAP = {0: "orchestrator", 1: "fixer", 2: "oracle", 3: "explorer", 4: "librarian", 5: "designer", 6: "council"}


class Decision:
    """A single architectural/design decision with provenance."""

    def __init__(self, id: str, decision: str, rationale: str, scope: str,
                 tags: list[str], status: str = "active", supersedes: str = "",
                 session_id: str = "", task_summary: str = "", domain: str = "",
                 source: str = "manual"):
        self.id = id
        self.decision = decision
        self.rationale = rationale
        self.scope = scope
        self.tags = tags
        self.status = status
        self.supersedes = supersedes
        self.session_id = session_id
        self.task_summary = task_summary
        self.domain = domain
        self.source = source
        self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "decision": self.decision,
            "rationale": self.rationale,
            "scope": self.scope,
            "tags": self.tags,
            "status": self.status,
            "supersedes": self.supersedes,
            "session_id": self.session_id,
            "task_summary": self.task_summary,
            "domain": self.domain,
            "source": self.source,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Decision":
        d = cls(
            id=data["id"],
            decision=data["decision"],
            rationale=data["rationale"],
            scope=data.get("scope", ""),
            tags=data.get("tags", []),
            status=data.get("status", "active"),
            supersedes=data.get("supersedes", ""),
            session_id=data.get("session_id", ""),
            task_summary=data.get("task_summary", ""),
            domain=data.get("domain", ""),
            source=data.get("source", "manual"),
        )
        d.created_at = data.get("created_at", d.created_at)
        return d

    def to_context_line(self) -> str:
        """Format for inclusion in context packet."""
        return f"- [{self.id}] {self.decision} (rationale: {self.rationale})"


class DecisionStore:
    """Append-only decision store with relevance filtering.

    Decisions are stored per-project as JSONL files.
    Each decision has full provenance (session, task, timestamp).
    Supersession is tracked (old decisions marked superseded, not deleted).
    """

    def __init__(self, decision_dir: Path | None = None):
        self.decision_dir = decision_dir or _DECISION_DIR
        self.decision_dir.mkdir(parents=True, exist_ok=True)

    def _get_path(self, project: str) -> Path:
        return self.decision_dir / f"{project}.jsonl"

    def _next_id(self, project: str) -> str:
        """Generate next decision ID: DEC-YYYY-NNNN."""
        year = datetime.now().year
        existing = self.list_decisions(project)
        count = sum(1 for d in existing if d.id.startswith(f"DEC-{year}"))
        return f"DEC-{year}-{count + 1:04d}"

    def add(self, project: str, decision: str, rationale: str,
            tags: list[str] | None = None, domain: str = "",
            session_id: str = "", task_summary: str = "",
            supersedes: str = "", source: str = "manual") -> Decision:
        """Record a new decision."""
        dec = Decision(
            id=self._next_id(project),
            decision=decision,
            rationale=rationale,
            scope=project,
            tags=tags or [],
            status="active",
            supersedes=supersedes,
            session_id=session_id,
            task_summary=task_summary,
            domain=domain,
            source=source,
        )

        # If superseding an old decision, mark it
        if supersedes:
            self._mark_superseded(project, supersedes)

        # Append to file
        path = self._get_path(project)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dec.to_dict(), ensure_ascii=False) + "\n")

        return dec

    def _mark_superseded(self, project: str, decision_id: str):
        """Mark a decision as superseded (rewrite file)."""
        path = self._get_path(project)
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        updated = []
        for line in lines:
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("id") == decision_id and data.get("status") == "active":
                data["status"] = "superseded"
            updated.append(json.dumps(data, ensure_ascii=False))
        path.write_text("\n".join(updated) + "\n", encoding="utf-8")

    def list_decisions(self, project: str, status: str = "active") -> list[Decision]:
        """List all decisions for a project, optionally filtered by status."""
        path = self._get_path(project)
        if not path.exists():
            return []
        decisions = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            if status and data.get("status") != status:
                continue
            decisions.append(Decision.from_dict(data))
        return decisions

    def get_relevant(self, project: str, tags: list[str] | None = None,
                     domain: str = "", limit: int = 5) -> list[Decision]:
        """Get decisions relevant to current context.

        Relevance scoring:
        - Exact tag match: +3
        - Domain match: +2
        - Recent decisions: +1
        - Superseded: excluded
        """
        decisions = self.list_decisions(project, status="active")
        scored = []
        for dec in decisions:
            score = 0
            if tags:
                score += sum(3 for t in tags if t in dec.tags)
            if domain and dec.domain == domain:
                score += 2
            # Recency bonus (simplified)
            score += 1
            scored.append((score, dec))

        scored.sort(key=lambda x: -x[0])
        return [dec for _, dec in scored[:limit]]

    def get_context_packet(self, project: str, task_domain: str = "",
                           max_decisions: int = 5) -> str:
        """Generate decision context for inclusion in worker packet.

        Returns formatted string, or empty string if no relevant decisions.
        """
        tags = ["architectural", "active"]
        decisions = self.get_relevant(project, tags=tags, domain=task_domain, limit=max_decisions)
        if not decisions:
            return ""

        lines = ["## Project Decisions"]
        for dec in decisions:
            lines.append(f"- [{dec.id}] {dec.decision}")
            if dec.rationale:
                lines.append(f"  Rationale: {dec.rationale}")
        return "\n".join(lines)

    def count(self, project: str, status: str = "active") -> int:
        """Count decisions for a project."""
        return len(self.list_decisions(project, status=status))

    def get_decision(self, project: str, decision_id: str) -> Decision | None:
        """Get a specific decision by ID."""
        for dec in self.list_decisions(project, status=None):
            if dec.id == decision_id:
                return dec
        return None
