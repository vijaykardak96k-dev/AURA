"""
AURA - Python backend / entry point.

Modes:
    normal       -> existing desktop GUI
    --text       -> console mode
    --electron   -> backend for the Electron UI

Electron is only the visual layer.

Python owns:
    Whisper
    Wake word
    Intent parsing
    Groq
    Security
    Actions
    Memory
    Piper TTS
"""

import argparse
import json
import os
import sys
import threading
import time
import importlib
import inspect

from dotenv import load_dotenv

load_dotenv()

from app.brain.provider import describe_active_provider, get_provider
from app.core.agent import AuraAgent, AuraState
from app.utils.logger import get_logger
from app.utils.paths import ensure_project_dirs


logger = get_logger()


# ================================================================
# ARGUMENTS
# ================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="AURA - Personal Desktop Assistant"
    )

    parser.add_argument(
        "--text",
        action="store_true",
        help="Run AURA in text console mode."
    )

    parser.add_argument(
        "--no-voice",
        action="store_true",
        help="Disable microphone and TTS."
    )

    parser.add_argument(
        "--electron",
        action="store_true",
        help="Run as backend for Electron UI."
    )

    return parser.parse_args()


# ================================================================
# ELECTRON EVENT OUTPUT
# ================================================================

ELECTRON_PREFIX = "AURA_UI_EVENT "


def emit_ui_event(event_type, data=None):
    """
    Send a JSON event to Electron through stdout.

    main.js watches for:
        AURA_UI_EVENT {...}
    """

    payload = {
        "type": event_type,
        "data": data or {}
    }

    try:
        print(
            ELECTRON_PREFIX
            + json.dumps(
                payload,
                ensure_ascii=True
            ),
            flush=True
        )

    except Exception as exc:
        logger.warning(
            f"Could not emit UI event: {exc}"
        )


# ================================================================
# ELECTRON BACKEND
# ================================================================

class ElectronBackend:

    def __init__(self):
        self.agent = None
        self.running = True
        self.command_thread = None

    # ------------------------------------------------------------
    # AURA -> Electron : state
    # ------------------------------------------------------------

    def on_state_change(self, state):
        if isinstance(state, AuraState):
            state_name = state.value
        else:
            state_name = str(state)

        emit_ui_event(
            "state",
            {
                "state": state_name
            }
        )

    # ------------------------------------------------------------
    # AURA -> Electron : messages
    # ------------------------------------------------------------

    def on_message(self, speaker, text):
        emit_ui_event(
            "message",
            {
                "speaker": speaker,
                "text": text
            }
        )

    # ------------------------------------------------------------
    # AURA -> Electron : action
    # ------------------------------------------------------------

    def on_action(self, action, message, success=True):
        emit_ui_event(
            "action",
            {
                "action": action,
                "message": message,
                "success": bool(success)
            }
        )

    # ------------------------------------------------------------
    # AURA -> Electron : per-utterance TTS status (Task 8)
    # ------------------------------------------------------------

    def on_tts_status(self, data):
        emit_ui_event("tts-utterance", data or {})

    # ------------------------------------------------------------
    # AURA -> Electron : initialization status
    # ------------------------------------------------------------

    def emit_voice_status(self, stt_available, tts_available):
        emit_ui_event(
            "voice-status",
            {
                "stt": bool(stt_available),
                "tts": bool(tts_available)
            }
        )

    # ------------------------------------------------------------
    # Create AURA
    # ------------------------------------------------------------

    def create_agent(self):

        logger.info(
            "Creating AURA Electron backend..."
        )

        provider = get_provider()

        provider_status = describe_active_provider(provider)

        logger.info(f"AI provider: {provider_status['detail']}")

        emit_ui_event("provider-status", provider_status)

        tts = None
        stt = None

        # ========================================================
        # TTS
        # ========================================================

        try:

            from app.voice.text_to_speech import (
                create_tts_engine
            )

            tts = create_tts_engine()

            if hasattr(tts, "preload"):
                tts.preload()

            logger.info(
                "TTS engine loaded."
            )

            emit_ui_event(
                "tts-status",
                {
                    "available": True
                }
            )

        except Exception as exc:

            logger.exception(
                "TTS initialization failed."
            )

            emit_ui_event(
                "tts-status",
                {
                    "available": False,
                    "error": str(exc)
                }
            )

        # ========================================================
        # STT
        # ========================================================

        try:

            stt_module = importlib.import_module(
                "app.voice.speech_to_text"
            )

            # ----------------------------------------------------
            # Try known factory names first
            # ----------------------------------------------------

            factory_names = [
                "create_stt_engine",
                "create_speech_to_text",
                "create_stt",
                "get_stt_engine",
                "create_voice_engine",
            ]

            for factory_name in factory_names:

                factory = getattr(
                    stt_module,
                    factory_name,
                    None
                )

                if not callable(factory):
                    continue

                try:

                    logger.info(
                        f"Trying STT factory: {factory_name}"
                    )

                    candidate = factory()

                    if candidate is not None:

                        stt = candidate

                        logger.info(
                            f"STT loaded using {factory_name}."
                        )

                        break

                except Exception as exc:

                    logger.warning(
                        f"STT factory {factory_name} failed: {exc}"
                    )

            # ----------------------------------------------------
            # If no factory exists, find a compatible class
            # ----------------------------------------------------

            if stt is None:

                candidate_names = [
                    "SpeechToText",
                    "SpeechRecognizer",
                    "WhisperSpeechToText",
                    "WhisperSTT",
                    "SpeechToTextEngine",
                    "VoiceEngine",
                ]

                for class_name in candidate_names:

                    cls = getattr(
                        stt_module,
                        class_name,
                        None
                    )

                    if not inspect.isclass(cls):
                        continue

                    if not (
                        hasattr(
                            cls,
                            "listen_until_silence"
                        )
                        and
                        hasattr(
                            cls,
                            "listen_for_wake_word"
                        )
                    ):
                        continue

                    try:

                        logger.info(
                            f"Trying STT class: {class_name}"
                        )

                        candidate = cls()

                        if candidate is not None:

                            stt = candidate

                            logger.info(
                                f"STT loaded using {class_name}."
                            )

                            break

                    except Exception as exc:

                        logger.warning(
                            f"STT class {class_name} failed: {exc}"
                        )

            if stt is None:
                raise RuntimeError(
                    "Could not locate a compatible STT engine."
                )

            # Load Whisper now rather than on the first spoken command, so
            # the user isn't waiting on a model load mid-sentence.
            try:
                if hasattr(stt, "preload"):
                    stt.preload()
                    logger.info("Whisper model preloaded.")
            except Exception as exc:
                logger.warning(f"Whisper preload failed: {exc}")

            emit_ui_event(
                "stt-status",
                {
                    "available": True
                }
            )

        except Exception as exc:

            logger.exception(
                "STT initialization failed."
            )

            emit_ui_event(
                "stt-status",
                {
                    "available": False,
                    "error": str(exc)
                }
            )

        # ========================================================
        # AURA AGENT
        # ========================================================

        session_timeout = float(
            os.environ.get(
                "AURA_SESSION_TIMEOUT_SECONDS",
                "120"
            )
        )

        confirmation_timeout = float(
            os.environ.get(
                "AURA_CONFIRMATION_TIMEOUT_SECONDS",
                "12"
            )
        )

        self.agent = AuraAgent(

            provider=provider,

            stt=stt,

            tts=tts,

            voice_enabled=(
                stt is not None
            ),

            wake_word_enabled=True,

            wake_name="aura",

            session_timeout=session_timeout,

            confirmation_timeout=confirmation_timeout,

            on_state_change=self.on_state_change,

            on_message=self.on_message,

            on_action=self.on_action,

            on_tts_status=self.on_tts_status,
        )

        logger.info(
            "AURA agent created successfully."
        )

    # ============================================================
    # Electron -> AURA
    # ============================================================

    def process_command(self, command):

        if not isinstance(command, dict):
            return

        command_type = command.get("type")

        # --------------------------------------------------------
        # Text command
        # --------------------------------------------------------

        if command_type == "command":

            text = str(
                command.get(
                    "text",
                    ""
                )
            ).strip()

            if not text:
                return

            logger.info(
                f"Electron command: {text}"
            )

            threading.Thread(
                target=self.agent.handle_input,
                args=(text,),
                daemon=True
            ).start()

            return

        # --------------------------------------------------------
        # Mute — stop TTS immediately
        # --------------------------------------------------------

        if command_type == "mute":

            logger.info("Electron requested MUTE.")

            try:
                self.agent._stop_speaking()
            except Exception as exc:
                logger.exception("Could not stop speech.")
                emit_ui_event("error", {"message": str(exc)})

            return


        # --------------------------------------------------------
        # Start listening
        # --------------------------------------------------------

        if command_type == "start_listening":

            logger.info(
                "Electron requested START LISTENING."
            )

            try:
                self.agent.start_listening()
            except Exception as exc:
                logger.exception(
                    "Could not start listening."
                )

                emit_ui_event(
                    "error",
                    {
                        "message": str(exc)
                    }
                )

            return

        # --------------------------------------------------------
        # Stop listening
        # --------------------------------------------------------

        if command_type == "stop_listening":

            logger.info(
                "Electron requested STOP LISTENING."
            )

            try:
                self.agent.stop_listening()
            except Exception as exc:
                logger.exception(
                    "Could not stop listening."
                )

                emit_ui_event(
                    "error",
                    {
                        "message": str(exc)
                    }
                )

            return

        # --------------------------------------------------------
        # Confirmation
        # --------------------------------------------------------

        if command_type == "confirm":

            value = bool(
                command.get(
                    "value",
                    False
                )
            )

            try:
                self.agent.confirm(value)

            except Exception as exc:

                logger.exception(
                    "Confirmation failed."
                )

                emit_ui_event(
                    "error",
                    {
                        "message": str(exc)
                    }
                )

            return

        # --------------------------------------------------------
        # Shutdown
        # --------------------------------------------------------

        if command_type == "shutdown":

            logger.info(
                "Electron requested shutdown."
            )

            self.running = False

            try:
                self.agent.shutdown()
            except Exception:
                logger.exception(
                    "Error shutting down AURA."
                )

            return

    # ============================================================
    # Electron stdin reader
    # ============================================================

    def stdin_loop(self):

        logger.info(
            "Electron command reader started."
        )

        while self.running:

            try:

                line = sys.stdin.readline()

            except Exception as exc:

                logger.error(
                    f"stdin read error: {exc}"
                )

                break

            if not line:
                time.sleep(0.05)
                continue

            line = line.strip()

            if not line:
                continue

            try:

                command = json.loads(line)

            except json.JSONDecodeError as exc:

                logger.warning(
                    f"Ignoring invalid Electron command: {exc}"
                )

                continue

            try:

                self.process_command(
                    command
                )

            except Exception as exc:

                logger.exception(
                    "Electron command processing failed."
                )

                emit_ui_event(
                    "error",
                    {
                        "message": str(exc)
                    }
                )

    # ============================================================
    # Run Electron backend
    # ============================================================

    def run(self):

        ensure_project_dirs()

        emit_ui_event(
            "backend-status",
            {
                "connected": False,
                "starting": True
            }
        )

        # --------------------------------------------------------
        # Create AURA
        # --------------------------------------------------------

        try:

            self.create_agent()

        except Exception as exc:

            logger.exception(
                "Could not create AURA agent."
            )

            emit_ui_event(
                "backend-status",
                {
                    "connected": False,
                    "starting": False
                }
            )

            emit_ui_event(
                "error",
                {
                    "message":
                        f"AURA initialization failed: {exc}"
                }
            )

            return

        # --------------------------------------------------------
        # Tell Electron backend is alive
        # --------------------------------------------------------

        emit_ui_event(
            "backend-status",
            {
                "connected": True,
                "starting": False
            }
        )

        # --------------------------------------------------------
        # Start command reader
        # --------------------------------------------------------

        self.command_thread = threading.Thread(
            target=self.stdin_loop,
            daemon=True
        )

        self.command_thread.start()

        # --------------------------------------------------------
        # Start AURA
        # --------------------------------------------------------

        try:

            logger.info(
                "Starting AURA agent..."
            )

            self.agent.start()

        except Exception as exc:

            logger.exception(
                "AURA failed during startup."
            )

            emit_ui_event(
                "error",
                {
                    "message":
                        f"AURA startup failed: {exc}"
                }
            )

            self.running = False

        # --------------------------------------------------------
        # Keep backend alive
        # --------------------------------------------------------

        while self.running:

            try:

                time.sleep(0.5)

            except KeyboardInterrupt:

                self.running = False

                break

        # --------------------------------------------------------
        # Shutdown
        # --------------------------------------------------------

        try:

            if self.agent is not None:
                self.agent.shutdown()

        except Exception:

            logger.exception(
                "Error during final AURA shutdown."
            )

        emit_ui_event(
            "backend-status",
            {
                "connected": False,
                "stopping": True
            }
        )


# ================================================================
# TEXT MODE
# ================================================================

def run_text_mode(voice_output_enabled=True):

    ensure_project_dirs()

    tts = None

    if voice_output_enabled:

        try:

            from app.voice.text_to_speech import (
                create_tts_engine
            )

            tts = create_tts_engine()

            if hasattr(tts, "preload"):
                tts.preload()

        except Exception as exc:

            logger.warning(
                f"TTS unavailable in text mode: {exc}"
            )

    provider = get_provider()

    agent = AuraAgent(

        provider=provider,

        stt=None,

        tts=tts,

        voice_enabled=False,

        session_timeout=float(
            os.environ.get(
                "AURA_SESSION_TIMEOUT_SECONDS",
                "120"
            )
        ),

        confirmation_timeout=float(
            os.environ.get(
                "AURA_CONFIRMATION_TIMEOUT_SECONDS",
                "12"
            )
        ),

        on_message=lambda speaker, text:
            print(
                f"{speaker}: {text}"
            ),
    )

    print()
    print("AURA")
    print("-" * 40)
    print(
        "AURA is ready."
    )
    print(
        "Type a command, or 'exit' to quit."
    )
    print("-" * 40)

    agent.start()

    while True:

        try:

            text = input(
                "\nYou: "
            ).strip()

        except (
            EOFError,
            KeyboardInterrupt
        ):

            print(
                "\nAURA: Goodbye."
            )

            agent.shutdown()

            break

        if not text:
            continue

        if text.lower() in (
            "exit",
            "quit",
            "bye"
        ):

            print(
                "AURA: Goodbye."
            )

            agent.shutdown()

            break

        if (
            agent.state
            == AuraState.WAITING_FOR_CONFIRMATION
        ):

            agent.handle_input(text)

            continue

        if (
            agent.state
            == AuraState.WAITING_FOR_BROWSER_CHOICE
        ):

            agent.handle_input(text)

            continue

        threading.Thread(
            target=agent.handle_input,
            args=(text,),
            daemon=True
        ).start()


# ================================================================
# MAIN
# ================================================================

def main():

    args = parse_args()

    if args.electron:

        backend = ElectronBackend()

        backend.run()

        return

    if args.text:

        run_text_mode(
            voice_output_enabled=(
                not args.no_voice
            )
        )

        return

    # ------------------------------------------------------------
    # Existing Tkinter GUI
    # ------------------------------------------------------------

    try:

        from app.ui.main_window import launch

        launch(
            voice_enabled=(
                not args.no_voice
            )
        )

    except Exception as exc:

        logger.warning(
            f"Tkinter GUI unavailable: {exc}"
        )

        run_text_mode(
            voice_output_enabled=(
                not args.no_voice
            )
        )


if __name__ == "__main__":
    main()