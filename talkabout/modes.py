"""Deterministic text-only mode policy. No model judge or autonomous tools."""
PHASES = {"bounded_rounds": ("proposal", "review", "synthesis"),
          "co_creation": ("researcher", "creator", "reviewer")}


def valid_turn_request(room, entry):
    """A pending request is authority only in its exact originating context."""
    fields = {'principal_id', 'generation', 'membership_epoch', 'membership_revision'}
    if not isinstance(entry, dict) or set(entry) != fields:
        return False
    pid = entry['principal_id']
    if not isinstance(pid, str) or not pid:
        return False
    if not all(type(entry[k]) is int and entry[k] > 0 for k in
               ('generation', 'membership_epoch', 'membership_revision')):
        return False
    member = room.get('members', {}).get(pid)
    return (isinstance(member, dict) and member.get('status') == 'active'
            and entry['generation'] == room.get('generation')
            and entry['membership_epoch'] == member.get('epoch')
            and entry['membership_revision'] == member.get('revision'))


def next_turn(room, eligible):
    progress = room.setdefault("mode_progress", {"round": 1, "phase": 0, "remaining": None})
    if room["mode"] == "free_discussion":
        requests = room.setdefault("turn_requests", [])
        valid = [entry for entry in requests if valid_turn_request(room, entry)]
        available = [pid for pid in eligible if any(entry['principal_id'] == pid for entry in valid)]
        if not available:
            return None
        ordered = eligible[room["next_agent"] % len(eligible):] + eligible[:room["next_agent"] % len(eligible)]
        pid = next(p for p in ordered if p in available)
        room["next_agent"] = (eligible.index(pid) + 1) % len(eligible)
        requests.remove(next(entry for entry in valid if entry['principal_id'] == pid))
        return pid, "requested_turn", "free", progress["round"]
    phases = PHASES[room["mode"]]
    while True:
        if progress["phase"] >= len(phases):
            if room["mode"] == "co_creation":
                room["mode_status"] = "awaiting_adoption"
                return None
            if progress["round"] >= room["limits"].get("rounds", 1):
                room["mode_status"] = "round_limit"
                return None
            progress.update(round=progress["round"] + 1, phase=0, remaining=None, issued=[])
        if progress["remaining"] is None:
            progress["remaining"] = list(eligible)
            progress["issued"] = []
        # A newly accepted agent gets the current phase opportunity before it closes.
        for pid in eligible:
            if pid not in progress["remaining"] and pid not in progress["issued"]:
                progress["remaining"].append(pid)
        progress["remaining"] = [pid for pid in progress["remaining"] if pid in eligible]
        if progress["remaining"]:
            pid = progress["remaining"].pop(0)
            progress["issued"].append(pid)
            phase = phases[progress["phase"]]
            return pid, phase, phase, progress["round"]
        progress.update(phase=progress["phase"] + 1, remaining=None, issued=[])
