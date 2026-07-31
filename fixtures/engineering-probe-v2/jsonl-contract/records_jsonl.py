"""Serialize records using the legacy line format."""


def dump_records(records):
    return "\n".join(
        f"{record['id']}|{record.get('note', '')}" for record in records
    )


def load_records(text):
    if not text:
        return []
    records = []
    for line in text.splitlines():
        record_id, note = line.split("|", 1)
        records.append({"id": record_id, "note": note})
    return records
