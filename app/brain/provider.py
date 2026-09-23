"""
app/brain/provider.py

AI provider abstraction.

AURA's cloud AI provider is Groq. There is no Gemini path, no Gemini
fallback, and no configuration that can select one.

Provider order:

    Groq  (cloud, requires GROQ_API_KEY)
       |  unavailable / error / bad JSON
       v
    Fallback parser  (deterministic, local, always works offline)

Ollama remains available as an opt-in local alternative for people who
want one (AI_PROVIDER=ollama); it is not part of the default path.

Nothing outside this file needs to know which provider actually ran —
but AURA does report it honestly to the user and the UI, via
describe_active_provider(). AURA never claims Groq is working when it
has silently fallen back.
"""

import json
import os
from abc import ABC, abstractmethod
from enum import Enum

import requests

from app.brain.fallback_parser import parse_fallback_to_dict
from app.brain.json_utils import extract_json_object
from app.brain.prompt_builder import build_system_prompt
from app.utils.logger import get_logger

logger = get_logger()


class ProviderUnavailable(Exception):
    """Raised when an AI provider cannot produce a result."""


class ProviderName(str, Enum):
    GROQ = "groq"
    OLLAMA = "ollama"
    FALLBACK = "fallback"


class AIProvider(ABC):
    """Common interface for every provider."""

    name: str = "unknown"
    is_ai: bool = True

    @abstractmethod
    def generate_actions(self, user_text: str, context: str = "") -> dict:
        raise NotImplementedError

    def is_available(self) -> bool:
        return True


# ============================================================
# FALLBACK PROVIDER
# ============================================================

class FallbackProvider(AIProvider):
    """
    Deterministic local parser.
    Always available and requires no API.
    """

    name = ProviderName.FALLBACK.value
    is_ai = False

    def generate_actions(self, user_text: str, context: str = "") -> dict:
        return parse_fallback_to_dict(user_text)

    def is_available(self) -> bool:
        return True


# ============================================================
# GROQ PROVIDER
# ============================================================

class GroqProvider(AIProvider):
    """
    Groq cloud AI provider, with an optional second-account backup.

    Environment variables:

        GROQ_API_KEY     (required)
        GROQ_MODEL       (optional, defaults to openai/gpt-oss-20b)
        GROQ_API_KEY_2   (optional) — a second Groq account. When the
                         primary account's request fails for any reason
                         (rate limit, timeout, bad response), this
                         account is tried before giving up and letting
                         the caller (app/brain/intent_parser.py) fall
                         back to the offline parser. Leave unset to run
                         with just one account — behavior is identical
                         to the single-account path when it's empty.
        GROQ_MODEL_2     (optional) — model for the backup account;
                         defaults to the same value as GROQ_MODEL.

    Example:

        GROQ_MODEL=openai/gpt-oss-20b
    """

    name = ProviderName.GROQ.value
    is_ai = True

    def __init__(self):
        self.api_key = os.environ.get("GROQ_API_KEY", "").strip()

        self.model = os.environ.get(
            "GROQ_MODEL",
            "openai/gpt-oss-20b",
        ).strip()

        self.backup_api_key = os.environ.get("GROQ_API_KEY_2", "").strip()
        self.backup_model = (
            os.environ.get("GROQ_MODEL_2", "").strip() or self.model
        )

        try:
            self.timeout = float(
                os.environ.get("AURA_LLM_TIMEOUT", "20")
            )
        except (TypeError, ValueError):
            self.timeout = 20.0

        # Reused across calls: building a client per request adds a TLS
        # handshake to every single command. Primary and backup accounts
        # get their own cached client.
        self._client = None
        self._backup_client = None

        # Which account actually answered the most recent request —
        # "primary" | "backup" | None. Read by describe_active_provider()
        # and the UI's per-request provider status, never required for
        # correctness.
        self.last_account_used: str | None = None

    def is_available(self) -> bool:
        """
        True only when Groq could plausibly answer: a key is configured
        AND the SDK is importable. This is a local check — it costs
        nothing and makes no network call, so it is safe to run at
        startup and never adds latency.
        """
        if not self.api_key and not self.backup_api_key:
            return False

        try:
            import groq  # noqa: F401
        except ImportError:
            return False

        return True

    def unavailable_reason(self) -> str | None:
        """A short, user-facing explanation, or None when Groq is usable."""
        if not self.api_key and not self.backup_api_key:
            return "GROQ_API_KEY isn't set"

        try:
            import groq  # noqa: F401
        except ImportError:
            return "the Groq SDK isn't installed (pip install groq)"

        return None

    def _call_account(self, api_key: str, model: str, client_attr: str,
                       user_text: str, context: str) -> dict:
        """
        One request against one Groq account. Raises ProviderUnavailable
        on any failure (network, bad JSON, missing action key) — the
        caller decides whether that means "try the backup account" or
        "give up and let the offline parser take over".
        """
        from groq import Groq

        system_prompt = build_system_prompt()

        prompt = (
            user_text
            if not context
            else (
                f"Conversation context:\n"
                f"{context}\n\n"
                f"User said: {user_text}"
            )
        )

        try:
            client = getattr(self, client_attr)
            if client is None:
                client = Groq(
                    api_key=api_key,
                    timeout=self.timeout,
                    max_retries=1,
                )
                setattr(self, client_attr, client)

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )

            raw_text = response.choices[0].message.content or ""

        except Exception as e:
            # Never log the exception body — it can echo the request,
            # and the API key lives in the client's headers.
            logger.warning(
                f"Groq request failed: {type(e).__name__}."
            )

            # Drop the client so a stale/broken connection isn't reused.
            setattr(self, client_attr, None)

            raise ProviderUnavailable("Groq request failed.") from e

        try:
            parsed = extract_json_object(raw_text)
        except json.JSONDecodeError as e:
            logger.warning(
                f"Groq produced unparseable JSON: {raw_text[:200]!r}"
            )
            raise ProviderUnavailable(
                "Could not parse JSON from Groq's output."
            ) from e

        if "action" not in parsed and "actions" not in parsed:
            raise ProviderUnavailable(
                "Groq JSON missing 'action'/'actions' key."
            )

        return parsed

    def generate_actions(
        self,
        user_text: str,
        context: str = "",
    ) -> dict:

        if not self.api_key and not self.backup_api_key:
            raise ProviderUnavailable(
                "GROQ_API_KEY is not set."
            )

        try:
            import groq  # noqa: F401
        except ImportError as e:
            raise ProviderUnavailable(
                "Groq SDK is not installed."
            ) from e

        primary_error: Exception | None = None

        if self.api_key:
            try:
                result = self._call_account(
                    self.api_key, self.model, "_client", user_text, context,
                )
                self.last_account_used = "primary"
                return result
            except ProviderUnavailable as e:
                primary_error = e
                logger.warning(
                    "Groq primary request failed. "
                    + ("Trying backup Groq account..." if self.backup_api_key
                       else "Falling back to the offline parser for this command.")
                )

        if self.backup_api_key:
            try:
                result = self._call_account(
                    self.backup_api_key, self.backup_model, "_backup_client",
                    user_text, context,
                )
                self.last_account_used = "backup"
                logger.info(
                    "Groq primary unavailable; backup Groq account handled the request."
                )
                return result
            except ProviderUnavailable as backup_error:
                self.last_account_used = None
                logger.warning(
                    "Groq backup account also failed. "
                    "Falling back to the offline parser for this command."
                )
                raise ProviderUnavailable(
                    "Groq primary and backup accounts both failed."
                ) from backup_error

        # No backup configured (or no primary key at all, handled above) —
        # surface whatever the primary attempt raised.
        self.last_account_used = None
        if primary_error is not None:
            raise primary_error
        raise ProviderUnavailable("Groq request failed.")


# ============================================================
# OLLAMA PROVIDER
# ============================================================

class OllamaProvider(AIProvider):
    """
    Local Ollama REST provider.
    """

    name = ProviderName.OLLAMA.value
    is_ai = True

    def __init__(self):
        self.host = os.environ.get(
            "OLLAMA_HOST",
            "http://localhost:11434",
        ).rstrip("/")

        self.model = os.environ.get(
            "OLLAMA_MODEL",
            "qwen2.5:3b-instruct",
        ).strip()

        self.timeout = float(
            os.environ.get(
                "AURA_LLM_TIMEOUT",
                "20",
            )
        )

    def is_available(self) -> bool:
        try:
            resp = requests.get(
                f"{self.host}/api/tags",
                timeout=3,
            )

            return resp.status_code == 200

        except requests.exceptions.RequestException:
            return False

    def generate_actions(
        self,
        user_text: str,
        context: str = "",
    ) -> dict:

        system_prompt = build_system_prompt()

        prompt = (
            user_text
            if not context
            else (
                f"Conversation context:\n"
                f"{context}\n\n"
                f"User said: {user_text}"
            )
        )

        payload = {
            "model": self.model,
            "system": system_prompt,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.1
            },
        }

        try:
            resp = requests.post(
                f"{self.host}/api/generate",
                json=payload,
                timeout=self.timeout,
            )

        except requests.exceptions.Timeout as e:
            raise ProviderUnavailable(
                "Ollama request timed out."
            ) from e

        except requests.exceptions.RequestException as e:
            raise ProviderUnavailable(
                "Ollama unreachable: "
                f"{type(e).__name__}"
            ) from e

        if resp.status_code == 404:
            raise ProviderUnavailable(
                f"Ollama model '{self.model}' not found."
            )

        if resp.status_code != 200:
            raise ProviderUnavailable(
                f"Ollama returned HTTP "
                f"{resp.status_code}."
            )

        try:
            body = resp.json()
            raw_text = body.get(
                "response",
                "",
            )

        except json.JSONDecodeError as e:
            raise ProviderUnavailable(
                "Malformed Ollama HTTP response."
            ) from e

        try:
            parsed = extract_json_object(
                raw_text
            )

        except json.JSONDecodeError as e:

            logger.warning(
                "Ollama produced unparseable JSON: "
                f"{raw_text[:200]!r}"
            )

            raise ProviderUnavailable(
                "Could not parse JSON from Ollama's output."
            ) from e

        if (
            "action" not in parsed
            and "actions" not in parsed
        ):
            raise ProviderUnavailable(
                "Ollama JSON missing "
                "'action'/'actions' key."
            )

        return parsed


# ============================================================
# PROVIDER FACTORY
# ============================================================

def get_provider(
    provider_name: str | None = None,
) -> AIProvider:
    """
    Return the configured provider.

    AI_PROVIDER=groq
        Groq → existing fallback

    AI_PROVIDER=ollama
        Ollama → existing fallback

    AI_PROVIDER=fallback
        Local fallback only
    """

    choice = (
        provider_name
        or os.environ.get(
            "AI_PROVIDER",
            "groq",
        )
    ).strip().lower()

    # Gemini was removed from AURA. If an old .env still selects it, say
    # so plainly and use Groq rather than silently doing something else.
    if choice == "gemini":
        logger.warning(
            "AI_PROVIDER=gemini is no longer supported — AURA uses Groq. "
            "Update your .env (AI_PROVIDER=groq)."
        )
        return GroqProvider()

    if choice == ProviderName.GROQ.value:
        return GroqProvider()

    if choice == ProviderName.OLLAMA.value:
        return OllamaProvider()

    if choice == ProviderName.FALLBACK.value:
        return FallbackProvider()

    logger.warning(
        f"Unknown AI_PROVIDER '{choice}', "
        "defaulting to fallback parser."
    )

    return FallbackProvider()

# ============================================================
# STATUS REPORTING
# ============================================================

def describe_active_provider(provider: AIProvider) -> dict:
    """
    Describe what AURA is actually running on, for logs and the UI.

    Returns:
        {
            "name":      "groq" | "ollama" | "fallback",
            "label":     "Groq" | "Offline parser",
            "online":    bool,     # a cloud/AI provider is usable
            "detail":    str,      # short human-readable explanation
        }

    The point of this function is honesty: if Groq can't be reached,
    AURA says it is running offline rather than pretending otherwise.
    """
    name = getattr(provider, "name", "unknown")

    if not getattr(provider, "is_ai", False):
        return {
            "name": name,
            "label": "Offline parser",
            "online": False,
            "detail": (
                "Running on the built-in offline parser. "
                "Desktop commands work; open conversation is limited."
            ),
        }

    reason = None
    if hasattr(provider, "unavailable_reason"):
        try:
            reason = provider.unavailable_reason()
        except Exception:
            reason = None

    available = True
    try:
        available = bool(provider.is_available())
    except Exception:
        available = False

    if available:
        model = getattr(provider, "model", "")
        return {
            "name": name,
            "label": name.capitalize(),
            "online": True,
            "detail": f"Connected to {name.capitalize()}"
                      + (f" ({model})." if model else "."),
        }

    return {
        "name": name,
        "label": "Offline parser",
        "online": False,
        "detail": (
            f"{name.capitalize()} is unavailable"
            + (f" — {reason}." if reason else ".")
            + " Using the offline parser instead."
        ),
    }
