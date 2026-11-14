# PM-Adapter v2: specification (2026-11-14)

Status: the specification the v2 code in this branch was built from. Where the build corrected the draft, the requirement says so
("corrected 2026-11-14"). Nothing here touches the `pro-memoria` repository. The original adapter is kept untouched under `legacy_v1/`.

Each requirement carries a tag: **[supported]** by a discovery run, **[partly]** supported, or **[untested]**. The evidence comes from
unpublished discovery runs by the author (one synthetic task family per experiment, one to three models). Section numbers and codes
(T2, T3, T5) refer to the author's unpublished discovery notes; the numbers are preliminary.

## 1. What stays from the original adapter
The original concept is kept as the foundation:
- **Separate from PM-1.** PM-1 carries state; the adapter owns what the state means. The adapter is not part of PM-1.
- **Schema-driven.** Meaning lives in a schema outside the model. Domains supply their own schema.
- **Deterministic, zero LLM calls in the adapter.** Every function in the adapter is a pure lookup or computation.
- **The four output modes** (`to_english`, `to_json`, `to_semantic`, `to_events`) keep their names.
- **The Morse frame decoder** stays available as a legacy codec (`pm_adapter.legacy`), with its existing tests passing. The
  architecture no longer depends on it; the default input is now a plain JSON packet.

## 2. What changes, in one sentence
The adapter moves from "expand a frame into text" to "own the state": it validates the packet, derives the decision fields, keeps the
durable store, removes what is superseded, and assembles the bounded packet for the next worker.
Reason: every discovery experiment found that these jobs fail when left to the model and work when the harness does them
(unpublished discovery notes, section 1).

## 3. Components and requirements

### 3.1 Packet schema
- R1. A schema lists every packet field with: name, type, allowed values (a closed vocabulary where possible), and a role:
  `decision`, `transient` or `invariant`. **[supported]** The decision followed two fields; a transient field holding 44 percent of
  the packet's tokens changed 0.4 percent of decisions (T2).
- R2. Validation rejects unknown fields and values outside the vocabulary. It never guesses or repairs. **[supported]** A wrong
  field value was followed by the model 100 percent of the time (T2), so a wrong value must not reach the packet.
- R3. Every field carries a provenance label (`measured`, `derived`, `claimed`) and an evidence id. A field may be labelled
  `measured` only by the adapter, from an event that records the measurement. **[partly]** Supported on synthetic items: a wrong
  `measured` label was trusted in 84 to 93 percent of cases (T5). Not reproduced on real traces.

- R3a (added 2026-11-14 after the replay). The evidence shown in a packet is an opaque id (a short token without spaces). Any
  descriptive text about the evidence is kept in the event log only. The adapter refuses an evidence id that is not a short token.
  Reason: a descriptive evidence string carried an earlier ticket's file name into the packet at 36 of 36 test tickets.
- R9a (added 2026-11-14). Supersession is by write order: the newest confirmed entry under a key wins. The adapter cannot tell an
  old truth from a new error, so the caller must confirm only verified facts. In a replay control run during the build, stale entries written after the
  teaching were delivered at 24 of 36.

### 3.2 Event log
- R4. An append-only log of events. Event types: `state_init`, `state_change` (both as in the original `to_events`), plus
  `notice`, `rule_stored`, `rule_confirmed`, `rule_superseded`. Each event has a sequence number, a source and a provenance label.
  (Corrected 2026-11-14 after the build: `rule_stored` was missing and is needed by R8 and R10. The original adapter names its
  frame event `value_change`; the legacy frame path keeps that name, the v2 log uses `state_change`.)
- R5. Current state is computed by folding over the log. Nothing else is the source of truth. **[supported]** as a design
  consequence of R6 and R9; not tested as a component.

### 3.3 Derived state
- R6. A registry of pure functions computes decision fields from the event log. The packet holds the derived value, not the raw
  input it was derived from. Example to ship: "freeze active at this hop" computed from a schedule notice. **[supported]** A raw
  schedule pinned in the packet led to violations in 8 of 12 episodes; the derived field gave 0 of 12 (T3).
- R7. A derived field records which function and which events produced it (provenance `derived`).

### 3.4 Durable store (the vault)
- R8. Entries have: key (from the schema's closed vocabulary), text, status (`confirmed` or `unverified`), provenance, write
  sequence number, and `supersedes`. Only the adapter writes entries. **[supported]** Harness-written lessons kept knowledge where
  agent-written lessons lost it (E2: CPh against CP; V1).
- R9. Supersession: when a confirmed entry is written under a key that already has one, the older entry is marked superseded and
  is never returned again. It stays in the log for audit. **[supported]** Stale picks: 62 percent with no cue, 30 percent with a
  label, 12 to 16 percent with provenance tags, 0 percent when the harness removed the entry (section 9).
- R10. Unverified entries are stored but are not returned by retrieval unless the caller asks for them explicitly.
  **[partly]** Unlabelled wrong lessons were reused (section 9), and look-alike unverified lines were picked in 5 of 36 match calls
  (section 17). That excluding them fixes the problem is untested.
- R11. An entry becomes `confirmed` only through an explicit adapter call that names the evidence (for example a passing test).

### 3.5 Retrieval
- R12. Retrieval by exact key is the default and returns the current confirmed entry for that key. **[supported]** 36 of 36 at
  1,000 entries (section 8).
- R13. When the caller has no clean key and the number of lines offered to the match step is at most `table_limit` (default 24),
  retrieval returns the whole table and a match request (3.6). **[supported]** 36 of 36 with 12 distinct rules in a table of up to
  23 lines (section 14; corrected 2026-11-14 after the replay: the experiment's table also held duplicate test-ticket lessons, so
  the tested table was up to 23 lines, not 12).
- R14. When more than `table_limit` lines are offered without a clean key (confirmed entries, plus unverified ones if the caller
  asked for them), retrieval still works but returns a flag `outside_tested_range`. (Corrected 2026-11-14: the flag counts lines
  offered, not confirmed entries; the replay showed 100 offered lines raised no flag.) **[partly]** At
  100 lines with look-alikes the match found the true rule in 30 of 36 and named a wrong line in 5 (section 17). Above 100 lines:
  **[untested]** (sections 15 and 16 void).
- R15. No similarity search in the default path. `to_semantic` is kept for compatibility and documented as not recommended for
  retrieval. **[supported]** Similarity retrieval by ticket text at 1,000 entries delivered 10, 6 and 0 of 12 at lags 9, 51 and 150
  (section 8; corrected 2026-11-14: the numbers are by lag, the rule is crowded out by newer writes, not by store size alone) and 15 of 36 with a
  paraphrased key (section 13). Index words written at storage time did not help (13 of 36, section 14).

### 3.6 Match step
- R16. The adapter builds the match question ("which one of these lines applies to this item? answer with the line's name or
  none") and parses the answer. The model call itself is a function supplied by the caller. The adapter imports no model client and
  makes no network call.
- R17. The answer is accepted only if it is exactly one existing entry name. Anything else, including an empty or cut reply, means
  no rule is delivered. The adapter never falls back to a guess. **[supported]** A wrongly marked line made the worker fail in 5 of
  5 cases, which is worse than delivering no rule (section 17).
- R18. The caller must pass the token limit for the match call; there is no default. The documentation states the measured reply
  lengths (median about 2,100 tokens at 100 lines, up to about 12,000). **[supported]** Three runs lost arms to limits set too low
  (sections 13, 15, 16).
- R19. An optional `verify` hook: a caller-supplied check on the named entry before delivery. **[untested]**
- R20. The documentation states that a "none" answer is not reliable: when no line applied, the model named one anyway in most
  finished calls (section 16).

### 3.7 Packet assembly
- R21. The adapter assembles the outgoing packet: a reference to the invariant task specification, the decision fields (derived),
  and delivered store entries marked "applies to <target>" with their provenance. **[supported]** The default design (key query,
  superseded entries removed, provenance tags) scored 36 of 36 on two models (sections 11 and 12).
- R22. The packet has a size budget. If the content does not fit, assembly raises an error that names what did not fit. It never
  truncates silently. **[partly]** V0.6 of the main project found that budgeted selection can omit fields the model cannot recover
  from; not tested in the discovery runs, and that finding is cited from the original repository's README, not from the discovery notes.
  The build measures the budget in characters of the packet JSON (default 20,000, settable), because the spec gave no unit.
- R23. The worker receives only the packet and the task specification. The adapter offers no way to attach the transcript.

### 3.8 Output modes
- `to_json`: the assembled packet. `to_english`: the same content as sentences, for people. `to_events`: the event log or a slice
  of it. `to_semantic`: unchanged, see R15.

## 3.9 What the original code already had (found during the build, 2026-11-14)
This spec was first written from the original README only. The original code holds more than the frame decoder: a decision store with
supersession (`decision_store.py`), state computed as a fold over a log (`state_reducer.py`, session and project reducers), a
status module, a frame encoder, and `dispatch.py`, which starts an agent run. Those modules write under the user's opencode
configuration folder, and `dispatch.py` conflicts with "no model call, no network", so v2 re-implements supersession and the log
fold in memory and does not carry the others over. R8, R9 and R5 are therefore a continuation of ideas already in the original,
not additions.

## 4. Not covered, stated so nobody assumes it
- Growth and transfer (accumulated general intelligence): no experiment yet.
- Fork and merge of state (Yggdrasil): no experiment yet.
- Stores above 100 lines without a clean key; a second model for the match step; provenance on real traces.
- All evidence is from one synthetic task family per experiment and one to three models.

## 5. Build notes
- Python, standard library only, as in the original. No network, no model client.
- Layout: `pm_adapter/` (package), `tests/`, `examples/`, `README.md` at the repository root; the original adapter is in `legacy_v1/`.
- Tests are deterministic and mirror the free replays of the discovery runs: supersession (R9), exact-key lookup among 1,000
  entries (R12), table limit and flag (R13, R14), match parsing including empty, cut and unknown names (R17), budget error (R22),
  the freeze example (R6), validation (R2), and the legacy frame decoder.
- One example script walks a teach-once rule through: event, confirmation, store write, later retrieval by key, packet assembly;
  then a superseding rule, showing the old one is no longer returned.
