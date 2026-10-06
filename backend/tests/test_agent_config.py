from pydantic_ai.models.test import TestModel

from app.agents import copilot


def test_agent_tolerates_transient_empty_model_responses(monkeypatch):
    # Gemini sometimes returns an empty turn; a budget of 1 failed whole runs.
    monkeypatch.setattr(copilot, "build_model", lambda: (TestModel(), None))
    agent, _ = copilot.build_agent()
    assert agent._max_output_retries >= 3
