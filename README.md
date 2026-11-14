# PM-Adapter v2: the adapter owns the state

This branch adds a second version of the adapter next to the original. The original code, tests, scripts and README are kept
untouched under `legacy_v1/` and `docs/README_V1.md`; the v2 package is `pm_adapter/` at the repository root. The specification the
build followed is `docs/SPEC_V2.md`.

**What it is.** A small library a harness uses to *own the state* of an agent run: validate the packet, keep an append-only event log, derive
the decision fields, keep a durable store of confirmed rules (and remove what is superseded), retrieve by key, build the match question for the
cases without a key, and assemble the bounded packet for the next worker. Python standard library only. No model client, no network, no LLM
call anywhere in the package: every function is a lookup or a computation. The one model step (the match call) is a function **you** pass in.

**Evidence status (read this before relying on any claim below).** The design follows the author's unpublished discovery runs on one
synthetic task family, with a small number of models and a small rule set. The results are preliminary and not independently replicated.
Growth and transfer (accumulated general intelligence) are untested. Stores beyond the table limit without a clean key are outside the
tested range. This README gives the reasons in plain words and quotes no result figures.

**One later run.** The adapter has been used as the harness in one model-in-the-loop run by the author on the same task family; the
results are unpublished.

**Why these jobs.** In the author's discovery runs these jobs failed when left to the model and worked when the harness did them. This
package is the harness side of that finding, for the task families that were tested. It is not a general memory system.

## What stays from the original adapter
* **Separate from PM-1** (PM-1 carries state, the adapter owns what it means), **schema-driven**, **deterministic**, **zero LLM calls**.
* **The four output modes keep their names:** `to_english`, `to_json`, `to_semantic`, `to_events` (as `Adapter` methods, and as the
  original module-level functions with the original signatures). `Adapter`, `load_schema` also keep their names.
* **The Morse frame decoder stays**, as `pm_adapter.legacy` (`LegacyDecoder`, also importable as `pm_adapter.legacy.Adapter`). `Adapter`
  still accepts a frame string wherever the original did. The original test suite is carried over (`tests/test_legacy.py`).
* **`pro-memoria` is optional.** Only decoding frames needs it; without it those calls raise `LegacyUnavailable` (an `ImportError`
  subclass with an install hint) and everything else works. Tested in a subprocess with the package blocked.

## What changes
The adapter moves from "expand a frame into text" to "own the state". The default input is a plain JSON packet, not a frame.

## Quick start
```python
from pm_adapter import Adapter, load_schema
a = Adapter(load_schema("vault_demo"), measurement_sources={"test-runner"})
a.init_state({"phase": "act"}, source="agent")                       # validated: unknown fields and values outside the vocabulary are rejected
a.notice("freeze", source="scheduler", from_hop=50, to_hop=60)       # the raw schedule goes in the log, never into the packet
e = a.store_rule("mod", "return x % 13", source="agent")             # unverified: not returned yet
a.confirm(e.id, evidence="test.mod_01.pass", source="test-runner",           # confirmed only by an explicit call naming evidence: an opaque id (R3a);
          note="lib/mod_01.py passed")                                       # descriptive text goes in the note, which only the log keeps
r = a.retrieve("mod")                                                # exact key: the current confirmed entry
packet = a.assemble("lib/mod_02.py", hop=55, delivered=r.entries)    # derived freeze_active, the rule marked "applies to lib/mod_02.py"
```
`examples/teach_once.py` walks one rule through event, confirmation, store write, retrieval by key and packet assembly, then a superseding rule
(printed output: `examples/teach_once_output.txt`). Run the tests with `python -m unittest discover -s tests -v` from the repository root (standard library;
`pytest` also works).

## Components
| Module | Job | Requirements |
|---|---|---|
| `schema.py` | field types, closed vocabularies, roles (`decision`, `transient`, `invariant`), store-key vocabulary; validation that never repairs | R1, R2 |
| `log.py` | append-only event log; types `state_init`, `state_change`, `notice`, `rule_stored`, `rule_confirmed`, `rule_superseded`; sequence numbers, source, provenance, evidence | R3, R4, R5 |
| `derived.py` | registry of pure functions; shipped example `freeze_active` | R6, R7 |
| `store.py` | entries as a projection of the log: unverified, confirmed, superseded | R8, R9, R10, R11 |
| `adapter.py` | the `Adapter` class: events, state fold, vault writes, retrieval, match, assembly, output modes | R3 to R15, R21 to R23 |
| `match.py` | builds the match question, parses the answer strictly; the model call is the caller's | R16 to R20 |
| `packet.py` | the packet, the size budget, the error that names what did not fit | R21, R22, R23 |
| `legacy.py` | the original Morse frame decoder and the original module-level functions | section 1 of the spec |

## Requirements, their spec tags and the reason behind each
Tags are the spec's: **supported** = by a discovery run, **partly**, **untested**; "design" = a design rule the spec gives no tag.
"Built" says what this package does; every requirement below is built. The last column gives the reason in plain words; the figures
behind it are unpublished and are not quoted.

| R | Built | Spec tag | Reason |
|---|---|---|---|
| R1 schema: name, type, vocabulary, role | `schema.py`; roles decide what reaches the packet (decision yes, transient never, invariant only as the task-spec reference) | supported | the decisions followed two fields; a large transient field hardly changed them |
| R2 validation rejects unknown fields and out-of-vocabulary values, never repairs | `Schema.validate_fields`; no coercion (a string is not an int, a bool is not an int, "Act" is not "act") | supported | the model followed a wrong field value, so a wrong value must not reach the packet |
| R3 provenance label and evidence id; `measured` only by the adapter | the label is decided by the adapter: `measured` only when the event's source is a configured `measurement_sources` entry **and** the event names evidence, otherwise `claimed`; callers cannot pass a label | partly | on synthetic items a wrong `measured` label was trusted far more often than no label; not reproduced on real traces |
| R3a the evidence shown in a packet is an opaque id; descriptive text stays in the log | `check_evidence` in `schema.py`: an evidence id must be **a short token: 1 to 40 characters from letters, digits, dot, dash, underscore and colon, no spaces**; every writer (`init_state`, `change_state`, `notice`, `store_rule`, `confirm`, `store_confirmed`) refuses anything else with `ValidationError`; the optional `note=` argument is stored in the event log only and is never read into a packet | design (added after a validation replay) | in a replay during the build a descriptive evidence string put an earlier ticket's file name into the packet on every test ticket; with an opaque id it did not |
| R4 append-only log, event types, sequence number, source, provenance | `EventLog`: append and read-copy only | design | none needed; the type list adds `rule_stored` (see "Deviations and conflicts") |
| R5 current state is a fold over the log | `Adapter.state()`, entries are an incremental projection of the log | supported as a design consequence | follows from R6 and R9; not tested as a component |
| R6 derived decision fields from the log; the packet holds the derived value | `DerivedRegistry`, `freeze_active`; a derived field cannot be written by a caller; the raw schedule never enters the packet | supported | a raw schedule in the packet led to violations; the derived field did not |
| R7 a derived field records function and events | every derived entry has `function` and `events` (the sequence numbers it used) | supported (with R6) | as R6 |
| R8 entries: key (closed vocabulary), text, status, provenance, write sequence number, `supersedes`; only the adapter writes | `Entry` (frozen); all writes go through `store_rule`, `confirm`, `store_confirmed` | supported | harness-written lessons kept knowledge where agent-written lessons lost it |
| R9 supersession: the older entry is marked superseded, never returned again, stays in the log | `confirm` appends `rule_superseded`; retrieval and delivery exclude superseded entries | supported | a stale line was picked often with no cue, less often with a label, still sometimes with provenance tags, and never when the harness removed it |
| R9a supersession is by write order; the caller must confirm only verified facts | documented in the `confirm` and `store_confirmed` docstrings and here; a test shows a later confirmed stale write winning | design (added after a validation replay) | in a replay control, stale entries written after the teaching were delivered on most tickets. **The adapter cannot tell an old truth from a new error: the newest confirmed entry under a key wins. The caller's duty is to confirm only verified facts, in the order in which they became true.** |
| R10 unverified entries stored, not returned unless asked | `include_unverified=True` | partly | stale entries with no cue were picked, and look-alike unverified lines were sometimes picked by the match step. That excluding unverified entries fixes this is **untested** |
| R11 an entry becomes confirmed only through an explicit adapter call naming the evidence | `Adapter.confirm(entry_id, evidence=..., source=..., note=None)`; empty evidence is refused (`StoreError`) and a descriptive one too (`ValidationError`, R3a) | design | none |
| R12 retrieval by exact key is the default | `retrieve(key)` | supported | exact-key lookup found the right entry every time, including in a large store |
| R13 no clean key and at most `table_limit` (**default 24 lines offered**) lines offered to the match step: whole table plus a match request | `retrieve(item=...)`; `DEFAULT_TABLE_LIMIT = 24` | supported | it worked on a small rule set in a small table (that table also held duplicate lessons) |
| R14 more than `table_limit` **lines offered** without a key (confirmed entries, plus unverified ones if the caller asked for them): works, with the flag `outside_tested_range` | `Retrieval.flag` counts the lines offered, not the confirmed entries (corrected after a replay) | partly | with a long table of look-alike lines the match usually found the true rule but sometimes named a wrong line; beyond the tested range **untested** |
| R15 no similarity search in the default path; `to_semantic` kept, documented as not recommended | none in the default path; `to_semantic` kept and documented | supported | similarity retrieval lost the rule as the store grew (newer writes crowd it out) and did not bridge a paraphrased key; index words written at storage time did not help |
| R16 the adapter builds the question and parses the answer; the model call is the caller's | `MatchRequest`, `run_match`; no model client imported, checked by a test that scans the package's imports | design | none |
| R17 accept only exactly one existing entry name; empty, cut, unknown or several names deliver no rule; never a guess | `parse_reply`; one pair of surrounding quotes and whitespace is tolerated, nothing else is repaired | supported | a wrongly marked line was worse than delivering no rule |
| R18 the caller sets the token limit; no default | `max_tokens` is a required keyword; zero, negative, non-int and bool are refused | supported | several runs lost whole arms to limits set too low. Reply length depends on the model and on the number of lines; measure it at your real input size before setting the limit |
| R19 optional `verify` hook before delivery | `match(..., verify=...)`; a false answer gives status `rejected` and no rule | untested | none |
| R20 documentation: a "none" answer is not reliable | `NONE_ANSWER_IS_RELIABLE = False`, the `pm_adapter.match` docstring and this README | (documentation) | when no line applied, the model named one anyway in most finished calls |
| R21 assembled packet: task-spec reference, derived decision fields, delivered entries marked "applies to" with provenance | `Adapter.assemble`; the entry is re-read from the store at assembly (a caller cannot forge text) | supported | the default design (key query, superseded entries removed, provenance tags) succeeded on every test ticket on two models |
| R22 packet size budget; overflow raises an error naming what did not fit; never truncates | `BudgetError` with `.overflow` (parts in priority order: task spec, decision fields, store entries); default budget 20,000 characters, settable | partly | the evidence is a finding in the original project's README, not a discovery run; not tested in the discovery runs |
| R23 the worker receives only the packet and the task specification; no way to attach a transcript | `assemble` has no such argument (tested by signature and by refusing extra keywords); the adapter has no attach, transcript or history method | design | none |

## Output modes
`to_json(packet)` the assembled packet as a dict; `to_english(packet)` the same content as sentences; `to_events()` the event log or a slice
(`since`, `until` by sequence number); `to_semantic` unchanged and not recommended for retrieval (R15). Each also takes a frame string or a
plain mapping: a frame string goes to the legacy decoder, a mapping is validated against the schema.

## Deviations and conflicts with the spec
1. **R4 has no event type for storing an unverified entry**, but R8 and R10 need one. Added `rule_stored`. Proposed spec change: add it to R4.
2. **Event name.** R4 says `state_change` "as in the original `to_events`"; the original emits `value_change`. The v2 log and `Adapter.to_events()` use `state_change`;
   frame events (`to_events(frame)`, the module-level `to_events`) keep the original `value_change` so the carried-over tests hold. Proposed: say so in the spec.
3. **`values` format.** Original schemas give `values` as a dict (raw byte to label); v2 schemas give a list (a closed vocabulary). An original schema is recognised by its
   `state_width` and `byte` fields and works for frames only; v2 features on it raise a clear error.
4. **R3 wording.** "A field may be labelled `measured` only by the adapter, from an event that records the measurement" is built as: a configured measurement source plus named
   evidence. That is an interpretation; the spec does not say how the adapter knows an event records a measurement.
5. **R15 evidence wording.** The spec's evidence for R15 is stated by lag, not by store size alone; this README uses that wording.
6. **R22 budget unit.** The spec gives no unit; characters of the packet's JSON are used, with a default of 20,000 that the caller can change.

## Changes after a validation replay (2026-11-14)
A free replay of one experiment's logic through the adapter, run during the build, led to four changes to the spec and the adapter:
1. **R14, the flag counts lines offered** to the match step (confirmed entries, plus unverified ones when asked for), not confirmed entries. Test changed: `test_the_flag_counts_confirmed_entries_only`
   (it asserted no flag with 13 lines offered, 12 confirmed) is replaced by `test_the_flag_counts_lines_offered_including_unverified_ones_when_asked_for`.
2. **R13, the default `table_limit` is 24 lines offered** (was 12). Tests changed: `test_table_at_or_below_the_limit_has_no_flag_and_a_match_request` now expects the default 24, and
   `test_above_the_limit_it_still_works_but_carries_the_flag` is replaced by `test_exactly_the_limit_has_no_flag_one_more_line_raises_it` (24 lines no flag, 25 lines flag).
3. **R3a, evidence is an opaque id.** The rule: a short token of 1 to 40 characters from letters, digits, dot, dash, underscore and colon (no spaces); anything else is refused with
   `ValidationError`. Descriptive text goes in the `note=` argument, which only the event log keeps. Tests and example updated (the old tests used strings like
   "test:lib/mod_01.py passed"); four new tests in `EvidenceIdTests`.
4. **R9a, write order.** Documented in the `confirm` and `store_confirmed` docstrings and here: the newest confirmed entry under a key wins; the adapter cannot tell an old truth from a new error, so the
   caller must confirm only verified facts, in the order in which they became true. New test: `test_supersession_is_by_write_order_a_later_confirmed_stale_write_wins`.
After the changes, the replay's only moved clause was the flag: it is now raised on every ticket when unverified entries are asked for.

## Not carried over from the original repository, and why
The original also contains a decision store, session and project reducers, a state reducer, an encoder, a dispatcher (`dispatch.py`) and a status tool. They write under
`~/.config/opencode/...`, and the dispatcher starts an agent run, which conflicts with "standard library only, no network, zero model calls". They are not part of v2; the
original is kept untouched in `legacy_v1/`. Their tests (`legacy_v1/tests/test_validation.py`, 11 scenario tests over those modules) are not run by the v2 test suite; their ideas that v2 re-implements
without the filesystem are supersession (R9) and state as a fold over a log (R5).

## What this does not cover (spec section 4)
Growth and transfer (accumulated general intelligence): no experiment yet. Fork and merge of state (Yggdrasil): no experiment yet. Stores beyond the table limit without a clean key;
a second model for the match step; provenance on real traces. All evidence is from one synthetic task family per experiment and a small number of models.

## Tests
103 deterministic tests, standard library only, about one second: schema and validation (11), log, state, evidence ids and derived state including the freeze example (21), vault, supersession, write order, exact-key
lookup among 1,000 entries, the table limit and flag (20), match parsing and the no-model-client check (18), packet assembly, budget error, no-transcript and output modes (17), and the
legacy decoder (16: the original 12 tests carried over plus one on the module-level functions and three on the optional import). The legacy tests need `pro-memoria` and are skipped
without it; the original's two real-trace tests need traces in `~/self-harness/traces` and are skipped without them.
