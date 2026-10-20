"""Remove retired blog fields and promoted definitions, with a durable backup."""

import json
import os
from pathlib import Path

LEGACY_FIELDS = {"tags", "summary", "aiRefresh", "pinned"}
LEGACY_LABELS = LEGACY_FIELDS | {f"label:{name}" for name in LEGACY_FIELDS}
LEGACY_LABELS |= {f"child:{name}" for name in LEGACY_LABELS}


def cleanup_legacy_attributes(notes, delete_attribute, backup_path):
    planned = {}
    for note in notes:
        for attr in note.get("attributes") or []:
            if (attr.get("type") != "label" or attr.get("name") not in LEGACY_LABELS
                    or attr.get("noteId", note["noteId"]) != note["noteId"]):
                continue
            attribute_id = attr["attributeId"]
            planned[attribute_id] = {**attr, "noteId": note["noteId"]}
    if not planned:
        return 0

    path = Path(backup_path)
    # A corrupt backup must stop migration rather than lose the previous values.
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    saved.update(planned)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        os.chmod(temporary, 0o600)
        json.dump(saved, output, ensure_ascii=False, indent=2, sort_keys=True)
    temporary.replace(path)
    for attribute_id in planned:
        delete_attribute(attribute_id)
    return len(planned)
