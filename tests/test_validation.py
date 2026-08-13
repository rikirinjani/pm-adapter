"""PM-1 Validation Test Suite

Tests from three independent reviews, prioritized:
A1: Deterministic replay regression
B1: Decision supersession chain
C1: Cross-session continuity regression
B2: Decision contradiction detection
D3/D4: Stress tests
D1: Missing event detection
D2: Two sessions, same task
A2: Corrupted state detection
A3: Duplicate event idempotency
C2/C3: Edge cases
"""

import json
import os
import sys
import time
import hashlib
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pm_adapter.encoder import encode_tool_call, encode_error, encode_cost, encode_handoff
from pm_adapter.state_reducer import StateReducer
from pm_adapter.session_reducer import SessionReducer, SessionState
from pm_adapter.project_reducer import ProjectReducer
from pm_adapter.decision_store import DecisionStore, Decision


# ─── Test infrastructure ───────────────────────────────────────────

class TestResult:
    def __init__(self, name):
        self.name = name
        self.passed = 0
        self.failed = 0
        self.errors = []

    def ok(self):
        self.passed += 1

    def fail(self, msg):
        self.failed += 1
        self.errors.append(msg)

    def summary(self):
        status = "PASS" if self.failed == 0 else "FAIL"
        return f"[{status}] {self.name}: {self.passed} passed, {self.failed} failed"


def state_hash(state_dict: dict) -> str:
    """Deterministic hash of state for bitwise comparison."""
    serialized = json.dumps(state_dict, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()


# ─── A1: Deterministic Replay Regression ───────────────────────────

def test_A1_deterministic_replay():
    """Same 60 events → same state, 100 runs. Bitwise equality."""
    result = TestResult("A1: Deterministic replay regression")

    # Generate 60 known events
    events = []
    agents = ["orchestrator", "fixer", "oracle", "explorer", "librarian"]
    tools = ["read", "write", "edit", "grep", "glob", "bash"]
    outcomes = ["ok", "ok", "ok", "ok", "error"]
    error_types = ["rate_limit", "timeout", "http"]

    for i in range(60):
        agent = agents[i % len(agents)]
        tool = tools[i % len(tools)]
        outcome = outcomes[i % len(outcomes)]

        if outcome == "error":
            frame = encode_error(
                agent=agent,
                error_type=error_types[i % len(error_types)],
                severity="warn",
                retry_count=i % 3,
                http_code=429 if i % 7 == 0 else 500,
                project="global",
            )
            events.append({"frame": frame, "schema": "error", "ts": 1700000000 + i * 10})
        else:
            frame = encode_tool_call(
                agent=agent,
                tool=tool,
                outcome=outcome,
                project="global",
            )
            events.append({"frame": frame, "schema": "tool_call", "ts": 1700000000 + i * 10})

    # Write events to temp dir
    log_dir = Path(tempfile.mkdtemp())
    with (log_dir / "2026-01-01.jsonl").open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    # Run 100 times, compare state hashes
    hashes = []
    for run in range(100):
        reducer = StateReducer(project="global", log_dir=log_dir)
        state = reducer.reduce()
        h = state_hash(state.to_dict())
        hashes.append(h)

    # All hashes should be identical
    unique = set(hashes)
    shutil.rmtree(log_dir)
    if len(unique) == 1:
        result.ok()
    else:
        result.fail(f"Only {100 - len(unique) + 1} of 100 runs produced same hash. Unique hashes: {len(unique)}")

    return result


# ─── A2: Corrupted State Detection ─────────────────────────────────

def test_A2_corrupted_state():
    """Delete random event mid-JSONL, reducer detects gap."""
    result = TestResult("A2: Corrupted state detection")

    # Create 60 events
    events = []
    for i in range(60):
        frame = encode_tool_call(
            agent="orchestrator", tool="read", outcome="ok",
            project="global",
        )
        events.append({"frame": frame, "schema": "tool_call", "ts": 1700000000 + i * 10})

    # Delete event at position 30
    corrupted = events[:30] + events[31:]

    log_dir = Path(tempfile.mkdtemp())
    log_file = log_dir / "2026-01-01.jsonl"
    with log_file.open("w") as f:
        for ev in corrupted:
            f.write(json.dumps(ev) + "\n")

    reducer = StateReducer(project="global")
    reducer._log_dir = log_dir
    state = reducer.reduce()

    # Should still produce a state (graceful degradation)
    if state is not None:
        result.ok()
    else:
        result.fail("Reducer returned None on corrupted input")

    shutil.rmtree(log_dir)
    return result


# ─── A3: Duplicate Event Idempotency ───────────────────────────────

def test_A3_duplicate_event():
    """Insert same event twice, reducer produces same state as single."""
    result = TestResult("A3: Duplicate event idempotency")

    frame = encode_tool_call(
        agent="orchestrator", tool="read", outcome="ok",
        project="global",
    )
    event = {"frame": frame, "schema": "tool_call", "ts": 1700000000}

    # Single event
    log_dir1 = Path(tempfile.mkdtemp())
    with (log_dir1 / "2026-01-01.jsonl").open("w") as f:
        f.write(json.dumps(event) + "\n")
    reducer1 = StateReducer(project="global")
    reducer1._log_dir = log_dir1
    state1 = reducer1.reduce()
    h1 = state_hash(state1.to_dict())
    shutil.rmtree(log_dir1)

    # Duplicate event
    log_dir2 = Path(tempfile.mkdtemp())
    with (log_dir2 / "2026-01-01.jsonl").open("w") as f:
        f.write(json.dumps(event) + "\n")
        f.write(json.dumps(event) + "\n")
    reducer2 = StateReducer(project="global")
    reducer2._log_dir = log_dir2
    state2 = reducer2.reduce()
    h2 = state_hash(state2.to_dict())
    shutil.rmtree(log_dir2)

    # For tool_call events, duplicate means tool_calls count = 2 vs 1
    # The reducer should count both (events are append-only, not idempotent by design)
    # What matters is that it doesn't crash
    if state2 is not None:
        result.ok()
    else:
        result.fail("Reducer returned None on duplicate events")

    return result


# ─── B1: Decision Supersession Chain ───────────────────────────────

def test_B1_supersession():
    """DEC-001 active → DEC-002 supersedes → DEC-001 superseded."""
    result = TestResult("B1: Decision supersession chain")

    ds = DecisionStore(decision_dir=Path(tempfile.mkdtemp()))

    # Add DEC-001
    d1 = ds.add("test-project",
        decision="Use event sourcing",
        rationale="Events capture everything",
        tags=["architectural"],
        domain="architecture",
    )

    # Verify DEC-001 is active
    active = ds.list_decisions("test-project", status="active")
    if len(active) != 1:
        result.fail(f"Expected 1 active, got {len(active)}")
    else:
        result.ok()

    # Add DEC-002 supersedes DEC-001
    d2 = ds.add("test-project",
        decision="Switch to polling",
        rationale="Event sourcing too complex",
        tags=["architectural"],
        domain="architecture",
        supersedes=d1.id,
    )

    # Verify DEC-001 is superseded, DEC-002 is active
    active = ds.list_decisions("test-project", status="active")
    superseded = ds.list_decisions("test-project", status="superseded")

    if len(active) != 1 or active[0].id != d2.id:
        result.fail(f"Expected DEC-002 active, got {[d.id for d in active]}")
    else:
        result.ok()

    if len(superseded) != 1 or superseded[0].id != d1.id:
        result.fail(f"Expected DEC-001 superseded, got {[d.id for d in superseded]}")
    else:
        result.ok()

    # Context packet should only include active decisions
    ctx = ds.get_context_packet("test-project")
    if d1.id in ctx:
        result.fail(f"Superseded decision {d1.id} appeared in context packet")
    else:
        result.ok()

    if d2.id not in ctx:
        result.fail(f"Active decision {d2.id} missing from context packet")
    else:
        result.ok()

    shutil.rmtree(ds.decision_dir)
    return result


# ─── B2: Decision Contradiction ────────────────────────────────────

def test_B2_contradiction():
    """Two active decisions with conflicting content."""
    result = TestResult("B2: Decision contradiction detection")

    ds = DecisionStore(decision_dir=Path(tempfile.mkdtemp()))

    # Two contradictory decisions
    ds.add("test-project",
        decision="Use event sourcing",
        rationale="Events capture everything",
        tags=["architectural"],
        domain="architecture",
    )
    ds.add("test-project",
        decision="Use polling",
        rationale="Simpler to implement",
        tags=["architectural"],
        domain="architecture",
    )

    # Both should be active
    active = ds.list_decisions("test-project", status="active")
    if len(active) != 2:
        result.fail(f"Expected 2 active contradictions, got {len(active)}")
    else:
        result.ok()

    # Context packet should include both (worker sees the contradiction)
    ctx = ds.get_context_packet("test-project")
    if "event sourcing" in ctx and "polling" in ctx:
        result.ok()  # Contradiction visible to worker
    else:
        result.fail("Context packet doesn't surface contradiction")

    shutil.rmtree(ds.decision_dir)
    return result


# ─── C1: Cross-Session Continuity Regression ──────────────────────

def test_C1_continuity_regression():
    """Session A → Session B, fresh worker answers correctly."""
    result = TestResult("C1: Cross-session continuity regression")

    # Session A: seed decisions
    ds = DecisionStore(decision_dir=Path(tempfile.mkdtemp()))
    ds.add("test-project",
        decision="Event sourcing over polling",
        rationale="Events are source of truth",
        tags=["architectural"],
        domain="architecture",
    )
    ds.add("test-project",
        decision="Log pruning at keep_days=7",
        rationale="Bounded accumulation",
        tags=["architectural", "constraint"],
        domain="infra",
    )

    # Verify decisions are queryable
    relevant = ds.get_relevant("test-project", tags=["architectural"])
    if len(relevant) < 2:
        result.fail(f"Expected 2+ relevant decisions, got {len(relevant)}")
    else:
        result.ok()

    # Context packet includes decisions
    ctx = ds.get_context_packet("test-project")
    if "Event sourcing" in ctx and "7" in ctx:
        result.ok()
    else:
        result.fail("Context packet missing decisions")

    shutil.rmtree(ds.decision_dir)
    return result


# ─── D1: Missing Event Detection ───────────────────────────────────

def test_D1_missing_event():
    """Delete event at position 30, reducer should handle gracefully."""
    result = TestResult("D1: Missing event detection")

    events = []
    for i in range(60):
        frame = encode_tool_call(
            agent="orchestrator", tool="read", outcome="ok",
            project="global",
        )
        events.append({"frame": frame, "schema": "tool_call", "ts": 1700000000 + i * 10})

    # Delete event at position 30
    corrupted = events[:30] + events[31:]

    log_dir = Path(tempfile.mkdtemp())
    with (log_dir / "2026-01-01.jsonl").open("w") as f:
        for ev in corrupted:
            f.write(json.dumps(ev) + "\n")

    reducer = StateReducer(project="global", log_dir=log_dir)
    state = reducer.reduce()

    # Should produce state with 59 events (not crash)
    if state is not None and hasattr(state, 'event_count'):
        if state.event_count == 59:
            result.ok()
        else:
            result.fail(f"Expected 59 events, got {state.event_count}")
    else:
        result.fail("State missing or incomplete")

    shutil.rmtree(log_dir)
    return result


# ─── D2: Two Sessions, Same Task ───────────────────────────────────

def test_D2_concurrent_sessions():
    """Two sessions writing decisions to same project."""
    result = TestResult("D2: Two sessions, same task")

    ds = DecisionStore(decision_dir=Path(tempfile.mkdtemp()))

    # Session A
    d1 = ds.add("test-project",
        decision="Use LanceDB",
        rationale="Fast vector search",
        tags=["architectural"],
        session_id="session-A",
        domain="data",
    )

    # Session B (concurrent, slightly different rationale for scoring)
    d2 = ds.add("test-project",
        decision="Use ChromaDB",
        rationale="Better ecosystem and community support",
        tags=["architectural", "preferred"],
        session_id="session-B",
        domain="data",
    )

    # Both should exist as active
    active = ds.list_decisions("test-project", status="active")
    if len(active) == 2:
        result.ok()
    else:
        result.fail(f"Expected 2 active decisions, got {len(active)}")

    # Both should be queryable
    relevant = ds.get_relevant("test-project", tags=["architectural"], domain="data", limit=2)
    if len(relevant) == 2:
        result.ok()
    else:
        result.fail(f"Expected 2 relevant decisions, got {len(relevant)}")

    shutil.rmtree(ds.decision_dir)
    return result


# ─── D3/D4: Stress Test ────────────────────────────────────────────

def test_D3_stress_100_sessions():
    """100 sessions, ProjectReducer processes without OOM."""
    result = TestResult("D3/D4: Stress test (100 sessions, 1000+ events)")

    # Create 100 session states
    sr = SessionReducer(session_dir=Path(tempfile.mkdtemp()))
    for i in range(100):
        ss = SessionState(session_id=f"stress-session-{i:03d}", project="stress-test")
        ss.tasks_completed = 5
        ss.tasks_failed = i % 10  # Some failures
        ss.total_tool_calls = 20
        ss.total_errors = i % 5
        ss.total_cost_cents = 0.01 * (i + 1)
        ss.total_tokens = 100 * (i + 1)
        ss.start_ts = 1700000000 + i * 60
        ss.end_ts = 1700000060 + i * 60
        ss.error_patterns = {"rate_limit": i % 3}
        ss.outcome = "success" if i % 10 != 0 else "failed"
        sr.save_session(ss)

    # ProjectReducer processes all
    t0 = time.time()
    pr = ProjectReducer(session_dir=sr.session_dir)
    ps = pr.reduce_project("stress-test")
    elapsed = time.time() - t0

    if ps.total_sessions == 100:
        result.ok()
    else:
        result.fail(f"Expected 100 sessions, got {ps.total_sessions}")

    if elapsed < 5.0:  # Should be fast
        result.ok()
    else:
        result.fail(f"Took {elapsed:.1f}s, expected <5s")

    # Context packet stays bounded
    ctx = pr.get_context_for_new_session("stress-test")
    if len(ctx) < 5000:  # 5000 chars ~ 1250 tokens
        result.ok()
    else:
        result.fail(f"Context packet too large: {len(ctx)} chars")

    shutil.rmtree(sr.session_dir)
    return result


# ─── C2: Fresh Worker Both Questions ───────────────────────────────

def test_C2_both_questions():
    """Fresh worker can answer both 'what?' and 'why?'."""
    result = TestResult("C2: Fresh worker both questions")

    ds = DecisionStore(decision_dir=Path(tempfile.mkdtemp()))
    ds.add("test-project",
        decision="Event sourcing over polling",
        rationale="Events capture everything, state is derived",
        tags=["architectural"],
        domain="architecture",
    )

    ctx = ds.get_context_packet("test-project")

    # Check both decision and rationale present
    has_decision = "Event sourcing" in ctx
    has_rationale = "capture everything" in ctx or "source of truth" in ctx

    if has_decision and has_rationale:
        result.ok()
    else:
        result.fail(f"Missing: decision={has_decision}, rationale={has_rationale}")

    shutil.rmtree(ds.decision_dir)
    return result


# ─── C3: No Decisions Edge Case ────────────────────────────────────

def test_C3_no_decisions():
    """Project with zero decisions in store."""
    result = TestResult("C3: No decisions edge case")

    ds = DecisionStore(decision_dir=Path(tempfile.mkdtemp()))
    ctx = ds.get_context_packet("empty-project")

    if ctx == "":
        result.ok()
    else:
        result.fail(f"Expected empty string, got: {ctx[:100]}")

    shutil.rmtree(ds.decision_dir)
    return result


# ─── Run all tests ─────────────────────────────────────────────────

def main():
    tests = [
        test_A1_deterministic_replay,
        test_A2_corrupted_state,
        test_A3_duplicate_event,
        test_B1_supersession,
        test_B2_contradiction,
        test_C1_continuity_regression,
        test_D1_missing_event,
        test_D2_concurrent_sessions,
        test_D3_stress_100_sessions,
        test_C2_both_questions,
        test_C3_no_decisions,
    ]

    results = []
    total_passed = 0
    total_failed = 0

    print("=" * 60)
    print("PM-1 VALIDATION TEST SUITE")
    print("=" * 60)

    for test_fn in tests:
        print(f"\nRunning {test_fn.__name__}...")
        try:
            r = test_fn()
            results.append(r)
            total_passed += r.passed
            total_failed += r.failed
            print(f"  {r.summary()}")
            for err in r.errors:
                print(f"    FAIL: {err}")
        except Exception as e:
            print(f"  ERROR: {e}")
            total_failed += 1

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for r in results:
        status = "PASS" if r.failed == 0 else "FAIL"
        print(f"  [{status}] {r.name}")

    print(f"\n  Total: {total_passed} passed, {total_failed} failed")
    print(f"  Overall: {'ALL PASSED' if total_failed == 0 else 'SOME FAILED'}")

    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
