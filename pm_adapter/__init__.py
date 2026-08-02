from .adapter import Adapter, load_schema
from .rag import to_rag
from .events import to_events

# Public name for the semantic description mode. RAG is the current
# implementation; other retrieval backends can expose the same name.
to_semantic = to_rag
