"""Legacy user-record migration."""


def migrate_user(record):
    migrated = dict(record)
    name = migrated.pop("name", "")
    first_name, _, last_name = name.partition(" ")
    migrated["first_name"] = first_name
    migrated["last_name"] = last_name
    return migrated


def migrate_all(records):
    return [migrate_user(record) for record in records]
