"""Multi-provider model construction.

All provider-specific quirks live here — the rest of the codebase only sees
a pydantic-ai Model + optional ModelSettings.

Anthropic notes (current API):
* default model claude-opus-4-8; adaptive thinking is the modern setting
* never send temperature / top_p / budget_tokens to Opus 4.7+ (they 400)
"""

from __future__ import annotations

from typing import Any

from ..services.settings_store import store

# Suggestions shown in the settings UI. The model field is free-form so users
# can use any model their key has access to.
CATALOG: dict[str, dict[str, Any]] = {
    "anthropic": {
        "label": "Anthropic",
        "key_setting": "anthropic_api_key",
        "models": ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"],
        "default": "claude-opus-4-8",
    },
    "openai": {
        "label": "OpenAI",
        "key_setting": "openai_api_key",
        "models": ["gpt-5.2", "gpt-5.2-mini", "gpt-5-mini"],
        "default": "gpt-5.2",
    },
    "google": {
        "label": "Google Gemini",
        "key_setting": "google_api_key",
        # Verified against the live /v1beta/models listing (2026-08-31).
        "models": ["gemini-3.1-pro-preview", "gemini-3.7-flash", "gemini-2.5-pro", "gemini-2.5-flash"],
        "default": "gemini-2.5-pro",
    },
}


class ProviderNotConfigured(Exception):
    pass


def build_model() -> tuple[Any, dict[str, Any] | None]:
    """Build (model, model_settings) from the active provider settings."""
    provider = store.get("active_provider", "anthropic")
    if provider not in CATALOG:
        raise ProviderNotConfigured(f"unknown provider '{provider}'")
    model_name = store.get("active_model") or CATALOG[provider]["default"]
    api_key = store.get(CATALOG[provider]["key_setting"])
    if not api_key:
        raise ProviderNotConfigured(
            f"No API key configured for {CATALOG[provider]['label']}. Add one in Settings."
        )

    if provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
        from pydantic_ai.providers.anthropic import AnthropicProvider

        model = AnthropicModel(model_name, provider=AnthropicProvider(api_key=api_key))
        return model, AnthropicModelSettings(anthropic_thinking={"type": "adaptive"})

    if provider == "openai":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        return OpenAIChatModel(model_name, provider=OpenAIProvider(api_key=api_key)), None

    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider

    return GoogleModel(model_name, provider=GoogleProvider(api_key=api_key)), None


async def test_provider(provider: str, model_name: str, api_key: str) -> dict[str, Any]:
    """Round-trip a tiny request to validate a key. Returns {ok, error?}."""
    from pydantic_ai import Agent

    try:
        if provider == "anthropic":
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import AnthropicProvider

            model: Any = AnthropicModel(model_name, provider=AnthropicProvider(api_key=api_key))
        elif provider == "openai":
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider

            model = OpenAIChatModel(model_name, provider=OpenAIProvider(api_key=api_key))
        elif provider == "google":
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google import GoogleProvider

            model = GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))
        else:
            return {"ok": False, "error": f"unknown provider '{provider}'"}
        agent = Agent(model)
        result = await agent.run("Reply with the single word: ok")
        return {"ok": True, "reply": str(result.output)[:50]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
