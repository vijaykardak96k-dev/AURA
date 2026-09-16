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
 Gemini / Groq / fallback
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
    THINKING
    SPEAKING
    WAITING_FOR_CONFIRMATION
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

# AURA stays ACTIVE for this long after the last meaningful activity.
DEFAULT_SESSION_TIMEOUT = 120.0

# Small delay after speech so trailing audio doesn't get interpreted
# as another command.
POST_SPEECH_PAUSE = 1.0

# Extra microphone settling time after AURA finishes speaking.
# This prevents the tail of AURA's own TTS audio from becoming a command.
MICROPHONE_SETTLE_SECONDS = 1.0

# Short window in which a transcript matching AURA's last spoken response
# is treated as speaker echo rather than a new user command.
TTS_ECHO_SUPPRESSION_SECONDS = 3.0

# While AURA is speaking, this small listener watches only for explicit
# interruption words. It lets the user say "stop" or "mute" without waiting
# for the response to finish.
SPEECH_INTERRUPT_POLL_SECONDS = 1.0
SPEECH_INTERRUPT_WORDS = {
    "stop", "mute", "quiet", "silence", "be quiet",
    "stop talking", "stop speaking", "shut up",
}

# Prevent a race between the voice loop and a command thread.
_INPUT_RACE_GRACE_SECONDS = 1.5

# Confirmation listening parameters.
_CONFIRMATION_LISTEN_WAIT = 3.0

# Error recovery.
_ERROR_RECOVERY_PAUSE = 1.0

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

    THINKING = "THINKING"

    SPEAKING = "SPEAKING"

    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"

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
        greeting: Optional[str] = None,
        on_state_change: Optional[Callable[[AuraState], None]] = None,
        on_message: Optional[Callable[[str, str], None]] = None,
        on_recent_actions_update: Optional[Callable[[], None]] = None,
        on_action: Optional[Callable[[str, str], None]] = None,
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
        self._tts_interrupt_event = threading.Event()
        self._tts_interrupt_thread = None


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
        Pause microphone listening.

        The process itself remains alive.
        """

        logger.info(
            "AURA listening stopped."
        )

        self._set_state(
            AuraState.STOPPED
        )


    def start_listening(self) -> None:
        """
        Resume microphone listening.

        Manual Start bypasses the wake-word gate and enters ACTIVE.
        """

        if self.state != AuraState.STOPPED:

            return


        if self.stt is not None:

            try:

                self.stt.reset_wake_buffer()

            except Exception:

                pass


        self._touch_activity()

        self._set_state(
            AuraState.ACTIVE
        )


    def wake(self) -> None:
        """
        Programmatically wake AURA.

        Equivalent to saying the wake word.
        """

        if self.state != AuraState.SLEEPING:

            return


        logger.info(
            "Manual wake triggered."
        )


        if self.stt is not None:

            try:

                self.stt.reset_wake_buffer()

            except Exception:

                pass


        self._touch_activity()

        self._set_state(
            AuraState.ACTIVE
        )


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
                self.on_action(action, message)
            except Exception as exc:
                logger.warning("Local action callback failed: %s", exc)

        self.respond(message)
        return True

    def _handle_local_control(self, text: str) -> bool:
        """Fast path for high-confidence controls that should never depend on Gemini/Groq."""
        normalized = self._normalize_command(text)

        # "sleep" means put AURA into its listening sleep mode. Explicit
        # Windows/PC sleep requests continue through SLEEP_SYSTEM.
        if normalized in {"sleep", "go to sleep", "aura sleep", "aura go to sleep", "sleep aura"}:
            self._touch_activity()
            self.respond("Okay. I'll go to sleep. Say AURA when you want me again.")
            if self.state != AuraState.STOPPED:
                self._set_state(AuraState.SLEEPING)
            return True

        if normalized in {"wake up", "wake aura", "aura wake up"}:
            if self.state == AuraState.SLEEPING:
                self.wake()
                self.respond("I'm awake.")
            else:
                self.respond("I'm already awake.")
            return True

        app_match = re.fullmatch(r"(?:open|launch|start)\s+(brave|brave browser|whatsapp|telegram)", normalized)
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

        return False

    def _listen_for_speech_interrupt(self) -> None:
        """Listen during TTS and stop it only for explicit interruption phrases."""
        if self.stt is None:
            return
        while not self._tts_interrupt_event.is_set() and not self._stop_event.is_set():
            try:
                text = self.stt.listen_for_interrupt(max_record_seconds=SPEECH_INTERRUPT_POLL_SECONDS)
            except Exception:
                time.sleep(0.15)
                continue
            normalized = self._normalize_command(text)
            if normalized in SPEECH_INTERRUPT_WORDS:
                logger.info("Speech interruption detected: %r", text)
                self._tts_interrupt_event.set()
                try:
                    stop = getattr(self.tts, "stop", None)
                    if callable(stop):
                        stop()
                except Exception as exc:
                    logger.warning("Could not stop TTS after interruption: %s", exc)
                return

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

        All user-facing responses go through here.
        """

        if not text:

            return


        text = str(text).strip()

        if not text:

            return


        # --------------------------------------------------------
        # UI
        # --------------------------------------------------------

        if (
            show_in_ui
            and self.on_message
        ):

            try:

                self.on_message(
                    "AURA",
                    text,
                )

            except Exception as e:

                logger.warning(
                    "on_message callback raised: "
                    f"{e}"
                )


        # --------------------------------------------------------
        # Memory
        # --------------------------------------------------------

        try:

            memory_manager.add_conversation_turn(
                "assistant",
                text,
            )

        except Exception as e:

            logger.warning(
                f"Could not save assistant message: {e}"
            )


        # --------------------------------------------------------
        # TTS
        # --------------------------------------------------------

        if (
            speak
            and self.voice_enabled_for_output()
        ):

            self._set_state(
                AuraState.SPEAKING
            )

            try:

                # Remember exactly what AURA just said so an echoed Whisper
                # transcript can be rejected before it reaches the command
                # parser.
                self._last_tts_text = text
                self._tts_echo_block_until = (
                    time.monotonic()
                    + TTS_ECHO_SUPPRESSION_SECONDS
                )

                self._tts_interrupt_event.clear()
                self._tts_interrupt_thread = None

                # Keep a tiny interrupt listener alive while Piper is speaking.
                # It listens only for explicit stop/mute phrases, so ordinary
                # speech and AURA's own audio are ignored.
                if self.stt is not None:
                    self._tts_interrupt_thread = threading.Thread(
                        target=self._listen_for_speech_interrupt,
                        name="AURA-TTS-Interrupt",
                        daemon=True,
                    )
                    self._tts_interrupt_thread.start()

                self.tts.speak(text)

                self._tts_interrupt_event.set()
                if self._tts_interrupt_thread is not None:
                    self._tts_interrupt_thread.join(timeout=0.4)
                    self._tts_interrupt_thread = None

            except Exception as e:

                logger.error(
                    f"TTS error: {e}"
                )


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

        # High-confidence desktop controls bypass the network AI provider.
        # This makes sleep/app/YouTube controls responsive even when Gemini
        # quota or Groq connectivity is unavailable.
        if self._handle_local_control(text):
            return

        self._touch_activity()


        self._set_state(
            AuraState.THINKING
        )


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


        # High-confidence desktop controls bypass the network AI provider.
        # This makes sleep/app/YouTube controls responsive even when Gemini
        # quota or Groq connectivity is unavailable. The user message has
        # already been emitted above, so local commands still appear once in
        # the conversation and memory.
        if self._handle_local_control(text):
            return

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


                self.respond(
                    response_text
                    or
                    "I'm here."
                )

                continue


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
                message
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

        THINKING / SPEAKING / EXECUTING:
            microphone stays closed.
        """

        logger.info(
            "AURA voice loop running."
        )


        logger.info(
            "AURA voice loop started."
        )


        while not self._stop_event.is_set():

            state = self.state


            if state == AuraState.ACTIVE:

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


            elif state == AuraState.STOPPED:

                time.sleep(
                    0.15
                )


            else:

                time.sleep(
                    0.15
                )


    # ============================================================
    # WAKE WORD
    # ============================================================

    def _wake_word_cycle(
        self,
    ) -> None:
        """
        Sleeping microphone gate.

        Nothing reaches Gemini/Groq/intent parser unless the
        deterministic wake-word detector accepts the transcript.
        """

        cycle_start = (
            time.monotonic()
        )


        try:

            raw_text = (
                self.stt.listen_for_wake_word()
            )


        except VoiceInputError as e:

            logger.warning(
                f"Voice input error while sleeping: {e}"
            )


            self._set_state(
                AuraState.ERROR
            )


            time.sleep(
                _ERROR_RECOVERY_PAUSE
            )


            if (
                not self._stop_event.is_set()
                and
                self.state
                != AuraState.STOPPED
            ):

                self._set_state(
                    AuraState.SLEEPING
                )


            return


        except Exception as e:

            logger.error(
                f"Unexpected error in wake-word cycle: {e}"
            )


            time.sleep(
                _ERROR_RECOVERY_PAUSE
            )


            return


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
                )
            )


        except VoiceInputError as e:

            logger.warning(
                f"Voice input error while active: {e}"
            )


            self._set_state(
                AuraState.ERROR
            )


            time.sleep(
                _ERROR_RECOVERY_PAUSE
            )


            if (
                not self._stop_event.is_set()
                and
                self.state
                != AuraState.STOPPED
            ):

                self._set_state(
                    AuraState.ACTIVE
                )


            return


        except Exception as e:

            logger.error(
                f"Unexpected error in active-listen cycle: {e}"
            )


            time.sleep(
                _ERROR_RECOVERY_PAUSE
            )


            return


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