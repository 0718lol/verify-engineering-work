"""Apply partial updates to persisted profile records."""


def apply_profile_patch(current, patch):
    updated = dict(current)
    if "name" in patch:
        updated["name"] = patch["name"]
    if patch.get("bio") is not None:
        updated["bio"] = patch["bio"]
    return updated
