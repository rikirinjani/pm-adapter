def to_events(current: dict, previous: dict | None, schema: dict) -> list[dict]:
    """Detect state transitions and emit Hermes-compatible event dicts.
    Each event: {"event": "value_change", "field": "phase", "old": "idle", "new": "act"}
    """
    events = []
    if previous is None:
        # Initial state — emit full state event
        for field in schema["fields"]:
            name = field["name"]
            label = current[name]["label"]
            events.append({"event": "state_init", "field": name, "value": label})
        return events

    for field in schema["fields"]:
        name = field["name"]
        old_label = previous[name]["label"]
        new_label = current[name]["label"]
        if old_label != new_label:
            events.append({
                "event": "value_change",
                "field": name,
                "old": old_label,
                "new": new_label,
            })
    return events
