"""Validation for optional, untrusted proposal completion criteria.

These criteria are data for human review, not proof of completion or permission
for any action. Callers must separately enforce the complete proposal byte limit,
authorization and all current task/room fences.
"""
import unicodedata


def valid_done_when(value):
    """Whether a supplied value is a nonempty, bounded list of valid text."""
    return (type(value) is list and 1 <= len(value) <= 8
            and all(type(item) is str and 1 <= len(item) <= 200 and item.strip()
                    and not any(unicodedata.category(ch).startswith('C')
                                and ch not in '\n\r\t' for ch in item)
                    for item in value))
