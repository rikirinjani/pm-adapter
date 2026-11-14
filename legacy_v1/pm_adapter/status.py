#!/usr/bin/env python3
"""
pm-adapter state inspector — observability window into the context compiler.

Usage:
    python -m pm_adapter.status --project pm-adapter
    python -m pm_adapter.status --project pm-adapter --verbose
    python -m pm_adapter.status --project pm-adapter --packet
    python -m pm_adapter.status --all
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime

# Add parent to path for imports
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from pm_adapter.encoder import detect_project
from pm_adapter.state_reducer import StateReducer
from pm_adapter.session_reducer import SessionReducer, SessionState
from pm_adapter.project_reducer import ProjectReducer
from pm_adapter.decision_store import DecisionStore

LOG_DIR = Path(os.path.expanduser("~/.config/opencode/pm1-logs"))
SESSION_DIR = Path(os.path.expanduser("~/.config/opencode/pm1-sessions"))
DECISION_DIR = Path(os.path.expanduser("~/.config/opencode/pm1-decisions"))


def count_events(project: str | None = None) -> dict:
    """Count events from JSONL logs."""
    total = 0
    schemas = {}
    agents = {}
    errors = 0
    oldest_ts = None
    newest_ts = None

    for log_file in sorted(LOG_DIR.glob("*.jsonl")):
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Filter by project if specified
                if project:
                    meta = entry.get("meta", {})
                    entry_project = meta.get("project", "")
                    if entry_project and entry_project != project:
                        continue

                total += 1
                schema = entry.get("schema", "unknown")
                schemas[schema] = schemas.get(schema, 0) + 1

                meta = entry.get("meta", {})
                agent = meta.get("agent", "unknown")
                agents[agent] = agents.get(agent, 0) + 1

                if schema == "error":
                    errors += 1

                ts = entry.get("ts")
                if ts:
                    if oldest_ts is None or ts < oldest_ts:
                        oldest_ts = ts
                    if newest_ts is None or ts > newest_ts:
                        newest_ts = ts

    return {
        "total": total,
        "schemas": schemas,
        "agents": agents,
        "errors": errors,
        "oldest": datetime.fromtimestamp(oldest_ts).isoformat() if oldest_ts else "N/A",
        "newest": datetime.fromtimestamp(newest_ts).isoformat() if newest_ts else "N/A",
    }


def count_sessions(project: str | None = None) -> dict:
    """Count saved sessions."""
    sessions = []
    for sf in sorted(SESSION_DIR.glob("*.json")):
        try:
            with open(sf, "r", encoding="utf-8") as f:
                ss = json.load(f)
            if project and ss.get("project", "") != project:
                continue
            sessions.append(ss)
        except Exception:
            continue

    return {
        "total": len(sessions),
        "sessions": [
            {
                "id": ss.get("session_id", "?"),
                "project": ss.get("project", "?"),
                "outcome": ss.get("outcome", "?"),
                "tasks_completed": ss.get("tasks_completed", 0),
                "tasks_failed": ss.get("tasks_failed", 0),
                "cost_cents": ss.get("total_cost_cents", 0),
            }
            for ss in sessions[-10:]  # last 10
        ],
    }


def count_decisions(project: str | None = None) -> dict:
    """Count decisions from store."""
    if not DECISION_DIR.exists():
        return {"total": 0, "active": 0, "superseded": 0, "decisions": []}

    ds = DecisionStore(decision_dir=DECISION_DIR)
    decisions = ds.list_decisions(project) if project else []
    active = [d for d in decisions if d.status == "active"]
    superseded = [d for d in decisions if d.status == "superseded"]

    return {
        "total": len(decisions),
        "active": len(active),
        "superseded": len(superseded),
        "decisions": [
            {
                "id": d.id[:8],
                "decision": d.decision[:80],
                "domain": d.domain,
                "status": d.status,
                "tags": d.tags,
            }
            for d in decisions
        ],
    }


def preview_packet(project: str, task_domain: str = "") -> dict:
    """Preview what the context packet would look like for a new session."""
    try:
        reducer = ProjectReducer()
        packet = reducer.get_context_for_new_session(project, task_domain=task_domain)
        # Estimate tokens (rough: 4 chars per token)
        tokens = len(packet) // 4
        return {
            "packet": packet,
            "estimated_tokens": tokens,
            "char_count": len(packet),
        }
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="PM-1 State Inspector")
    parser.add_argument("--project", help="Filter by project name")
    parser.add_argument("--all", action="store_true", help="Show all projects")
    parser.add_argument("--verbose", action="store_true", help="Show detailed info")
    parser.add_argument("--packet", action="store_true", help="Preview context packet")
    parser.add_argument("--task-domain", default="", help="Task domain for packet preview")
    args = parser.parse_args()

    project = args.project or (None if args.all else detect_project())

    # Header
    print("=" * 60)
    print(f"PM-1 STATE INSPECTOR")
    print(f"Project: {project or '(all)'}")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 60)

    # Events
    print(f"\n--- Events ---")
    ev = count_events(project)
    print(f"  Total: {ev['total']}")
    print(f"  Errors: {ev['errors']}")
    print(f"  Oldest: {ev['oldest']}")
    print(f"  Newest: {ev['newest']}")
    if args.verbose:
        print(f"  By schema: {json.dumps(ev['schemas'], indent=4)}")
        print(f"  By agent: {json.dumps(ev['agents'], indent=4)}")

    # Sessions
    print(f"\n--- Sessions ---")
    ss = count_sessions(project)
    print(f"  Total: {ss['total']}")
    if args.verbose:
        for s in ss["sessions"]:
            print(f"    {s['id'][:20]}  {s['project']}  {s['outcome']}  "
                  f"ok={s['tasks_completed']} fail={s['tasks_failed']}  "
                  f"${s['cost_cents']:.2f}")

    # Decisions
    print(f"\n--- Decisions ---")
    dc = count_decisions(project)
    print(f"  Total: {dc['total']}  Active: {dc['active']}  Superseded: {dc['superseded']}")
    if args.verbose:
        for d in dc["decisions"]:
            print(f"    [{d['status']}] {d['id']}  domain={d['domain']}  "
                  f"tags={d['tags']}")
            print(f"      {d['decision']}")

    # Packet preview
    if args.packet and project:
        print(f"\n--- Context Packet Preview ---")
        pp = preview_packet(project, args.task_domain)
        if "error" in pp:
            print(f"  Error: {pp['error']}")
        else:
            print(f"  Estimated tokens: {pp['estimated_tokens']}")
            print(f"  Character count: {pp['char_count']}")
            print(f"\n{pp['packet']}")
    elif args.packet and not project:
        print(f"\n  (packet preview requires --project)")

    print()


if __name__ == "__main__":
    main()
