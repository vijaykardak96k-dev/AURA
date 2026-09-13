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
    GeminiProvider,
    OllamaProvider,
    ProviderUnavailable,
    get_provider,
)


def test_get_provider_defaults_to_fallback_when_unset(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    provider = get_provider()
    assert isinstance(provider, FallbackProvider)
    assert provider.is_ai is False


def test_get_provider_resolves_gemini(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    provider = get_provider()
    assert isinstance(provider, GeminiProvider)


def test_get_provider_resolves_ollama(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    provider = get_provider()
    assert isinstance(provider, OllamaProvider)


def test_get_provider_unknown_value_falls_back(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "some_made_up_provider")
    provider = get_provider()
    assert isinstance(provider, FallbackProvider)


def test_gemini_missing_key_is_handled_gracefully(monkeypatch):
    """
    Stage 1: GeminiProvider is a stub and always reports unavailable —
    but crucially, constructing it and checking availability must never
    raise, even with no API key configured. Real quota/network/invalid-key
    handling arrives with the Stage 3 implementation.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider = GeminiProvider()
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
    monkeypatch.setenv("AI_PROVIDER", "gemini")
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
