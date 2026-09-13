"""
tests/test_ai_providers.py — GeminiProvider and OllamaProvider, fully
mocked at the `requests` layer so these tests run offline and
deterministically (no real API key or Ollama server needed).
"""

import json

import pytest
import requests

from app.brain.provider import GeminiProvider, OllamaProvider, ProviderUnavailable


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, raise_json_error=False):
        self.status_code = status_code
        self._json_data = json_data
        self._raise_json_error = raise_json_error

    def json(self):
        if self._raise_json_error:
            raise json.JSONDecodeError("bad json", "", 0)
        return self._json_data


def _gemini_ok_body(action_json: dict):
    return {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps(action_json)}]}}
        ]
    }


# ---------------------------------------------------------------------- Gemini

def test_gemini_success_path(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    provider = GeminiProvider()

    expected = {"action": "OPEN_APP", "parameters": {"app": "chrome"}, "response": "Opening chrome."}

    def fake_post(url, params=None, json=None, timeout=None):
        return _FakeResponse(200, _gemini_ok_body(expected))

    monkeypatch.setattr(requests, "post", fake_post)

    result = provider.generate_actions("open chrome")
    assert result["action"] == "OPEN_APP"
    assert result["parameters"]["app"] == "chrome"


def test_gemini_missing_key_raises_without_network_call(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider = GeminiProvider()

    def fake_post(*a, **kw):
        raise AssertionError("Should not make a network call with no API key")

    monkeypatch.setattr(requests, "post", fake_post)

    with pytest.raises(ProviderUnavailable):
        provider.generate_actions("open chrome")


def test_gemini_invalid_key_401(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "bad-key")
    provider = GeminiProvider()
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(401))

    with pytest.raises(ProviderUnavailable, match="rejected"):
        provider.generate_actions("open chrome")


def test_gemini_quota_exceeded_429(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    provider = GeminiProvider()
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(429))

    with pytest.raises(ProviderUnavailable, match="quota"):
        provider.generate_actions("open chrome")


def test_gemini_timeout(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    provider = GeminiProvider()

    def fake_post(*a, **kw):
        raise requests.exceptions.Timeout()

    monkeypatch.setattr(requests, "post", fake_post)

    with pytest.raises(ProviderUnavailable, match="timed out"):
        provider.generate_actions("open chrome")


def test_gemini_network_failure(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    provider = GeminiProvider()

    def fake_post(*a, **kw):
        raise requests.exceptions.ConnectionError()

    monkeypatch.setattr(requests, "post", fake_post)

    with pytest.raises(ProviderUnavailable):
        provider.generate_actions("open chrome")


def test_gemini_malformed_json_output_recovers_safely(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    provider = GeminiProvider()

    body = _gemini_ok_body({})
    body["candidates"][0]["content"]["parts"][0]["text"] = "this is not json at all {{{"
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(200, body))

    with pytest.raises(ProviderUnavailable):
        provider.generate_actions("open chrome")


def test_gemini_extracts_json_from_markdown_fence(monkeypatch):
    """The model sometimes wraps JSON in ```json fences despite instructions not to — must still parse."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    provider = GeminiProvider()

    fenced = "```json\n" + json.dumps({"action": "SCREENSHOT", "parameters": {}}) + "\n```"
    body = _gemini_ok_body({})
    body["candidates"][0]["content"]["parts"][0]["text"] = fenced
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(200, body))

    result = provider.generate_actions("take a screenshot")
    assert result["action"] == "SCREENSHOT"


def test_gemini_is_available_true_only_with_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert GeminiProvider().is_available() is False
    monkeypatch.setenv("GEMINI_API_KEY", "some-key")
    assert GeminiProvider().is_available() is True


# ---------------------------------------------------------------------- Ollama

def test_ollama_success_path(monkeypatch):
    provider = OllamaProvider()
    expected = {"action": "CPU_INFO", "parameters": {}}

    def fake_post(url, json=None, timeout=None):
        return _FakeResponse(200, {"response": __import__("json").dumps(expected)})

    monkeypatch.setattr(requests, "post", fake_post)
    result = provider.generate_actions("check cpu")
    assert result["action"] == "CPU_INFO"


def test_ollama_unreachable(monkeypatch):
    provider = OllamaProvider()

    def fake_post(*a, **kw):
        raise requests.exceptions.ConnectionError()

    monkeypatch.setattr(requests, "post", fake_post)

    with pytest.raises(ProviderUnavailable, match="unreachable"):
        provider.generate_actions("open chrome")


def test_ollama_model_not_found_404(monkeypatch):
    provider = OllamaProvider()
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(404))

    with pytest.raises(ProviderUnavailable, match="not found"):
        provider.generate_actions("open chrome")


def test_ollama_timeout(monkeypatch):
    provider = OllamaProvider()

    def fake_post(*a, **kw):
        raise requests.exceptions.Timeout()

    monkeypatch.setattr(requests, "post", fake_post)

    with pytest.raises(ProviderUnavailable, match="timed out"):
        provider.generate_actions("open chrome")


def test_ollama_is_available_checks_tags_endpoint(monkeypatch):
    provider = OllamaProvider()
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _FakeResponse(200))
    assert provider.is_available() is True

    def fake_get_fail(*a, **kw):
        raise requests.exceptions.ConnectionError()

    monkeypatch.setattr(requests, "get", fake_get_fail)
    assert provider.is_available() is False
