"""
app/core/agent.py

Central AURA orchestrator.

The Electron UI is only a visual frontend.
This class remains the actual AURA brain.

Flow:

    Voice / UI input
          |
          v
      AuraAgent
          |
    +-----+------+
    |            |
  Whisper       Typed input
    |
 Wake word
    |
 ACTIVE
    |
 Intent parser
    |
 Groq / fallback
    |
 Security / confirmation
    |
 Action executor
    |
 Piper TTS
    |
 Electron UI callbacks


States:

    STARTING
    SLEEPING
    ACTIVE
    HEARING
    THINKING
    SPEAKING
    WAITING_FOR_CONFIRMATION
    WAITING_FOR_BROWSER_CHOICE
    EXECUTING
    ERROR
    STOPPED
"""

import threading
import time
import difflib
import re
from enum import Enum
from typing import Callable, Optional

from app.actions.executor import execute_action
from app.actions.browser import match_browser_name, extract_browser_from_text
from app.brain.local_intents import match_local_intent
from app.brain.emotion import select_emotion
from app.brain.persona import (
    acknowledge,
    speakable_summary,
    warm_up_failure_message,
)
from app.brain.schemas import ActionItem
from app.brain.intent_parser import parse_intent
from app.brain.provider import get_provider
from app.memory import memory_manager
from app.security.confirmation import (
    build_confirmation_prompt,
    is_affirmative,
    is_negative,
)
from app.utils.logger import get_logger
from app.voice.speech_to_text import VoiceInputError
from app.voice.wake_word import (
    DEFAULT_WAKE_NAME,
    detect_wake_word,
)

logger = get_logger()


# ================================================================
# CONFIGURATION
# ================================================================

DEFAULT_CONFIRMATION_TIMEOUT = 12.0

# How long AURA waits, after asking, for the user to name a browser.
DEFAULT_BROWSER_CHOICE_TIMEOUT = 12.0

# AURA stays ACTIVE for this long after the last meaningful activity.
DEFAULT_SESSION_TIMEOUT = 120.0

# Small delay after speech so trailing audio doesn't get interpreted
# as another command.
POST_SPEECH_PAUSE = 0.15

# Extra microphone settling time after AURA finishes speaking. The
# microphone is already closed during SPEAKING, so this only needs to
# flush the speaker tail still sitting in the input buffer.
MICROPHONE_SETTLE_SECONDS = 0.25

# Short window in which a transcript matching AURA's last spoken response
# is treated as speaker echo rather than a new user command. This is a
# belt-and-braces backstop only: the real protection is that the
# microphone does not record at all while AURA speaks.
TTS_ECHO_SUPPRESSION_SECONDS = 3.0

# How many spoken app names are reasonable in one breath. Longer lists go
# to the UI in full and are summarized for speech.
MAX_SPOKEN_LIST_ITEMS = 8

# Prevent a race between the voice loop and a command thread.
_INPUT_RACE_GRACE_SECONDS = 1.5

# Confirmation listening parameters.
_CONFIRMATION_LISTEN_WAIT = 3.0

# Error recovery.
_ERROR_RECOVERY_PAUSE = 1.0

# How many consecutive listen failures before AURA reports ERROR in the
# UI. One dropped buffer is noise; five in a row is a real fault.
_VOICE_ERRORS_BEFORE_ERROR_STATE = 5

# Ceiling on the retry backoff so a machine with no microphone doesn't
# spin the CPU retrying forever.
_MAX_VOICE_ERROR_BACKOFF = 5.0

# Prevent a broken/mock microphone implementation from spinning
# the CPU at 100%.
_MIN_LISTEN_CYCLE_SECONDS = 0.15


# ================================================================
# STATES
# ================================================================

class AuraState(str, Enum):

    STARTING = "STARTING"

    SLEEPING = "SLEEPING"

    ACTIVE = "ACTIVE"

    HEARING = "HEARING"

    THINKING = "THINKING"

    SPEAKING = "SPEAKING"

    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"

    WAITING_FOR_BROWSER_CHOICE = "WAITING_FOR_BROWSER_CHOICE"

    EXECUTING = "EXECUTING"

    ERROR = "ERROR"

    STOPPED = "STOPPED"


# ================================================================
# AURA AGENT
# ================================================================

class AuraAgent:

    def __init__(
        self,
        provider=None,
        stt=None,
        tts=None,
        voice_enabled: bool = True,
        wake_word_enabled: bool = True,
        wake_name: str = DEFAULT_WAKE_NAME,
        session_timeout: float = DEFAULT_SESSION_TIMEOUT,
        confirmation_timeout: float = DEFAULT_CONFIRMATION_TIMEOUT,
        browser_choice_timeout: float = DEFAULT_BROWSER_CHOICE_TIMEOUT,
        greeting: Optional[str] = None,
        on_state_change: Optional[Callable[[AuraState], None]] = None,
        on_message: Optional[Callable[[str, str], None]] = None,
        on_recent_actions_update: Optional[Callable[[], None]] = None,
        on_action: Optional[Callable[[str, str], None]] = None,
        on_tts_status: Optional[Callable[[dict], None]] = None,
    ):
        """
        Create the AURA agent.

        Callbacks:

            on_state_change(state)
                Sent whenever AURA changes state.

            on_message(speaker, text)
                Sent whenever a user-facing message is produced.

            on_recent_actions_update()
                Sent when recent actions change.

            on_action(action, message)
                Sent when a real action is executed.

            on_tts_status({"state": "speaking"|"completed"|"failed",
                            "emotion": str, "chars": int, "seconds": float})
                Sent around every spoken reply (Task 8) — lets the UI show
                the emotion AURA is speaking with and whether synthesis
                actually completed. Optional; respond() no-ops if unset.

        The Electron UI uses these callbacks to stay synchronized
        with the real Python backend.
        """

        self.provider = provider or get_provider()

        self.tts = tts

        self.stt = stt if voice_enabled else None

        self.voice_enabled = (
            voice_enabled
            and stt is not None
        )

        self.wake_word_enabled = (
            wake_word_enabled
            and self.voice_enabled
        )

        self.wake_name = (
            wake_name
            or DEFAULT_WAKE_NAME
        ).strip().lower()

        self.session_timeout = float(
            session_timeout
        )

        self.confirmation_timeout = float(
            confirmation_timeout
        )

        self.browser_choice_timeout = float(
            browser_choice_timeout
        )

        self.greeting = (
            greeting
            or "Hello, welcome to AURA. How may I help you?"
        )


        # --------------------------------------------------------
        # UI callbacks
        # --------------------------------------------------------

        self.on_state_change = (
            on_state_change
        )

        self.on_message = (
            on_message
        )

        self.on_recent_actions_update = (
            on_recent_actions_update
        )

        self.on_action = (
            on_action
        )

        self.on_tts_status = on_tts_status

        # Session-scoped context for prosody selection (see respond() and
        # handle_input()) — not persisted, not part of the UI contract.
        self._last_user_text = ""
        self._last_tts_emotion = "neutral"


        # --------------------------------------------------------
        # Internal state
        # --------------------------------------------------------

        self._state = AuraState.STARTING

        self._state_lock = (
            threading.RLock()
        )

        # Only one command can be processed at a time.
        self._busy_lock = (
            threading.Lock()
        )


        # --------------------------------------------------------
        # Voice thread
        # --------------------------------------------------------

        self._voice_thread = None

        self._stop_event = (
            threading.Event()
        )


        # --------------------------------------------------------
        # Confirmation
        # --------------------------------------------------------

        self._confirmation_event = (
            threading.Event()
        )

        self._confirmation_result = None

        # --------------------------------------------------------
        # Browser choice (asked whenever a web search doesn't name
        # one and none can be inferred from the command text).
        # --------------------------------------------------------

        self._browser_choice_event = (
            threading.Event()
        )

        self._browser_choice_result = None

        self._last_target = None


        # --------------------------------------------------------
        # Session activity
        # --------------------------------------------------------

        self._last_activity_time = (
            time.monotonic()
        )

        # TTS echo protection.
        self._last_tts_text = ""
        self._tts_echo_block_until = 0.0

        # Set when the user types "mute" — makes the current TTS stop and
        # suppresses any remaining speech from the command in flight.
        self._mute_requested = threading.Event()

        # Consecutive microphone/transcription failures, reset on success.
        self._voice_error_count = 0

        # A clarifying question AURA asked that the next user utterance is
        # probably answering, e.g. "What should I search for?". Bounded to
        # a single pending slot on purpose — this is short-term context,
        # not a dialogue manager.
        self._pending_clarification = None
        self._pending_clarification_until = 0.0


    # ============================================================
    # STATE
    # ============================================================

    @property
    def state(self) -> AuraState:

        with self._state_lock:

            return self._state


    def _set_state(
        self,
        new_state: AuraState,
    ) -> None:

        with self._state_lock:

            if self._state == new_state:
                return

            old_state = self._state

            self._state = new_state


        logger.info(
            f"AURA state -> {new_state.value}"
        )


        if {
            old_state,
            new_state,
        } == {
            AuraState.SLEEPING,
            AuraState.ACTIVE,
        }:

            logger.info(
                f"State: {old_state.value} -> "
                f"{new_state.value}"
            )


        # --------------------------------------------------------
        # Notify Electron / UI
        # --------------------------------------------------------

        if self.on_state_change:

            try:

                self.on_state_change(
                    new_state
                )

            except Exception as e:

                logger.warning(
                    "on_state_change callback raised: "
                    f"{e}"
                )


    def _touch_activity(self) -> None:

        self._last_activity_time = (
            time.monotonic()
        )


    # ============================================================
    # MICROPHONE GATING
    # ============================================================

    def _listening_allowed(
        self,
        *expected_states: AuraState,
    ) -> bool:
        """
        The single source of truth for "may the microphone be recording
        right now?".

        Passed into every blocking STT call as `should_continue`, so an
        open input stream is torn down the instant AURA starts speaking,
        is shut down, or is asked to stop. This is what stops Whisper
        hearing Piper through the speakers — state-based gating, not
        after-the-fact filtering of AURA's own words.
        """

        if self._stop_event.is_set():
            return False

        state = self.state

        if state in (
            AuraState.SPEAKING,
            AuraState.THINKING,
            AuraState.EXECUTING,
            AuraState.STOPPED,
            AuraState.STARTING,
        ):
            return False

        if expected_states and state not in expected_states:
            return False

        return True


    # ============================================================
    # LIFECYCLE
    # ============================================================

    def start(self) -> None:
        """
        Start AURA.

        Startup behavior:

            STARTING
                |
                v
             greeting
                |
                v
             ACTIVE
                |
                | 120 seconds inactivity
                v
            SLEEPING

        This means AURA does NOT immediately sleep after launch.

        When voice is enabled, it starts listening immediately after
        the startup greeting.

        The 120-second inactivity timeout is measured from the
        startup greeting / last activity.
        """

        memory_manager.init()

        self._set_state(
            AuraState.STARTING
        )


        provider_label = (
            self.provider.name.capitalize()
            if self.provider.is_ai
            else "offline fallback parser"
        )


        logger.info(
            f"AURA starting. "
            f"AI provider: {provider_label}. "
            f"Voice enabled: {self.voice_enabled}. "
            f"Wake word: {self.wake_word_enabled}. "
            f"Session timeout: {self.session_timeout}s."
        )


        # --------------------------------------------------------
        # Startup greeting
        # --------------------------------------------------------

        self.respond(
            self.greeting
        )


        # Start inactivity timer AFTER greeting.
        self._touch_activity()


        # --------------------------------------------------------
        # Voice mode
        # --------------------------------------------------------

        if self.voice_enabled:

            self._stop_event.clear()

            # IMPORTANT:
            # Startup is ACTIVE, not SLEEPING.
            self._set_state(
                AuraState.ACTIVE
            )


            self._voice_thread = (
                threading.Thread(
                    target=self._voice_loop,
                    name="AURA-VoiceLoop",
                    daemon=True,
                )
            )

            self._voice_thread.start()


        # --------------------------------------------------------
        # Text-only mode
        # --------------------------------------------------------

        else:

            self._set_state(
                AuraState.ACTIVE
            )


    def shutdown(self) -> None:
        """
        Stop AURA completely.
        """

        logger.info(
            "Shutting down AURA..."
        )

        self._stop_event.set()

        self._set_state(
            AuraState.STOPPED
        )


        if (
            self._voice_thread
            and self._voice_thread.is_alive()
        ):

            self._voice_thread.join(
                timeout=2
            )


    def stop_listening(self) -> None:
        """
        Pause microphone listening. The process itself stays alive.

        STOPPED is honoured by _listening_allowed(), so any capture
        already in flight aborts on its next 50 ms block rather than
        running to completion.
        """

        logger.info("Microphone disabled (listening stopped).")

        self._clear_pending_clarification()

        self._set_state(AuraState.STOPPED)


    def start_listening(self) -> None:
        """
        Resume microphone listening.

        Manual Start bypasses the wake-word gate and enters ACTIVE. Safe
        to call from any state — if AURA is already listening this is a
        no-op rather than an error.
        """

        if self.state in (AuraState.ACTIVE, AuraState.HEARING):
            return

        self._reset_wake_buffer()

        self._touch_activity()

        logger.info("Microphone enabled (listening started).")

        self._set_state(AuraState.ACTIVE)


    def _reset_wake_buffer(self) -> None:
        """Drop carried-over wake audio. Never raises."""

        if self.stt is None:
            return

        try:
            self.stt.reset_wake_buffer()
        except Exception as exc:
            logger.debug("Could not reset wake buffer: %s", exc)


    def sleep(self) -> None:
        """
        Put AURA into SLEEPING: the wake word is the only thing it
        listens for, and Whisper only runs on windows that actually
        contain sound (see speech_to_text.WAKE_MIN_ENERGY).
        """

        if self.state in (AuraState.SLEEPING, AuraState.STOPPED):
            return

        self._clear_pending_clarification()
        self._reset_wake_buffer()

        logger.info("AURA going to sleep — wake word only.")

        self._set_state(AuraState.SLEEPING)


    def wake(self) -> None:
        """
        Programmatically wake AURA. Equivalent to saying the wake word.

        Unlike the old version this also works from STOPPED, so "wake up"
        can't leave AURA stuck with the microphone off.
        """

        if self.state in (AuraState.ACTIVE, AuraState.HEARING):
            return

        logger.info("Manual wake triggered.")

        self._reset_wake_buffer()

        self._touch_activity()

        self._set_state(AuraState.ACTIVE)


    # ============================================================
    # VOICE / LOCAL CONTROL
    # ============================================================

    @staticmethod
    def _normalize_command(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text.lower())).strip()

    def _run_local_action(self, action: str, parameters: dict) -> bool:
        """Handle common desktop commands locally without waiting for AI."""
        item = ActionItem(action=action, parameters=parameters)
        try:
            self._set_state(AuraState.EXECUTING)
            success, message = execute_action(item)
        except Exception as exc:
            logger.exception("Local action failed: %s", action)
            success, message = False, f"I couldn't complete that action: {exc}"

        if self.on_action:
            try:
                self.on_action(action, message, success)
            except Exception as exc:
                logger.warning("Local action callback failed: %s", exc)

        self.respond(warm_up_failure_message(message) if not success else message)

        self._touch_activity()

        if self.state not in (AuraState.STOPPED, AuraState.SLEEPING, AuraState.SPEAKING):
            self._set_state(AuraState.ACTIVE)
        return True

    # ------------------------------------------------------------------
    # Short-term conversational context
    # ------------------------------------------------------------------

    # How long a clarifying question stays "open" for the next utterance
    # to answer. Short on purpose: this is immediate context, not memory.
    PENDING_CLARIFICATION_SECONDS = 45.0

    def _set_pending_clarification(self, kind: str) -> None:
        self._pending_clarification = kind
        self._pending_clarification_until = (
            time.monotonic() + self.PENDING_CLARIFICATION_SECONDS
        )

    def _clear_pending_clarification(self) -> None:
        self._pending_clarification = None
        self._pending_clarification_until = 0.0

    def _apply_pending_clarification(self, text: str) -> str:
        """
        If AURA just asked "what should I search for?" and the user replies
        with a bare phrase, rewrite it into the full command before it
        reaches the intent parser.

        Deliberately conservative: only applies when a question is open,
        only within a short window, and only when the reply doesn't already
        look like a command of its own.
        """
        pending = self._pending_clarification

        if not pending:
            return text

        if time.monotonic() > self._pending_clarification_until:
            self._clear_pending_clarification()
            return text

        self._clear_pending_clarification()

        normalized = self._normalize_command(text)

        if not normalized:
            return text

        # A reply that is itself a command wins over the pending question.
        if re.match(
            r"^(open|launch|start|close|search|play|show|list|take|lock|sleep|"
            r"wake|mute|help|what|who|why|how|stop)\b",
            normalized,
        ):
            return text

        rewritten = {
            "web_search": f"search the web for {text}",
            "youtube_search": f"search youtube for {text}",
        }.get(pending)

        if not rewritten:
            return text

        logger.info(
            f'Resolved follow-up using pending question ({pending}): '
            f'"{text}" -> "{rewritten}"'
        )

        return rewritten

    # ------------------------------------------------------------------
    # Local fast path
    # ------------------------------------------------------------------

    def _handle_local_control(self, text: str) -> bool:
        """Fast path for high-confidence controls that should never depend on Groq."""
        normalized = self._normalize_command(text)

        if not normalized:
            return False

        # --- AURA's own sleep/wake (NOT Windows sleep) ------------------
        if normalized in {
            "sleep", "go to sleep", "aura sleep", "aura go to sleep",
            "sleep aura", "go quiet", "stand by",
        }:
            self._touch_activity()
            self._clear_pending_clarification()
            self.respond(acknowledge("sleep"))
            if self.state != AuraState.STOPPED:
                self.sleep()
            return True

        if normalized in {"wake up", "wake aura", "aura wake up", "wake"}:
            if self.state in (AuraState.SLEEPING, AuraState.STOPPED):
                self.wake()
                self.respond(acknowledge("wake"))
            else:
                self.respond("I'm already awake.")
            return True

        # --- listening control -----------------------------------------
        if normalized in {"stop listening", "stop the mic", "mic off"}:
            self.respond("Okay, I'll stop listening. Use Start Listening when you want me back.")
            self.stop_listening()
            return True

        if normalized in {"start listening", "listen", "mic on"}:
            if self.state == AuraState.STOPPED:
                self.start_listening()
            self.respond("Listening.")
            return True

        # --- help -------------------------------------------------------
        if normalized in {
            "help", "aura help", "what can you do", "what all can you do",
            "show me your commands", "list your commands", "your capabilities",
        }:
            return self._run_local_action("HELP", {})

        # --- installed applications -------------------------------------
        if re.fullmatch(
            r"(?:list|show|show me|what)\s+(?:me\s+)?(?:all\s+)?(?:my\s+)?"
            r"(?:installed\s+)?(?:apps?|applications?|programs?|software)"
            r"(?:\s+(?:are\s+)?installed)?",
            normalized,
        ) or normalized in {
            "list all installed apps", "list installed apps",
            "list all installed applications", "what apps do i have installed",
            "what applications are installed", "show me my installed programs",
            "what programs do i have", "what apps do i have",
        }:
            return self._run_local_action("LIST_INSTALLED_APPS", {})

        match = re.fullmatch(
            r"(?:do i have|is|are)\s+(.+?)\s+installed", normalized
        )
        if match:
            return self._run_local_action("FIND_APP", {"app": match.group(1).strip()})

        match = re.fullmatch(r"find\s+(?:the\s+)?app\s+(.+)", normalized)
        if match:
            return self._run_local_action("FIND_APP", {"app": match.group(1).strip()})

        # --- clipboard ---------------------------------------------------
        if normalized in {
            "what's on my clipboard", "whats on my clipboard",
            "read my clipboard", "read the clipboard", "show clipboard",
        }:
            return self._run_local_action("CLIPBOARD_READ", {})

        # --- explicit local app launches --------------------------------
        app_match = re.fullmatch(
            r"(?:open|launch|start)\s+(brave|brave browser|whatsapp|telegram)",
            normalized,
        )
        if app_match:
            app = app_match.group(1).replace(" browser", "").strip()
            return self._run_local_action("OPEN_APP", {"app": app})

        if normalized in {"open youtube", "launch youtube", "start youtube"}:
            return self._run_local_action("OPEN_WEBSITE", {"site": "youtube"})

        match = re.fullmatch(r"(?:play|watch)\s+(.+?)\s+(?:on\s+)?youtube", normalized)
        if match:
            return self._run_local_action("YOUTUBE_PLAY", {"query": match.group(1).strip()})

        match = re.fullmatch(r"(?:search|find)\s+(.+?)\s+on\s+youtube", normalized)
        if match:
            return self._run_local_action("YOUTUBE_SEARCH", {"query": match.group(1).strip()})

        # --- a bare "search youtube" needs a subject -------------------
        if normalized in {"search youtube", "serch youtube", "search on youtube"}:
            self._set_pending_clarification("youtube_search")
            self.respond("What would you like me to search for?")
            if self.state not in (AuraState.STOPPED, AuraState.SLEEPING, AuraState.SPEAKING):
                self._set_state(AuraState.ACTIVE)
            return True

        if normalized in {"search the web", "search google", "search"}:
            self._set_pending_clarification("web_search")
            self.respond("Happy to. What should I search for?")
            if self.state not in (AuraState.STOPPED, AuraState.SLEEPING, AuraState.SPEAKING):
                self._set_state(AuraState.ACTIVE)
            return True

        # Table-driven fast path for everything else that doesn't need the
        # AI provider: extra app launches (with discovery for anything not
        # explicitly known), screenshots, lock/volume/media controls, file
        # intelligence lookups, and "what did you do today" activity
        # summaries. See app/brain/local_intents.py for the full rule set
        # and why a few phrasings are deliberately excluded from it.
        local_match = match_local_intent(normalized)
        if local_match:
            action_name, parameters = local_match
            return self._run_local_action(action_name, parameters)

        return False

    # ============================================================
    # RESPONSE
    # ============================================================

    def respond(
        self,
        text: str,
        speak: bool = True,
        show_in_ui: bool = True,
    ) -> None:
        """
        Single AURA output path.

        Order matters and is deliberate:

            1. publish to the UI
            2. write to memory
            3. THEN attempt TTS

        The text is on screen before a single byte of audio is
        synthesized, so a Piper failure, a missing voice model, or a dead
        audio device can never cost the user their answer. TTS is
        best-effort decoration on top of a response that has already been
        delivered.
        """

        if not text:
            return

        text = str(text).strip()

        if not text:
            return

        # --------------------------------------------------------
        # 1. UI — first, always, before anything that can fail.
        # --------------------------------------------------------

        if show_in_ui and self.on_message:

            try:
                self.on_message("AURA", text)

            except Exception as e:
                logger.warning(f"on_message callback raised: {e}")

        # --------------------------------------------------------
        # 2. Memory
        # --------------------------------------------------------

        try:
            memory_manager.add_conversation_turn("assistant", text)

        except Exception as e:
            logger.warning(f"Could not save assistant message: {e}")

        # --------------------------------------------------------
        # 3. TTS — isolated so it can never strand the state machine.
        # --------------------------------------------------------

        if not (speak and self.voice_enabled_for_output()):
            return

        if self._mute_requested.is_set():
            logger.info("Skipping TTS — mute is active.")
            return

        previous_state = self.state

        if previous_state == AuraState.STOPPED:
            # Listening is off, but the user still asked for this reply.
            pass

        self._set_state(AuraState.SPEAKING)

        # Remember exactly what AURA just said so an echoed Whisper
        # transcript can be rejected before it reaches the command parser.
        self._last_tts_text = text
        self._tts_echo_block_until = (
            time.monotonic() + TTS_ECHO_SUPPRESSION_SECONDS
        )

        spoken = speakable_summary(text, max_items=MAX_SPOKEN_LIST_ITEMS)

        emotion = select_emotion(
            reply_text=spoken,
            user_text=getattr(self, "_last_user_text", ""),
        )
        self._last_tts_emotion = emotion

        logger.info(f"TTS started ({len(spoken)} chars, emotion={emotion}). Microphone disabled.")

        if self.on_tts_status:
            try:
                self.on_tts_status({"state": "speaking", "emotion": emotion, "chars": len(spoken)})
            except Exception as e:
                logger.debug("on_tts_status callback raised: %s", e)

        tts_ok = False
        tts_start = time.monotonic()

        try:
            # IMPORTANT: the microphone stays completely closed while AURA
            # speaks — see _listening_allowed(). A typed "mute" command is
            # the only way to interrupt playback, by design.
            try:
                result = self.tts.speak(spoken, emotion=emotion)
            except TypeError:
                # self.tts may be a plain object (a test double, a custom
                # engine, or a subclass that overrides speak(text) without
                # forwarding **kwargs) that predates the emotion parameter.
                # Retry with the original, always-supported call rather
                # than losing the utterance over a signature mismatch —
                # this is exactly the failure mode Task 1 exists to fix.
                result = self.tts.speak(spoken)
            # Engines that don't return a bool (legacy callers, mocks) are
            # treated as success as long as they didn't raise, matching
            # the pre-existing behavior of this call site.
            tts_ok = result is not False

        except Exception as e:
            tts_ok = False
            logger.error(f"TTS error (text already shown in UI): {e}")

        finally:
            elapsed = time.monotonic() - tts_start
            if tts_ok:
                logger.info(f"TTS completed ({elapsed:.2f}s audio). Microphone may resume.")
            else:
                logger.warning(f"TTS failed after {elapsed:.2f}s. Microphone may resume.")

            if self.on_tts_status:
                try:
                    self.on_tts_status({
                        "state": "completed" if tts_ok else "failed",
                        "emotion": emotion,
                        "seconds": round(elapsed, 2),
                    })
                except Exception as e:
                    logger.debug("on_tts_status callback raised: %s", e)

            self._mute_requested.clear()

            # SPEAKING must never be a terminal state, even if TTS threw.
            if self.state == AuraState.SPEAKING:
                if previous_state == AuraState.STOPPED:
                    self._set_state(AuraState.STOPPED)
                elif previous_state == AuraState.SLEEPING:
                    self._set_state(AuraState.SLEEPING)
                else:
                    self._set_state(AuraState.ACTIVE)


    def voice_enabled_for_output(
        self,
    ) -> bool:

        return self.tts is not None


    # ============================================================
    # INPUT DISPATCH
    # ============================================================

    def _dispatch_command_async(
        self,
        text: str,
    ) -> None:
        """
        Process a command on another thread.

        This is important for confirmation requests because
        _ask_confirmation() blocks while waiting for YES/NO.
        The voice loop must remain free to listen for that answer.
        """

        threading.Thread(
            target=self.handle_input,
            args=(text,),
            name="AURA-Command",
            daemon=True,
        ).start()


        # Wait briefly until the command thread actually moves
        # AURA away from ACTIVE.
        deadline = (
            time.monotonic()
            + 2.0
        )


        while (
            self.state
            in (
                AuraState.SLEEPING,
                AuraState.ACTIVE,
            )
            and time.monotonic()
            < deadline
        ):

            time.sleep(
                0.01
            )


    def _speak_and_return_to(
        self,
        text: str,
        target_state: AuraState,
    ) -> None:

        self.respond(
            text
        )


        if self.state != AuraState.STOPPED:

            self._set_state(
                target_state
            )


    # ============================================================
    # INPUT
    # ============================================================

    def handle_input(
        self,
        text: str,
    ) -> None:
        """
        Process already-authorized input.

        Voice input reaches here only after the wake-word gate
        has approved it.

        Typed UI input is intentionally accepted directly.
        """

        if not text:

            return


        text = str(text).strip()

        if not text:

            return

        # Remembered so respond()'s rule-based emotion selection has the
        # user's own words to react to (e.g. "terrible day" -> empathetic),
        # not just AURA's reply text. Session-scoped, not persisted.
        self._last_user_text = text

        # --------------------------------------------------------
        # Wait briefly if THINKING started just before this input.
        # --------------------------------------------------------

        deadline = (
            time.monotonic()
            + _INPUT_RACE_GRACE_SECONDS
        )


        while (
            self.state == AuraState.THINKING
            and time.monotonic()
            < deadline
        ):

            time.sleep(
                0.02
            )


        # --------------------------------------------------------
        # Typed TTS mute/stop.
        #
        # The microphone is intentionally CLOSED while AURA speaks (see
        # _listening_allowed), so a typed/UI "mute" is the only way to
        # interrupt playback — and that is the intended design, not a
        # limitation. Handled before the busy-lock and before the AI
        # pipeline so it works even mid-response.
        # --------------------------------------------------------

        normalized_input = self._normalize_command(text)

        if normalized_input in {"mute", "stop talking", "be quiet", "shut up", "silence"}:
            self._stop_speaking()
            return

        # --------------------------------------------------------
        # Confirmation reply
        # --------------------------------------------------------

        if (
            self.state
            == AuraState.WAITING_FOR_CONFIRMATION
        ):

            self._resolve_confirmation(
                text
            )

            return


        # --------------------------------------------------------
        # Browser-choice reply
        # --------------------------------------------------------

        if (
            self.state
            == AuraState.WAITING_FOR_BROWSER_CHOICE
        ):

            self._resolve_browser_choice(
                text
            )

            return


        # --------------------------------------------------------
        # Don't process two commands simultaneously.
        # --------------------------------------------------------

        if not self._busy_lock.acquire(
            blocking=False
        ):

            logger.info(
                f"Ignoring input while busy "
                f"({self.state.value}): {text!r}"
            )

            return


        try:

            self._process_command(
                text
            )

        finally:

            self._busy_lock.release()


    def _stop_speaking(self) -> None:
        """
        Immediately silence current TTS playback and return to a sane
        state. Safe to call at any time, in any state.
        """

        was_speaking = self.state == AuraState.SPEAKING

        self._mute_requested.set()

        try:
            stop = getattr(self.tts, "stop", None)
            if callable(stop):
                stop()
                logger.info("TTS stopped by mute command.")
        except Exception as exc:
            logger.warning("Could not stop TTS after mute: %s", exc)

        if was_speaking:
            # respond()'s finally-block normally restores state, but it is
            # blocked inside tts.speak(); nudge it here so the UI updates
            # without waiting for playback to unwind.
            if self.state == AuraState.SPEAKING:
                self._set_state(AuraState.ACTIVE)
        else:
            self._mute_requested.clear()

            if self.on_message:
                try:
                    self.on_message("AURA", "I wasn't saying anything, but noted.")
                except Exception:
                    pass


    def confirm(
        self,
        value: bool,
    ) -> None:
        """
        Programmatic confirmation from the UI.
        """

        if (
            self.state
            != AuraState.WAITING_FOR_CONFIRMATION
        ):

            return


        self._confirmation_result = bool(
            value
        )

        self._confirmation_event.set()


    # ============================================================
    # COMMAND PROCESSING
    # ============================================================

    def _process_command(
        self,
        text: str,
    ) -> None:

        self._touch_activity()

        self._mute_requested.clear()


        # --------------------------------------------------------
        # Save user message
        # --------------------------------------------------------

        try:

            memory_manager.add_conversation_turn(
                "user",
                text,
            )

        except Exception as e:

            logger.warning(
                f"Could not save user message: {e}"
            )


        # --------------------------------------------------------
        # Send user message to UI
        # --------------------------------------------------------

        if self.on_message:

            try:

                self.on_message(
                    "You",
                    text,
                )

            except Exception as e:

                logger.warning(
                    f"User message callback failed: {e}"
                )


        # High-confidence desktop controls bypass the network AI provider
        # entirely, which keeps sleep/app/volume controls responsive even
        # with no internet. This runs AFTER the user message has been
        # emitted, so a local command still appears exactly once in the
        # conversation and in memory.
        if self._handle_local_control(text):
            return

        self._set_state(AuraState.THINKING)

        # Fold in the clarifying question AURA asked a moment ago, if any,
        # so "DevOps tutorials" answers "What should I search for?".
        text = self._apply_pending_clarification(text)

        # --------------------------------------------------------
        # Build context
        # --------------------------------------------------------

        context = (
            memory_manager.build_context_string()
        )


        # --------------------------------------------------------
        # Intent parsing
        # --------------------------------------------------------

        try:

            actions = parse_intent(
                text,
                context=context,
                last_target=self._last_target,
                provider=self.provider,
            )

        except Exception as e:

            logger.exception(
                "Intent parsing failed."
            )

            self.respond(
                "Sorry, I couldn't process that request."
            )

            self._set_state(
                AuraState.ACTIVE
            )

            return


        # --------------------------------------------------------
        # Execute returned actions
        # --------------------------------------------------------

        for action in actions:

            if not action.valid:

                self.respond(
                    action.error
                    or
                    "Sorry, I didn't quite catch that. "
                    "Could you say it again?"
                )

                continue


            # ----------------------------------------------------
            # Unknown
            # ----------------------------------------------------

            if action.action == "UNKNOWN":

                self.respond(
                    action.response
                    or
                    "I'm not sure I understood that. "
                    "Could you try again?"
                )

                continue


            # ----------------------------------------------------
            # Conversation
            # ----------------------------------------------------

            if action.action == "CONVERSE":

                response_text = (
                    action.parameters
                    .get("text", "")
                    .strip()
                )


                response_text = response_text or "I'm here."

                self.respond(response_text)

                # If AURA just asked what to search for, treat the next
                # bare phrase as the answer (see _apply_pending_clarification).
                lowered = response_text.lower()
                if lowered.rstrip().endswith("?") and "search" in lowered:
                    if "youtube" in lowered:
                        self._set_pending_clarification("youtube_search")
                    else:
                        self._set_pending_clarification("web_search")

                continue


            # ----------------------------------------------------
            # Browser choice for general web searches.
            #
            # webbrowser.open() would otherwise fall through to
            # whatever Windows has set as the system default (often
            # Edge). If the command didn't already name a browser
            # ("...in chrome"), ask which one to use instead of
            # guessing.
            # ----------------------------------------------------

            if (
                action.action == "WEB_SEARCH"
                and not action.parameters.get("browser")
            ):

                inferred_browser = extract_browser_from_text(text)

                if inferred_browser:

                    action.parameters["browser"] = inferred_browser

                else:

                    chosen_browser = self._ask_browser_choice()

                    if not chosen_browser:

                        continue

                    action.parameters["browser"] = chosen_browser


            # ----------------------------------------------------
            # Confirmation
            # ----------------------------------------------------

            if action.requires_confirmation:

                confirmed = (
                    self._ask_confirmation(
                        action
                    )
                )


                if not confirmed:

                    continue


            # ----------------------------------------------------
            # Execute action
            # ----------------------------------------------------

            self._set_state(
                AuraState.EXECUTING
            )


            try:

                success, message = (
                    execute_action(
                        action
                    )
                )

            except Exception as e:

                logger.exception(
                    "Action execution failed."
                )

                success = False

                message = (
                    f"I couldn't complete that action: {e}"
                )


            # ----------------------------------------------------
            # Tell Electron about the REAL action.
            # ----------------------------------------------------

            if self.on_action:

                try:

                    self.on_action(
                        action.action,
                        message,
                        success,
                    )

                except Exception as e:

                    logger.warning(
                        "on_action callback raised: "
                        f"{e}"
                    )


            # ----------------------------------------------------
            # Normal AURA response
            # ----------------------------------------------------

            self.respond(
                message if success else warm_up_failure_message(message)
            )


            # ----------------------------------------------------
            # Remember created target.
            # ----------------------------------------------------

            if (
                success
                and action.action
                in (
                    "CREATE_FOLDER",
                    "CREATE_FILE",
                )
                and "path"
                in action.parameters
            ):

                self._last_target = {
                    "path":
                        action.parameters["path"]
                }


        # --------------------------------------------------------
        # Recent actions callback
        # --------------------------------------------------------

        if self.on_recent_actions_update:

            try:

                self.on_recent_actions_update()

            except Exception as e:

                logger.warning(
                    "on_recent_actions_update callback raised: "
                    f"{e}"
                )


        # --------------------------------------------------------
        # Give Piper a little time to finish before reopening mic.
        # --------------------------------------------------------

        time.sleep(
            POST_SPEECH_PAUSE
        )


        self._touch_activity()


        # --------------------------------------------------------
        # Return to ACTIVE.
        # --------------------------------------------------------

        if self.state != AuraState.STOPPED:

            self._set_state(
                AuraState.ACTIVE
            )


    # ============================================================
    # CONFIRMATION
    # ============================================================

    def _ask_confirmation(
        self,
        action,
    ) -> bool:

        prompt = (
            build_confirmation_prompt(
                action
            )
        )


        self.respond(
            prompt
        )


        self._confirmation_event.clear()

        self._confirmation_result = None


        self._set_state(
            AuraState.WAITING_FOR_CONFIRMATION
        )


        resolved = (
            self._confirmation_event.wait(
                timeout=self.confirmation_timeout
            )
        )


        self._touch_activity()


        if (
            not resolved
            or
            self._confirmation_result
            is None
        ):

            try:

                memory_manager.log_action(
                    prompt,
                    action.action,
                    False,
                    "No confirmation received (timeout).",
                )

            except Exception:

                pass


            self.respond(
                "I didn't receive a confirmation, "
                "so I've cancelled that."
            )


            return False


        if not self._confirmation_result:

            try:

                memory_manager.log_action(
                    prompt,
                    action.action,
                    False,
                    "Cancelled by user.",
                )

            except Exception:

                pass


            self.respond(
                "Okay, I've cancelled that."
            )


            return False


        return True


    def _resolve_confirmation(
        self,
        text: str,
    ) -> None:

        if is_affirmative(text):

            self._confirmation_result = True

            self._confirmation_event.set()

            return


        if is_negative(text):

            self._confirmation_result = False

            self._confirmation_event.set()

            return


        # Ambiguous response.
        self.respond(
            "Sorry, was that a yes or a no?"
        )


        if self.state != AuraState.STOPPED:

            self._set_state(
                AuraState.WAITING_FOR_CONFIRMATION
            )


    # ============================================================
    # BROWSER CHOICE
    # ============================================================

    def _ask_browser_choice(
        self,
    ) -> Optional[str]:
        """
        Ask which browser to use for a web search, then wait for a
        spoken or typed reply and fuzzy-match it to a known browser.

        Returns the canonical browser id (e.g. "chrome", "brave") or
        None if the question timed out or was cancelled, in which
        case the caller should skip executing the search.
        """

        self.respond(
            "Which browser should I use — Chrome or Brave?"
        )

        self._browser_choice_event.clear()

        self._browser_choice_result = None

        self._set_state(
            AuraState.WAITING_FOR_BROWSER_CHOICE
        )

        resolved = (
            self._browser_choice_event.wait(
                timeout=self.browser_choice_timeout
            )
        )

        self._touch_activity()

        if (
            not resolved
            or
            not self._browser_choice_result
        ):

            self.respond(
                "I didn't catch a browser, so I've cancelled the search."
            )

            return None

        return self._browser_choice_result


    def _resolve_browser_choice(
        self,
        text: str,
    ) -> None:

        browser = match_browser_name(text)

        if browser:

            self._browser_choice_result = browser

            self._browser_choice_event.set()

            return


        # Unrecognized reply — ask again rather than guessing.
        self.respond(
            "Sorry, I didn't catch that. Chrome or Brave?"
        )

        if self.state != AuraState.STOPPED:

            self._set_state(
                AuraState.WAITING_FOR_BROWSER_CHOICE
            )


    # ============================================================
    # VOICE LOOP
    # ============================================================

    def _voice_loop(self) -> None:
        """
        Continuous microphone loop.

        ACTIVE:
            listen for commands.

        SLEEPING:
            listen only for wake word.

        WAITING_FOR_CONFIRMATION:
            listen for yes/no.

        WAITING_FOR_BROWSER_CHOICE:
            listen for a browser name.

        THINKING / SPEAKING / EXECUTING:
            microphone stays closed.
        """

        logger.info("AURA voice loop started.")

        consecutive_errors = 0

        while not self._stop_event.is_set():

            try:
                self._voice_loop_tick()
                consecutive_errors = 0

            except Exception:
                # A microphone glitch, a Whisper failure, or a bad audio
                # buffer must never take the loop (or the process) down.
                # Back off briefly and carry on listening.
                consecutive_errors += 1

                logger.exception(
                    "Voice loop iteration failed "
                    f"(consecutive failures: {consecutive_errors})."
                )

                time.sleep(min(2.0, 0.25 * consecutive_errors))

                if consecutive_errors >= 20:
                    logger.error(
                        "Voice loop failing repeatedly — pausing listening. "
                        "Use Start Listening to retry."
                    )
                    self._set_state(AuraState.STOPPED)
                    consecutive_errors = 0

        logger.info("AURA voice loop stopped.")


    def _voice_loop_tick(self) -> None:
        """One iteration of the voice loop. Kept separate so _voice_loop()
        can wrap it in a single exception boundary."""

        state = self.state

        if state == AuraState.ACTIVE:
            if (
                self.session_timeout > 0
                and time.monotonic() - self._last_activity_time >= self.session_timeout
            ):
                logger.info(
                    f"Session timeout reached ({self.session_timeout:.0f}s). "
                    "Returning to SLEEPING."
                )
                self._set_state(AuraState.SLEEPING)
                return

            self._active_cycle()


        elif (
            state == AuraState.SLEEPING
            and self.wake_word_enabled
        ):

            self._wake_word_cycle()


        elif (
            state
            == AuraState.WAITING_FOR_CONFIRMATION
        ):

            self._confirmation_cycle()


        elif (
            state
            == AuraState.WAITING_FOR_BROWSER_CHOICE
        ):

            self._browser_choice_cycle()


        elif state == AuraState.STOPPED:

            # Listening is off: no microphone, no Whisper, no CPU.
            time.sleep(0.2)


        elif state == AuraState.SLEEPING:

            # Sleeping with the wake word disabled — nothing to do.
            time.sleep(0.2)


        else:

            # THINKING / EXECUTING / SPEAKING: microphone stays closed.
            time.sleep(0.1)


    # ============================================================
    # VOICE ERROR RECOVERY
    # ============================================================

    def _recover_from_voice_error(
        self,
        entry_state: AuraState,
        error: Exception,
    ) -> None:
        """
        Handle a failed listen cycle without stomping on a state change
        that happened while the microphone was open.

        Two things this protects against, both seen in real runs:

        1. A cycle that started while ACTIVE fails *after* the user has
           said "sleep". The old code unconditionally set ACTIVE on the
           way out, silently undoing the sleep. Now the state is only
           restored if it hasn't moved on since the cycle began.

        2. A permanently broken microphone (no device, no PortAudio)
           flipping the UI into ERROR on every single cycle. ERROR is now
           only surfaced once the failures actually persist, and the
           retry backs off instead of spinning.
        """

        self._voice_error_count += 1

        logger.warning(
            f"Voice input error while {entry_state.value} "
            f"(failure {self._voice_error_count}): {error}"
        )

        # Only report ERROR once it looks like a real fault rather than a
        # single dropped buffer.
        if self._voice_error_count == _VOICE_ERRORS_BEFORE_ERROR_STATE:
            if self.state == entry_state:
                self._set_state(AuraState.ERROR)

        backoff = min(
            _MAX_VOICE_ERROR_BACKOFF,
            _ERROR_RECOVERY_PAUSE * self._voice_error_count,
        )
        time.sleep(backoff)

        if self._stop_event.is_set():
            return

        # Restore only if nothing else has claimed the state meanwhile.
        if self.state in (entry_state, AuraState.ERROR):
            if self.state != AuraState.STOPPED:
                self._set_state(entry_state)


    # ============================================================
    # WAKE WORD
    # ============================================================

    def _wake_word_cycle(
        self,
    ) -> None:
        """
        Sleeping microphone gate.

        Nothing reaches Groq/intent parser unless the
        deterministic wake-word detector accepts the transcript.
        """

        cycle_start = (
            time.monotonic()
        )


        try:

            raw_text = (
                self.stt.listen_for_wake_word(
                    should_continue=lambda: self._listening_allowed(
                        AuraState.SLEEPING
                    ),
                )
            )


        except VoiceInputError as e:

            self._recover_from_voice_error(AuraState.SLEEPING, e)

            return


        except Exception as e:

            logger.error(
                f"Unexpected error in wake-word cycle: {e}"
            )


            time.sleep(
                _ERROR_RECOVERY_PAUSE
            )


            return


        # A capture that returned at all means the microphone is working.
        self._voice_error_count = 0

        # --------------------------------------------------------
        # Minimum cycle guard
        # --------------------------------------------------------

        if (
            time.monotonic()
            - cycle_start
            < _MIN_LISTEN_CYCLE_SECONDS
        ):

            time.sleep(
                _MIN_LISTEN_CYCLE_SECONDS
            )


        # --------------------------------------------------------
        # State may have changed while recording.
        # --------------------------------------------------------

        if self.state != AuraState.SLEEPING:

            return


        if (
            not raw_text
            or
            not raw_text.strip()
        ):

            return


        # --------------------------------------------------------
        # Deterministic wake-word detection.
        # --------------------------------------------------------

        result = detect_wake_word(
            raw_text,
            wake_name=self.wake_name,
        )


        logger.info(
            f"Wake word detected: "
            f"{str(result.detected).lower()}"
        )


        logger.info(
            f'Remaining command: "{result.remainder}"'
        )


        # --------------------------------------------------------
        # NOT AURA -> discard.
        # --------------------------------------------------------

        if not result.detected:

            return


        # --------------------------------------------------------
        # Wake accepted.
        # --------------------------------------------------------

        try:

            self.stt.reset_wake_buffer()

        except Exception:

            pass


        self._touch_activity()


        self._set_state(
            AuraState.ACTIVE
        )


        # --------------------------------------------------------
        # "AURA, open Chrome"
        # --------------------------------------------------------

        if result.remainder:

            logger.info(
                f'Processing command: "{result.remainder}"'
            )


            self._dispatch_command_async(
                result.remainder
            )


        # --------------------------------------------------------
        # Just "AURA"
        # --------------------------------------------------------

        else:

            self._speak_and_return_to(
                "Yes?",
                AuraState.ACTIVE,
            )


    # ============================================================
    # ACTIVE VOICE CYCLE
    # ============================================================

    def _is_probable_tts_echo(self, text: str) -> bool:
        """Return True when a fresh transcript looks like AURA's own TTS."""
        if not text or not self._last_tts_text:
            return False

        if time.monotonic() > self._tts_echo_block_until:
            return False

        def normalize(value: str) -> str:
            return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

        current = normalize(text)
        spoken = normalize(self._last_tts_text)

        if not current or not spoken:
            return False

        if current == spoken:
            return True

        similarity = difflib.SequenceMatcher(
            None,
            current,
            spoken,
        ).ratio()

        return similarity >= 0.88

    def _active_cycle(
        self,
    ) -> None:
        """
        ACTIVE microphone cycle.

        Uses listen_until_silence() rather than a fixed recording
        duration.
        """

        cycle_start = (
            time.monotonic()
        )


        try:

            # Do not immediately reopen the microphone after TTS.
            # Discard the short audio tail already present in the mic path.
            text = (
                self.stt.listen_until_silence(
                    initial_discard_seconds=MICROPHONE_SETTLE_SECONDS,
                    on_speech_start=lambda: self._set_state(AuraState.HEARING),
                    should_continue=lambda: self._listening_allowed(
                        AuraState.ACTIVE,
                        AuraState.HEARING,
                    ),
                )
            )


        except VoiceInputError as e:

            if self.state == AuraState.HEARING:
                self._set_state(AuraState.ACTIVE)

            self._recover_from_voice_error(AuraState.ACTIVE, e)

            return


        except Exception as e:

            logger.error(
                f"Unexpected error in active-listen cycle: {e}"
            )


            if self.state == AuraState.HEARING:
                self._set_state(AuraState.ACTIVE)


            time.sleep(
                _ERROR_RECOVERY_PAUSE
            )


            return


        self._voice_error_count = 0

        # --------------------------------------------------------
        # Minimum cycle guard
        # --------------------------------------------------------

        if (
            time.monotonic()
            - cycle_start
            < _MIN_LISTEN_CYCLE_SECONDS
        ):

            time.sleep(
                _MIN_LISTEN_CYCLE_SECONDS
            )


        # --------------------------------------------------------
        # HEARING was only a brief "actively capturing speech" flash for
        # the UI — fold back to ACTIVE now that capture is done, before
        # deciding what to do with the transcription.
        # --------------------------------------------------------

        if self.state == AuraState.HEARING:
            self._set_state(AuraState.ACTIVE)

        # --------------------------------------------------------
        # Ignore stale audio.
        # --------------------------------------------------------

        if self.state != AuraState.ACTIVE:

            return


        # --------------------------------------------------------
        # Reject AURA's own TTS if Whisper captured speaker echo.
        # --------------------------------------------------------

        if self._is_probable_tts_echo(text):
            logger.info(
                f'Discarding probable TTS echo: "{text}"'
            )
            return

        # --------------------------------------------------------
        # Nothing was said.
        # --------------------------------------------------------

        if (
            not text
            or
            not text.strip()
        ):

            idle_time = (
                time.monotonic()
                - self._last_activity_time
            )


            if (
                idle_time
                >= self.session_timeout
            ):

                logger.info(
                    f"No activity for "
                    f"{self.session_timeout:.0f}s."
                )


                self._set_state(
                    AuraState.SLEEPING
                )


            return


        # --------------------------------------------------------
        # Real command.
        # --------------------------------------------------------

        self._touch_activity()


        logger.info(
            f'Command transcription: "{text}"'
        )


        self._dispatch_command_async(
            text
        )


    # ============================================================
    # CONFIRMATION VOICE CYCLE
    # ============================================================

    def _confirmation_cycle(
        self,
    ) -> None:

        cycle_start = (
            time.monotonic()
        )


        try:

            text = (
                self.stt.listen_until_silence(
                    max_wait_for_speech=
                        _CONFIRMATION_LISTEN_WAIT,

                    silence_hangover=
                        0.6,

                    max_record_seconds=
                        6.0,

                    should_continue=lambda: self._listening_allowed(
                        AuraState.WAITING_FOR_CONFIRMATION
                    ),
                )
            )


        except VoiceInputError as e:

            logger.warning(
                "Voice input error while awaiting "
                f"confirmation: {e}"
            )


            time.sleep(
                0.3
            )


            return


        except Exception as e:

            logger.error(
                "Unexpected error in "
                f"confirmation-listen cycle: {e}"
            )


            time.sleep(
                0.3
            )


            return


        if (
            time.monotonic()
            - cycle_start
            < _MIN_LISTEN_CYCLE_SECONDS
        ):

            time.sleep(
                _MIN_LISTEN_CYCLE_SECONDS
            )


        if (
            self.state
            != AuraState.WAITING_FOR_CONFIRMATION
        ):

            return


        if (
            text
            and
            text.strip()
        ):

            self.handle_input(
                text
            )


    # ============================================================
    # BROWSER CHOICE VOICE CYCLE
    # ============================================================

    def _browser_choice_cycle(
        self,
    ) -> None:

        cycle_start = (
            time.monotonic()
        )


        try:

            text = (
                self.stt.listen_until_silence(
                    max_wait_for_speech=
                        _CONFIRMATION_LISTEN_WAIT,

                    silence_hangover=
                        0.6,

                    max_record_seconds=
                        6.0,

                    should_continue=lambda: self._listening_allowed(
                        AuraState.WAITING_FOR_BROWSER_CHOICE
                    ),
                )
            )


        except VoiceInputError as e:

            logger.warning(
                "Voice input error while awaiting "
                f"browser choice: {e}"
            )


            time.sleep(
                0.3
            )


            return

        except Exception as e:

            logger.error(
                "Unexpected error in "
                f"browser-choice-listen cycle: {e}"
            )


            time.sleep(
                0.3
            )


            return


        if (
            time.monotonic()
            - cycle_start
            < _MIN_LISTEN_CYCLE_SECONDS
        ):

            time.sleep(
                _MIN_LISTEN_CYCLE_SECONDS
            )


        if (
            self.state
            != AuraState.WAITING_FOR_BROWSER_CHOICE
        ):

            return


        if (
            text
            and
            text.strip()
        ):

            self.handle_input(
                text
            )