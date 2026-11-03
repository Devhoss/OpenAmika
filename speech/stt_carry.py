"""Carry text across a forced utterance split (Build F speech layer).

When the dialog layer force-commits an utterance at MAX_UTTERANCE_SEC and the
owner keeps talking, the brain prunes that unprotected turn as soon as the next
message arrives, so everything said before the cut used to be lost. These pure
helpers keep that text and prefix it to the next messages sent to the brain
until a turn is protected (its text is then durable in history) or the memory
is wiped. Pure functions so they can be unit-tested without loading the speech
stack.
"""

CARRY_MAX_CHARS = 2000


def note_force_commit(carry: str, committed_text: str, protected: bool, max_chars: int = CARRY_MAX_CHARS) -> str:
    """Return the new carry after a force commit of ``committed_text``.

    ``protected`` is whether the committed turn already received protect (its
    audio started), in which case its text is safe in history and nothing is
    carried. Repeated force commits accumulate, bounded to the last ``max_chars``.
    """
    text = (committed_text or "").strip()
    carry = (carry or "").strip()
    if not text or protected:
        return carry
    merged = (carry + " " + text).strip() if carry else text
    if len(merged) > max_chars:
        merged = merged[-max_chars:]
    return merged


def with_carry(carry: str, text: str) -> str:
    """Prefix the carried text to ``text`` for the message sent to the brain."""
    carry = (carry or "").strip()
    if not carry:
        return text
    if not (text or "").strip():
        return carry
    return carry + " " + text
