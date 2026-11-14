"""PM-Adapter v2: schema-driven, deterministic, zero LLM calls; the adapter owns the state (see README.md and SPEC.md).

The original public names keep working: ``Adapter``, ``load_schema``, ``to_english``, ``to_json``, ``to_semantic``, ``to_events``.
The Morse frame decoder lives in ``pm_adapter.legacy`` and needs the optional ``pro-memoria`` package only when frames are decoded.
"""
__version__ = "2.0.0"

from .adapter import Adapter, DEFAULT_TABLE_LIMIT, OUTSIDE_TESTED_RANGE, Retrieval
from .derived import DerivedRegistry, freeze_active
from .errors import (BudgetError, LegacyUnavailable, MatchError, PMAdapterError, RetrievalError, SchemaError, StoreError,
                     ValidationError)
from .legacy import to_english, to_events, to_json, to_rag
from .log import EVENT_TYPES, EventLog
from .match import NONE_ANSWER_IS_RELIABLE, MatchRequest, MatchResult, parse_reply, run_match
from .packet import Packet
from .schema import Schema, load_schema
from .store import Entry

# Public name for the semantic description mode (the original alias). Not recommended for retrieval; see Adapter.to_semantic.
to_semantic = to_rag
