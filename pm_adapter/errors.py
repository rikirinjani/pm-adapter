"""Exceptions of PM-Adapter v2. Every failure is an explicit error; the adapter never repairs, guesses or truncates."""
from __future__ import annotations


class PMAdapterError(Exception):
    """Base class of every error raised by the adapter itself."""


class SchemaError(PMAdapterError):
    """The schema is malformed (R1)."""


class ValidationError(PMAdapterError):
    """A value or a field is not allowed by the schema (R2). The adapter rejects; it never repairs."""

    def __init__(self, field, reason):
        super().__init__("%s: %s" % (field, reason))
        self.field = field
        self.reason = reason


class StoreError(PMAdapterError):
    """An illegal vault operation (R8 to R11)."""


class RetrievalError(PMAdapterError):
    """A retrieval call that cannot be answered as asked (R12, R13)."""


class MatchError(PMAdapterError):
    """The match step was called wrongly, for example without a token limit (R18)."""


class BudgetError(PMAdapterError):
    """The assembled packet does not fit its size budget (R22). Nothing is truncated; ``overflow`` names what did not fit."""

    def __init__(self, budget, size, overflow):
        names = ", ".join("%s (%d chars)" % (o["part"], o["size"]) for o in overflow)
        super().__init__("packet needs %d chars, budget is %d; did not fit: %s" % (size, budget, names))
        self.budget = budget
        self.size = size
        self.overflow = overflow


class LegacyUnavailable(PMAdapterError, ImportError):
    """The Morse frame decoder needs the optional ``pro-memoria`` package, which is not installed."""
