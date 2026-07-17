from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from app.agents.history import trim_history


def _turn(i: int, pad: int = 0):
    return [
        ModelRequest(parts=[UserPromptPart(content=f"question {i} " + "x" * pad)]),
        ModelResponse(parts=[TextPart(content=f"answer {i}")]),
    ]


def test_under_budget_untouched():
    msgs = _turn(1) + _turn(2)
    assert trim_history(msgs, 1_000_000) == msgs


def test_drops_oldest_whole_turns():
    msgs = _turn(1, pad=5000) + _turn(2, pad=5000) + _turn(3, pad=5000)
    out = trim_history(msgs, 12_000)
    # Oldest turn(s) dropped; result still starts at a user-prompt boundary.
    assert isinstance(out[0], ModelRequest)
    assert any(isinstance(p, UserPromptPart) for p in out[0].parts)
    assert len(out) < len(msgs)


def test_always_keeps_last_turn_even_over_budget():
    msgs = _turn(1, pad=50_000)
    out = trim_history(msgs, 10)
    assert len(out) == 2
