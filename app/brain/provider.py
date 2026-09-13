"""
app/brain/provider.py

AI provider abstraction.

Provider order when AI_PROVIDER=gemini:

    Gemini
       ↓ failure / quota / timeout
    Groq
       ↓ failure
    Fallback parser

Other providers:
    - OllamaProvider
    - FallbackProvider

Nothing outside this file needs to know which provider actually ran.
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
    GEMINI = "gemini"
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
    Groq cloud AI provider.

    Uses the official Groq Python SDK.

    Environment variables:

        GROQ_API_KEY
        GROQ_MODEL

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

        self.timeout = float(
            os.environ.get("AURA_LLM_TIMEOUT", "20")
        )

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate_actions(
        self,
        user_text: str,
        context: str = "",
    ) -> dict:

        if not self.api_key:
            raise ProviderUnavailable(
                "GROQ_API_KEY is not set."
            )

        try:
            from groq import Groq
        except ImportError as e:
            raise ProviderUnavailable(
                "Groq SDK is not installed."
            ) from e

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
            client = Groq(
                api_key=self.api_key,
            )

            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                temperature=0.1,
                response_format={
                    "type": "json_object"
                },
            )

            raw_text = (
                response.choices[0]
                .message
                .content
                or ""
            )

        except Exception as e:
            logger.warning(
                f"Groq request failed: {type(e).__name__}"
            )

            raise ProviderUnavailable(
                "Groq request failed."
            ) from e

        try:
            parsed = extract_json_object(raw_text)

        except json.JSONDecodeError as e:
            logger.warning(
                f"Groq produced unparseable JSON: "
                f"{raw_text[:200]!r}"
            )

            raise ProviderUnavailable(
                "Could not parse JSON from Groq's output."
            ) from e

        if (
            "action" not in parsed
            and "actions" not in parsed
        ):
            raise ProviderUnavailable(
                "Groq JSON missing 'action'/'actions' key."
            )

        return parsed


# ============================================================
# GEMINI PROVIDER
# ============================================================

class GeminiProvider(AIProvider):
    """
    Google Gemini REST provider.

    IMPORTANT:

    Gemini is the PRIMARY provider.

    If Gemini fails for ANY provider-level reason
    (quota, timeout, network error, invalid key, etc.),
    this provider automatically tries Groq.

        Gemini
           ↓
        failure
           ↓
        Groq
           ↓
        failure
           ↓
        intent_parser fallback
    """

    name = ProviderName.GEMINI.value
    is_ai = True

    _BASE_URL = (
        "https://generativelanguage.googleapis.com/"
        "v1beta/models"
    )

    def __init__(self):
        self.api_key = os.environ.get(
            "GEMINI_API_KEY",
            "",
        ).strip()

        self.model = os.environ.get(
            "GEMINI_MODEL",
            "gemini-2.5-flash",
        ).strip()

        self.timeout = float(
            os.environ.get(
                "AURA_LLM_TIMEOUT",
                "20",
            )
        )

    def is_available(self) -> bool:
        """
        Cheap local check only.
        Does not contact Gemini.
        """
        return bool(self.api_key)

    def generate_actions(
        self,
        user_text: str,
        context: str = "",
    ) -> dict:

        # ----------------------------------------------------
        # TRY GEMINI
        # ----------------------------------------------------

        try:
            return self._generate_with_gemini(
                user_text,
                context,
            )

        except ProviderUnavailable as gemini_error:

            logger.warning(
                "Gemini unavailable: "
                f"{gemini_error}"
            )

            # ------------------------------------------------
            # GEMINI FAILED → TRY GROQ
            # ------------------------------------------------

            logger.info(
                "Trying Groq as AI fallback..."
            )

            try:
                result = GroqProvider().generate_actions(
                    user_text,
                    context,
                )

                logger.info(
                    "Groq successfully handled the request."
                )

                return result

            except ProviderUnavailable as groq_error:

                logger.warning(
                    "Groq fallback unavailable: "
                    f"{groq_error}"
                )

                # ------------------------------------------------
                # BOTH AI PROVIDERS FAILED
                # Let intent_parser use its existing fallback.
                # ------------------------------------------------

                raise ProviderUnavailable(
                    "Gemini and Groq are both unavailable."
                ) from groq_error

    def _generate_with_gemini(
        self,
        user_text: str,
        context: str = "",
    ) -> dict:

        if not self.api_key:
            raise ProviderUnavailable(
                "GEMINI_API_KEY is not set."
            )

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

        url = (
            f"{self._BASE_URL}/"
            f"{self.model}:generateContent"
        )

        payload = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": system_prompt
                    }
                ]
            },
            "contents": [
                {
                    "parts": [
                        {
                            "text": prompt
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }

        try:
            resp = requests.post(
                url,
                params={
                    "key": self.api_key
                },
                json=payload,
                timeout=self.timeout,
            )

        except requests.exceptions.Timeout as e:
            raise ProviderUnavailable(
                "Gemini request timed out."
            ) from e

        except requests.exceptions.RequestException as e:
            raise ProviderUnavailable(
                "Gemini network error: "
                f"{type(e).__name__}"
            ) from e

        # ----------------------------------------------------
        # GEMINI HTTP ERRORS
        # ----------------------------------------------------

        if resp.status_code in (401, 403):
            raise ProviderUnavailable(
                "Gemini API key was rejected."
            )

        if resp.status_code == 429:
            raise ProviderUnavailable(
                "Gemini free-tier quota exceeded."
            )

        if resp.status_code >= 500:
            raise ProviderUnavailable(
                f"Gemini service error "
                f"(HTTP {resp.status_code})."
            )

        if resp.status_code != 200:
            raise ProviderUnavailable(
                f"Gemini returned HTTP "
                f"{resp.status_code}."
            )

        # ----------------------------------------------------
        # READ RESPONSE
        # ----------------------------------------------------

        try:
            body = resp.json()

            raw_text = (
                body["candidates"][0]
                ["content"]["parts"][0]
                ["text"]
            )

        except (
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
        ) as e:

            logger.warning(
                "Malformed Gemini HTTP response shape: "
                f"{e}"
            )

            raise ProviderUnavailable(
                "Gemini returned an unexpected response shape."
            ) from e

        # ----------------------------------------------------
        # PARSE JSON
        # ----------------------------------------------------

        try:
            parsed = extract_json_object(
                raw_text
            )

        except json.JSONDecodeError as e:

            logger.warning(
                "Gemini produced unparseable JSON: "
                f"{raw_text[:200]!r}"
            )

            raise ProviderUnavailable(
                "Could not parse JSON from Gemini's output."
            ) from e

        if (
            "action" not in parsed
            and "actions" not in parsed
        ):
            raise ProviderUnavailable(
                "Gemini JSON missing "
                "'action'/'actions' key."
            )

        return parsed


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

    AI_PROVIDER=gemini
        Gemini → Groq → existing fallback

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
            "fallback",
        )
    ).strip().lower()

    if choice == ProviderName.GEMINI.value:
        return GeminiProvider()

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