# PM-Adapter v2: specification (2026-11-14)

Status: the specification the v2 code in this branch was built from. Where the build corrected the draft, the requirement says so
("corrected 2026-11-14"). Nothing here touches the `pro-memoria` repository. The original adapter is kept untouched under `legacy_v1/`.

Each requirement carries a tag: **[supported]** by a discovery run, **[partly]** supported, **[untested]**, or **[design]** (a design
rule without a run behind it). The design follows the author's unpublished discovery runs on one synthetic task family, with a small
number of models and a small rule set. The results are preliminary and not independently replicated. Growth and transfer are
untested, and stores beyond the table limit without a clean key are outside the tested range. This document states the reasons in
plain words and quotes no result figures.

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
Reason: in the author's discovery runs these jobs failed when left to the model and worked when the harness did them.

## 3. Components and requirements

### 3.1 Packet schema
- R1. A schema lists every packet field with: name, type, allowed values (a closed vocabulary where possible), and a role:
  `decision`, `transient` or `invariant`. **[supported]** The decisions followed two fields; a large transient field hardly
  changed them.
- R2. Validation rejects unknown fields and values outside the vocabulary. It never guesses or repairs. **[supported]** The model
  followed a wrong field value, so a wrong value must not reach the packet.
- R3. Every field carries a provenance label (`measured`, `derived`, `claimed`) and an evidence id. A field may be labelled
  `measured` only by the adapter, from an event that records the measurement. **[partly]** Supported on synthetic items: a wrong
  `measured` label was trusted far more often than no label. Not reproduced on real traces.
- R3a (added 2026-11-14 after a validation replay during the build). The evidence shown in a packet is an opaque id (a short token
  without spaces; at most 40 characters from letters, digits, dot, dash, underscore and colon). Any descriptive text about the
  evidence is kept in the event log only. The adapter refuses an evidence id that is not a short token. **[design]** Reason: a
  descriptive evidence string carried an earlier ticket's file name into the packet on every test ticket of the replay.
- R9a (added 2026-11-14). Supersession is by write order: the newest confirmed entry under a key wins. The adapter cannot tell an
  old truth from a new error, so the caller must confirm only verified facts. **[design]** In a replay control run during the
  build, stale entries written after the teaching were delivered on most tickets.

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
  schedule pinned in the packet led to violations; the derived field did not.
- R7. A derived field records which function and which events produced it (provenance `derived`).

### 3.4 Durable store (the vault)
- R8. Entries have: key (from the schema's closed vocabulary), text, status (`confirmed` or `unverified`), provenance, write
  sequence number, and `supersedes`. Only the adapter writes entries. **[supported]** Harness-written lessons kept knowledge where
  agent-written lessons lost it.
- R9. Supersession: when a confirmed entry is written under a key that already has one, the older entry is marked superseded and
  is never returned again. It stays in the log for audit. **[supported]** A stale line was picked often with no cue, less often
  with a label, still sometimes with provenance tags, and never when the harness removed the entry.
- R10. Unverified entries are stored but are not returned by retrieval unless the caller asks for them explicitly.
  **[partly]** Unlabelled wrong lessons were reused, and look-alike unverified lines were sometimes picked by the match step. That
  excluding them fixes the problem is untested.
- R11. An entry becomes `confirmed` only through an explicit adapter call that names the evidence (for example a passing test).
  **[design]**

### 3.5 Retrieval
- R12. Retrieval by exact key is the default and returns the current confirmed entry for that key. **[supported]** It found the
  right entry every time, including in a large store.
- R13. When the caller has no clean key and the number of lines offered to the match step is at most `table_limit` (default 24),
  retrieval returns the whole table and a match request (3.6). **[supported]** It worked every time on a small rule set in a
  small table (corrected 2026-11-14 after a replay: the experiment's table also held duplicate lessons, so the tested
  table was larger than the number of distinct rules).
- R14. When more than `table_limit` lines are offered without a clean key (confirmed entries, plus unverified ones if the caller
  asked for them), retrieval still works but returns a flag `outside_tested_range`. (Corrected 2026-11-14: the flag counts lines
  offered, not confirmed entries.) **[partly]** With a long table of look-alike lines the match usually found the true rule but
  sometimes named a wrong line. Beyond the tested range: **[untested]**.
- R15. No similarity search in the default path. `to_semantic` is kept for compatibility and documented as not recommended for
  retrieval. **[supported]** Similarity retrieval lost the rule as the store grew (newer writes crowd it out; the lag matters, not
  store size alone) and did not bridge a paraphrased key. Index words written at storage time did not help.

### 3.6 Match step
- R16. The adapter builds the match question ("which one of these lines applies to this item? answer with the line's name or
  none") and parses the answer. The model call itself is a function supplied by the caller. The adapter imports no model client and
  makes no network call. **[design]**
- R17. The answer is accepted only if it is exactly one existing entry name. Anything else, including an empty or cut reply, means
  no rule is delivered. The adapter never falls back to a guess. **[supported]** A wrongly marked line was worse than delivering no
  rule.
- R18. The caller must set the token limit for the match call; there is no default. Reply length depends on the model and on the
  number of lines; measure it at your real input size before setting the limit. **[supported]** Several runs lost whole arms to
  limits set too low.
- R19. An optional `verify` hook: a caller-supplied check on the named entry before delivery. **[untested]**
- R20. The documentation states that a "none" answer is not reliable: when no line applied, the model named one anyway in most
  finished calls.

### 3.7 Packet assembly
- R21. The adapter assembles the outgoing packet: a reference to the invariant task specification, the decision fields (derived),
  and delivered store entries marked "applies to <target>" with their provenance. **[supported]** The default design (key query,
  superseded entries removed, provenance tags) succeeded on every test ticket on two models.
- R22. The packet has a size budget. If the content does not fit, assembly raises an error that names what did not fit. It never
  truncates silently. **[partly]** An earlier project found that budgeted selection can omit fields the model cannot recover from;
  not tested in the discovery runs. The build measures the budget in characters of the packet JSON (default 20,000, settable),
  because the spec gave no unit.
- R23. The worker receives only the packet and the task specification. The adapter offers no way to attach the transcript.
  **[design]**

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
- Stores beyond the table limit without a clean key; a second model for the match step; provenance on real traces.
- All evidence is from one synthetic task family per experiment and a small number of models.

## 5. Build notes
- Python, standard library only, as in the original. No network, no model client.
- Layout: `pm_adapter/` (package), `tests/`, `examples/`, `README.md` at the repository root; the original adapter is in `legacy_v1/`.
- Tests are deterministic and mirror the free replays of the discovery runs: supersession (R9), exact-key lookup among 1,000
  entries (R12), table limit and flag (R13, R14), match parsing including empty, cut and unknown names (R17), budget error (R22),
  the freeze example (R6), validation (R2), and the legacy frame decoder.
- One example script walks a teach-once rule through: event, confirmation, store write, later retrieval by key, packet assembly;
  then a superseding rule, showing the old one is no longer returned.
