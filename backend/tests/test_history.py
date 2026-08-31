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


# --- dangling tool calls (Phase 3 smoke-test finding) ------------------------
#
# A run that dies between issuing a tool call and recording its result — a
# provider 5xx, a timeout, a process restart — leaves the persisted history
# with a tool-call part and no matching tool-return. pydantic-ai then refuses
# every subsequent prompt with "Cannot provide a new user prompt when the
# message history contains unprocessed tool calls", wedging the session
# permanently. Found live against Gemini on 2026-08-31.

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from app.agents.history import reconcile_tool_calls  # noqa: E402


def _dangling():
    return [
        ModelRequest(parts=[UserPromptPart(content="deploy nginx")]),
        ModelResponse(parts=[ToolCallPart(tool_name="run_container", args={}, tool_call_id="c1")]),
    ]


def test_dangling_tool_call_gets_a_synthetic_return():
    out = reconcile_tool_calls(_dangling())
    returns = [p for m in out for p in m.parts if isinstance(p, ToolReturnPart)]
    assert [p.tool_call_id for p in returns] == ["c1"]
    assert returns[0].tool_name == "run_container"


def test_reconcile_is_a_noop_when_every_call_is_answered():
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(parts=[ToolCallPart(tool_name="list_containers", args={}, tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="list_containers", content={}, tool_call_id="c1")]),
    ]
    assert reconcile_tool_calls(msgs) == msgs


def test_reconcile_handles_several_dangling_calls():
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="do two things")]),
        ModelResponse(parts=[
            ToolCallPart(tool_name="a", args={}, tool_call_id="c1"),
            ToolCallPart(tool_name="b", args={}, tool_call_id="c2"),
        ]),
    ]
    out = reconcile_tool_calls(msgs)
    ids = [p.tool_call_id for m in out for p in m.parts if isinstance(p, ToolReturnPart)]
    assert sorted(ids) == ["c1", "c2"]


def test_reconcile_preserves_original_messages():
    msgs = _dangling()
    out = reconcile_tool_calls(msgs)
    assert out[: len(msgs)] == msgs, "existing history must not be mutated or dropped"


def test_reconcile_on_empty_history():
    assert reconcile_tool_calls([]) == []


def test_synthetic_return_marks_the_outcome_as_failed():
    """The model must not read an interrupted call as a success."""
    out = reconcile_tool_calls(_dangling())
    ret = [p for m in out for p in m.parts if isinstance(p, ToolReturnPart)][0]
    assert ret.outcome == "failed"
