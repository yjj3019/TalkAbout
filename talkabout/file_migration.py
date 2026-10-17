"""Explicit additive room JSONB migration; never run by app startup."""
from .domain import authorize, require
from .text_files import migrate_room


def migrate_selected_rooms(store, human_token, room_ids):
    require(isinstance(room_ids,list) and len(room_ids)<=64
            and all(isinstance(r,str) for r in room_ids) and len(set(room_ids))==len(room_ids),
            'invalid_migration_rooms',422)
    migrated=[]
    for rid in room_ids:
        with store.transaction(rid,human_token) as (actor,room):
            authorize(actor,room,'room:control',True)
            migrate_room(room)
            migrated.append(rid)
    return {'schema_version':1,'rooms':migrated}
