"""Deterministic text-only mode policy. No model judge or autonomous tools."""
PHASES = {"bounded_rounds": ("proposal", "review", "synthesis"),
          "co_creation": ("researcher", "creator", "reviewer")}


def next_turn(room, eligible):
    progress = room.setdefault("mode_progress", {"round": 1, "phase": 0, "remaining": None})
    if room["mode"] == "free_discussion":
        requests = room.setdefault("turn_requests", [])
        available = [pid for pid in eligible if pid in requests]
        if not available:
            return None
        ordered = eligible[room["next_agent"] % len(eligible):] + eligible[:room["next_agent"] % len(eligible)]
        pid = next(p for p in ordered if p in available)
        room["next_agent"] = (eligible.index(pid) + 1) % len(eligible)
        requests.remove(pid)
        return pid, "requested_turn", "free", progress["round"]
    phases = PHASES[room["mode"]]
    while True:
        if progress["phase"] >= len(phases):
            if room["mode"] == "co_creation":
                artifact = room.get('artifacts', {}).get(room.get('creation_artifact_id'), {})
                room["mode_status"] = "awaiting_adoption" if artifact.get('versions') else 'no_artifact'
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
