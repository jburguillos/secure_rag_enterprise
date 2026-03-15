from __future__ import annotations

from app.retrieval.followup import is_followup_like, maybe_rewrite_followup


def test_followup_detection() -> None:
    assert is_followup_like("And what about performance?")
    assert is_followup_like("tambien?" )
    assert is_followup_like("Tell me about it")
    assert is_followup_like("Use that one")
    assert not is_followup_like("Summarize VC trends with citations")


def test_followup_rewrite_uses_previous_user_turn() -> None:
    history = [
        {"role": "user", "content": "Summarize VC trends in Spain"},
        {"role": "assistant", "content": "VC trends in Spain include..."},
    ]
    rewritten, changed = maybe_rewrite_followup("And in 2008?", history)

    assert changed is True
    assert "Summarize VC trends in Spain" in rewritten
    assert "Follow-up:" in rewritten


def test_followup_rewrite_uses_previous_turn_for_anaphora() -> None:
    history = [
        {"role": "user", "content": "In 2025_LP_Commitment_Register.xlsx, summarize the content with citations."},
        {"role": "assistant", "content": "Summary [1]"},
    ]
    rewritten, changed = maybe_rewrite_followup("Tell me about it", history)

    assert changed is True
    assert "2025_LP_Commitment_Register.xlsx" in rewritten
    assert "Follow-up: Tell me about it" in rewritten


def test_followup_rewrite_skips_when_no_previous_user() -> None:
    history = [{"role": "assistant", "content": "hello"}]
    rewritten, changed = maybe_rewrite_followup("And in 2008?", history)

    assert changed is False
    assert rewritten == "And in 2008?"
