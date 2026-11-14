"""Match step (R16 to R20). The adapter builds the question and parses the answer; the model call is a function the caller supplies.

This module imports no model client and makes no network call. It never repairs, guesses or falls back:

* The answer is accepted only if it is exactly one existing entry name (R17). An empty reply, a reply cut at the token limit, the word
  "none", an unknown name, or a reply naming more than one line all mean that no rule is delivered.
* The caller must set the token limit for the model call; there is no default (R18). Reply length depends on the model and on the
  number of lines; measure it at your real input size before setting the limit. In the author's runs, limits set too low cost whole runs.
* A "none" answer is NOT reliable (R20). In the author's runs, when no line applied the model named a line anyway in most finished
  calls. Do not build a design that relies on "none" to filter a long list.
"""
from __future__ import annotations

from .errors import MatchError, RetrievalError

NONE_ANSWER_IS_RELIABLE = False        # R20, machine-readable
MATCH_QUESTION = "Which one of these lines applies to this item? Answer with the line's name only, or none."

MATCHED, NONE, EMPTY, CUT, UNKNOWN, REJECTED = "matched", "none", "empty", "cut", "unknown", "rejected"


class MatchRequest:
    """The question for one item over a list of entries. ``names`` are the answers the parser will accept (plus "none")."""

    def __init__(self, item, entries):
        if not isinstance(item, str) or not item.strip():
            raise RetrievalError("a match request needs the item text")
        self.item = item
        self.entries = tuple(entries)
        keys = [e.key for e in self.entries]
        self._name_of = {e.id: (e.key if keys.count(e.key) == 1 else e.id) for e in self.entries}   # an entry id names a line only if its key repeats
        self.names = tuple(self._name_of[e.id] for e in self.entries)
        self._by_name = {self._name_of[e.id]: e for e in self.entries}

    def name_of(self, entry):
        return self._name_of[entry.id]

    def entry_named(self, name):
        return self._by_name.get(name)

    def prompt(self) -> str:
        lines = "\n".join("- %s: %s" % (self._name_of[e.id], e.text) for e in self.entries)
        return "Item: %s\n\nLines:\n%s\n\n%s" % (self.item, lines, MATCH_QUESTION)


class MatchResult:
    """What came back. ``entry`` is the delivered line, or None (no rule). ``status`` says why."""

    def __init__(self, status, name=None, entry=None, raw=None, finish_reason=None):
        self.status, self.name, self.entry, self.raw, self.finish_reason = status, name, entry, raw, finish_reason

    @property
    def rule_delivered(self):
        return self.status == MATCHED and self.entry is not None

    def __repr__(self):
        return "MatchResult(status=%r, name=%r)" % (self.status, self.name)


def _unwrap(reply):
    """A model function may return a str, a mapping with text and finish_reason, or an object with those attributes."""
    if reply is None:
        return "", None
    if isinstance(reply, str):
        return reply, None
    if isinstance(reply, dict):
        return (reply.get("text") or ""), reply.get("finish_reason")
    return (getattr(reply, "text", "") or ""), getattr(reply, "finish_reason", None)


def parse_reply(reply, request: MatchRequest, finish_reason=None) -> MatchResult:
    """Apply R17 to one reply. ``reply`` may be a str, a mapping or an object (see _unwrap)."""
    text, fr = _unwrap(reply)
    fr = finish_reason or fr
    if fr == "length":
        return MatchResult(CUT, raw=text, finish_reason=fr)
    if not text.strip():
        return MatchResult(EMPTY, raw=text, finish_reason=fr)
    t = text.strip()
    for q in ("'", '"', "`"):                       # one pair of surrounding quotes is tolerated; nothing else is repaired
        if len(t) >= 2 and t[0] == q and t[-1] == q:
            t = t[1:-1].strip()
            break
    if t.lower() == "none":
        return MatchResult(NONE, raw=text, finish_reason=fr)
    entry = request.entry_named(t)
    if entry is None:
        return MatchResult(UNKNOWN, raw=text, finish_reason=fr)
    return MatchResult(MATCHED, name=t, entry=entry, raw=text, finish_reason=fr)


def run_match(request: MatchRequest, call_model, *, max_tokens, verify=None) -> MatchResult:
    """Ask the caller's model function and parse the answer.

    ``call_model(prompt, max_tokens)`` is supplied by the caller and returns a str, a mapping or an object (see _unwrap). Errors it raises
    propagate: the adapter does not turn a failed call into a guess. ``max_tokens`` is required, a positive int, with no default (R18).
    ``verify(entry) -> bool`` is an optional caller-supplied check on the named entry before delivery (R19, untested in the discovery runs).
    """
    if type(max_tokens) is not int or max_tokens <= 0:
        raise MatchError("max_tokens must be a positive int chosen by the caller (there is no default)")
    if not callable(call_model):
        raise MatchError("call_model must be a function (prompt, max_tokens) -> reply")
    result = parse_reply(call_model(request.prompt(), max_tokens), request)
    if result.status == MATCHED and verify is not None and not verify(result.entry):
        return MatchResult(REJECTED, name=result.name, entry=None, raw=result.raw, finish_reason=result.finish_reason)
    return result
