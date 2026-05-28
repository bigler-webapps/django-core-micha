REDACTED_PLACEHOLDER = "***"


def redact_snapshot(snapshot: dict, fields: frozenset[str]) -> bool:
    if not isinstance(snapshot, dict):
        return False
    changed = False
    for field in fields:
        if field not in snapshot:
            continue
        if snapshot[field] in (None, "", REDACTED_PLACEHOLDER):
            continue
        snapshot[field] = REDACTED_PLACEHOLDER
        changed = True
    return changed


def redact_changes(changes: dict, fields: frozenset[str]) -> bool:
    if not isinstance(changes, dict):
        return False
    changed = False
    for field in fields:
        if field not in changes:
            continue
        entry = changes[field]
        if not isinstance(entry, dict):
            continue
        for key in ("from", "to"):
            current = entry.get(key)
            if current in (None, "", REDACTED_PLACEHOLDER):
                continue
            entry[key] = REDACTED_PLACEHOLDER
            changed = True
    return changed


def redact_metadata(metadata: dict, fields: frozenset[str]) -> None:
    for snap_key in ("before", "after"):
        redact_snapshot(metadata.get(snap_key) or {}, fields)
    redact_changes(metadata.get("changes") or {}, fields)
