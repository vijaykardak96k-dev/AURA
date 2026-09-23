"""Offline mocked tests for the Groq provider.

No real Groq API call is made.
"""

import json
import sys
import types

import pytest

from app.brain.provider import GroqProvider, ProviderUnavailable


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


def _install_fake_groq(monkeypatch, response_text=None, error=None):
    class FakeCompletions:
        def create(self, **kwargs):
            if error:
                raise error
            return _FakeResponse(
                response_text
                or json.dumps(
                    {
                        "action": "OPEN_APP",
                        "parameters": {"app": "chrome"},
                        "response": "Opening Chrome.",
                    }
                )
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeGroqClient:
        # The real SDK accepts timeout/max_retries alongside api_key, and
        # GroqProvider passes both, so the fake must tolerate them too.
        def __init__(self, api_key, **kwargs):
            self.api_key = api_key
            self.kwargs = kwargs
            self.chat = FakeChat()

    module = types.ModuleType("groq")
    module.Groq = FakeGroqClient
    monkeypatch.setitem(sys.modules, "groq", module)


def test_groq_success_path(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    _install_fake_groq(monkeypatch)

    result = GroqProvider().generate_actions("open chrome")
    assert result["action"] == "OPEN_APP"
    assert result["parameters"]["app"] == "chrome"


def test_groq_missing_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ProviderUnavailable, match="GROQ_API_KEY"):
        GroqProvider().generate_actions("open chrome")


def test_groq_network_failure(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    _install_fake_groq(
        monkeypatch,
        error=RuntimeError("network down"),
    )
    with pytest.raises(ProviderUnavailable, match="Groq request failed"):
        GroqProvider().generate_actions("open chrome")


def test_groq_malformed_json(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    _install_fake_groq(monkeypatch, response_text="not json {{{")
    with pytest.raises(ProviderUnavailable, match="Could not parse JSON"):
        GroqProvider().generate_actions("open chrome")


def test_groq_missing_action_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    _install_fake_groq(monkeypatch, response_text=json.dumps({"hello": "world"}))
    with pytest.raises(ProviderUnavailable, match="missing"):
        GroqProvider().generate_actions("open chrome")


def test_groq_is_available_needs_both_key_and_sdk(monkeypatch):
    """
    is_available() is a purely local check — no network call — and it must
    be honest about BOTH preconditions. A key with no SDK installed is
    still unusable, and AURA reports offline mode rather than pretending
    Groq is up and then failing on the first command.
    """
    _install_fake_groq(monkeypatch)

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert GroqProvider().is_available() is False

    monkeypatch.setenv("GROQ_API_KEY", "some-key")
    assert GroqProvider().is_available() is True


def test_groq_unavailable_reason_explains_missing_key(monkeypatch):
    _install_fake_groq(monkeypatch)

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert "GROQ_API_KEY" in (GroqProvider().unavailable_reason() or "")

    monkeypatch.setenv("GROQ_API_KEY", "some-key")
    assert GroqProvider().unavailable_reason() is None


def test_groq_client_is_reused_across_calls(monkeypatch):
    """Building a fresh client per command adds a TLS handshake to every
    single utterance — the provider must cache it."""
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    _install_fake_groq(monkeypatch)

    provider = GroqProvider()
    provider.generate_actions("open chrome")
    first = provider._client
    provider.generate_actions("open chrome")

    assert first is provider._client


# ============================================================
# Two-account backup chain (Groq 1 -> Groq 2 -> caller falls back to
# the offline parser). See app/brain/provider.py's GroqProvider docstring.
# ============================================================

def _install_fake_groq_per_key(monkeypatch, behavior_by_key: dict):
    """Like _install_fake_groq, but the fake client's behavior depends on
    which api_key it was constructed with — lets a test make the primary
    account fail and the backup account succeed (or vice versa) within
    a single generate_actions() call."""

    class FakeCompletions:
        def __init__(self, api_key):
            self.api_key = api_key

        def create(self, **kwargs):
            response_text, error = behavior_by_key[self.api_key]
            if error:
                raise error
            return _FakeResponse(response_text or json.dumps({
                "action": "OPEN_APP",
                "parameters": {"app": "chrome"},
                "response": "Opening Chrome.",
            }))

    class FakeChat:
        def __init__(self, api_key):
            self.completions = FakeCompletions(api_key)

    class FakeGroqClient:
        def __init__(self, api_key, **kwargs):
            self.api_key = api_key
            self.chat = FakeChat(api_key)

    module = types.ModuleType("groq")
    module.Groq = FakeGroqClient
    monkeypatch.setitem(sys.modules, "groq", module)


def test_groq_backup_account_used_when_primary_fails(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "primary-key")
    monkeypatch.setenv("GROQ_API_KEY_2", "backup-key")
    _install_fake_groq_per_key(monkeypatch, {
        "primary-key": (None, RuntimeError("rate limited")),
        "backup-key": (None, None),  # succeeds with the default response
    })

    provider = GroqProvider()
    result = provider.generate_actions("open chrome")

    assert result["action"] == "OPEN_APP"
    assert provider.last_account_used == "backup"


def test_groq_both_accounts_failing_raises_provider_unavailable(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "primary-key")
    monkeypatch.setenv("GROQ_API_KEY_2", "backup-key")
    _install_fake_groq_per_key(monkeypatch, {
        "primary-key": (None, RuntimeError("rate limited")),
        "backup-key": (None, RuntimeError("also down")),
    })

    provider = GroqProvider()
    with pytest.raises(ProviderUnavailable, match="both failed"):
        provider.generate_actions("open chrome")
    assert provider.last_account_used is None


def test_groq_no_backup_configured_fails_like_before(monkeypatch):
    """Without GROQ_API_KEY_2 set, behavior is identical to the
    single-account path — no behavior change for existing installs."""
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    _install_fake_groq(monkeypatch, error=RuntimeError("network down"))

    with pytest.raises(ProviderUnavailable, match="Groq request failed"):
        GroqProvider().generate_actions("open chrome")


def test_groq_primary_success_never_touches_backup(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "primary-key")
    monkeypatch.setenv("GROQ_API_KEY_2", "backup-key")
    _install_fake_groq_per_key(monkeypatch, {
        "primary-key": (None, None),
        "backup-key": (None, RuntimeError("should never be called")),
    })

    provider = GroqProvider()
    result = provider.generate_actions("open chrome")
    assert result["action"] == "OPEN_APP"
    assert provider.last_account_used == "primary"
