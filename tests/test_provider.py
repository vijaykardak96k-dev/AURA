"""
tests/test_provider.py — provider abstraction, config loading, and
provider-failure -> fallback behavior in intent_parser.
"""

import os

import pytest

from app.brain.intent_parser import parse_intent
from app.brain.provider import (
    AIProvider,
    FallbackProvider,
    GroqProvider,
    OllamaProvider,
    ProviderUnavailable,
    get_provider,
)


def test_get_provider_defaults_to_groq_when_unset(monkeypatch):
    """Groq is AURA's default cloud provider — an unset AI_PROVIDER must
    resolve to it, not to the offline parser."""
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    provider = get_provider()
    assert isinstance(provider, GroqProvider)
    assert provider.is_ai is True


def test_get_provider_resolves_groq(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "groq")
    provider = get_provider()
    assert isinstance(provider, GroqProvider)


def test_legacy_gemini_setting_resolves_to_groq(monkeypatch):
    """Gemini was removed. An old .env must not silently do something
    unexpected — it falls through to Groq with a warning."""
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    provider = get_provider()
    assert isinstance(provider, GroqProvider)


def test_describe_active_provider_reports_offline_without_key(monkeypatch):
    """AURA must never claim Groq is working when it isn't."""
    from app.brain.provider import describe_active_provider

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    status = describe_active_provider(GroqProvider())

    assert status["online"] is False
    assert "offline" in status["label"].lower()
    assert "GROQ_API_KEY" in status["detail"]


def test_describe_active_provider_reports_fallback_honestly():
    from app.brain.provider import describe_active_provider

    status = describe_active_provider(FallbackProvider())
    assert status["online"] is False
    assert status["name"] == "fallback"


def test_get_provider_resolves_ollama(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    provider = get_provider()
    assert isinstance(provider, OllamaProvider)


def test_get_provider_unknown_value_falls_back(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "some_made_up_provider")
    provider = get_provider()
    assert isinstance(provider, FallbackProvider)


def test_groq_missing_key_is_handled_gracefully(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    provider = GroqProvider()
    assert provider.is_available() is False
    with pytest.raises(ProviderUnavailable):
        provider.generate_actions("open chrome")


def test_fallback_provider_is_ai_false():
    """The deterministic parser must never be presented as an AI model."""
    provider = FallbackProvider()
    assert provider.is_ai is False
    assert provider.name == "fallback"


def test_intent_parser_falls_back_when_configured_provider_unavailable(monkeypatch):
    """
    End-to-end: even with AI_PROVIDER=gemini configured (and thus
    unavailable in Stage 1), a recognizable command still produces a
    correct, valid action via the automatic fallback to the keyword parser.
    """
    monkeypatch.setenv("AI_PROVIDER", "groq")
    actions = parse_intent("open chrome")
    assert len(actions) == 1
    assert actions[0].valid
    assert actions[0].action == "OPEN_APP"
    assert actions[0].parameters["app"] == "chrome"


def test_intent_parser_never_raises_on_gibberish():
    actions = parse_intent("asdkjfhaskjdfh nonsense gibberish")
    assert len(actions) == 1
    assert actions[0].action == "UNKNOWN"


def test_intent_parser_empty_text_returns_unknown():
    actions = parse_intent("")
    assert actions[0].action == "UNKNOWN"
