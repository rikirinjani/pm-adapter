def to_rag(decoded: dict, schema: dict) -> str:
    """Generate dense, searchable natural language description for RAG embedding.
    Optimized for semantic retrieval: rich field names, natural phrasing.

    This is the RAG implementation of to_semantic(). Other implementations
    (graph memory, keyword index, hybrid retrieval) can be added as separate
    modules.
    """
    parts = []
    for field in schema["fields"]:
        name = field["name"].replace("_", " ")
        label = decoded[field["name"]]["label"]
        parts.append(f"{name} set to {label}")
    return "Agent state: " + ", ".join(parts)
