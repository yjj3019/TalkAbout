"""Human moderation panel catalog — the closed set of room actions a moderation UI
may offer, and the read-only projections it may poll.

TalkAbout-owned (docs/CLEAN_ROOM_IMPLEMENTATION.md). The browser panel itself is
NOTRUN (no live browser acceptance); what is pinned here is the *contract the panel
must bind to*: a closed, validated catalog so an unknown or forged action fails
closed instead of falling through to an arbitrary mutation. Reading is limited to
the read-only projections (never marks seen, never mutates). Human-only actions
are marked; agents can never issue them.
"""

# Closed catalog of actions a moderation panel may surface, mapped to the exact API
# surface each one binds to. Adding an entry requires an explicit review; anything
# not in the catalog is rejected by the panel contract.
PANEL_ACTIONS = frozenset({
    'open_room',            # POST /rooms            (사람/에이전트 생성)
    'invite_agent',         # POST /rooms/{id}/memberships (invite)
    'start',                # controls start          (사람 승인)
    'pause',                # controls pause
    'resume',               # controls resume
    'close',                # controls close
    'change_mode',          # controls change_mode
    'change_deliverable',   # controls change_deliverable
    'approve_control',      # controls/{id}/approve   (사람 전용)
    'adopt_artifact',       # artifacts/{id}/adoptions (사람 전용)
    'search',               # GET rooms/{id}/search   (읽기 전용)
    'review_production',    # GET rooms/{id}/production (읽기 전용)
})

# Actions only the human owner/moderator may trigger.
HUMAN_ONLY_ACTIONS = frozenset({'approve_control', 'adopt_artifact', 'close'})

# Read-only projections the panel may poll without ever mutating room state.
PANEL_READS = frozenset({'search', 'production', 'diagnostics', 'messages', 'artifacts'})


class PanelError(ValueError):
    """Unknown or malformed panel action (fail closed, nothing is attempted)."""


def panel_action(action):
    """Validate one catalog action; raise PanelError if it is not in the catalog."""
    if not isinstance(action, str) or action not in PANEL_ACTIONS:
        raise PanelError('unknown_panel_action')
    return action


def panel_read(path_operation):
    """Validate a read-only panel binding; raise PanelError when it is not a read."""
    if not isinstance(path_operation, str) or path_operation not in PANEL_READS:
        raise PanelError('unsupported_panel_read')
    return path_operation


def panel_is_human_only(action):
    """Whether an action may never be issued by an agent."""
    return action in HUMAN_ONLY_ACTIONS
