"""
tests/test_agent_behavior.py

The behaviours the upgrade was actually asked for, tested at the agent
level with fake STT/TTS/provider — no microphone, no speaker, no network.

Each test here maps to a specific reported problem:

  * "AURA speaks but nothing appears in the UI"
  * "TTS failure kills the response / strands SPEAKING"
  * "Whisper hears AURA's own voice through the speakers"
  * "empty transcriptions still run the whole pipeline"
  * "sleep gets stuck"
  * "follow-ups need the whole command repeated"
"""

import threading
import time

import pytest

from app.core.agent import AuraAgent, AuraState


class FakeProvider:
    name = "fake"
    is_ai = True

    def __init__(self, scripted_response=None):
        self.scripted_response = scripted_response or {"actions": [{"action": "UNKNOWN"}]}
        self.calls = []

    def generate_actions(self, user_text, context=""):
        self.calls.append(user_text)
        return self.scripted_response

    def is_available(self):
        return True


class RecordingTTS:
    def __init__(self, delay=0.0, fail=False):
        self.spoken = []
        self.delay = delay
        self.fail = fail
        self.stop_calls = 0
        self.speaking = threading.Event()

    def speak(self, text):
        self.speaking.set()
        try:
            if self.fail:
                raise RuntimeError("piper exploded")
            self.spoken.append(text)
            if self.delay:
                time.sleep(self.delay)
        finally:
            self.speaking.clear()

    def stop(self):
        self.stop_calls += 1


class GatedSTT:
    """
    Records whether `should_continue` said listening was allowed at the
    moment each capture ran, which is how the microphone-gating tests
    observe the gate without any real audio.
    """

    def __init__(self):
        self.allowed_at_call = []
        self.command_calls = 0
        self.wake_calls = 0
        self.next_command = ""

    def listen_until_silence(self, should_continue=None, **kwargs):
        self.command_calls += 1
        self.allowed_at_call.append(bool(should_continue()) if should_continue else True)
        text, self.next_command = self.next_command, ""
        time.sleep(0.05)
        return text

    def listen_for_wake_word(self, should_continue=None, **kwargs):
        self.wake_calls += 1
        self.allowed_at_call.append(bool(should_continue()) if should_continue else True)
        time.sleep(0.05)
        return ""

    def reset_wake_buffer(self):
        pass


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    from app.memory import database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "behavior.db")
    yield


def _wait_until(predicate, timeout=3.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def make_agent(response=None, tts=None, stt=None, voice=False):
    provider = FakeProvider(response)
    tts = tts if tts is not None else RecordingTTS()
    agent = AuraAgent(
        provider=provider,
        stt=stt,
        tts=tts,
        voice_enabled=voice,
        confirmation_timeout=1.0,
        session_timeout=120.0,
    )
    return agent, provider, tts


# ==========================================================================
# Text output must always reach the UI
# ==========================================================================

def test_ui_receives_the_response_before_tts_is_attempted():
    """
    The reported bug was AURA speaking a response that never appeared on
    screen. Publishing to the UI must happen BEFORE synthesis starts, not
    after it finishes.
    """
    order = []

    class OrderTTS(RecordingTTS):
        def speak(self, text):
            order.append("tts")
            super().speak(text)

    agent, provider, tts = make_agent(
        response={"actions": [{"action": "CONVERSE", "parameters": {"text": "Hello there."}}]},
        tts=OrderTTS(),
    )
    agent.on_message = lambda speaker, text: order.append(f"ui:{speaker}")
    agent.start()
    order.clear()

    agent.handle_input("hi")

    assert "ui:AURA" in order
    assert order.index("ui:AURA") < order.index("tts"), order


def test_tts_failure_does_not_lose_the_text_response():
    """Piper failing must cost the user nothing but the audio."""
    messages = []
    agent, provider, tts = make_agent(
        response={"actions": [{"action": "CONVERSE", "parameters": {"text": "Still visible."}}]},
        tts=RecordingTTS(fail=True),
    )
    agent.on_message = lambda speaker, text: messages.append((speaker, text))
    agent.start()
    messages.clear()

    agent.handle_input("say something")

    assert ("AURA", "Still visible.") in messages


def test_tts_failure_does_not_crash_the_agent_or_strand_speaking():
    """SPEAKING must never become a terminal state."""
    agent, provider, tts = make_agent(
        response={"actions": [{"action": "CONVERSE", "parameters": {"text": "hello"}}]},
        tts=RecordingTTS(fail=True),
    )
    agent.start()

    agent.handle_input("say something")

    assert agent.state == AuraState.ACTIVE


def test_a_broken_ui_callback_does_not_stop_the_response():
    agent, provider, tts = make_agent(
        response={"actions": [{"action": "CONVERSE", "parameters": {"text": "hello"}}]},
    )
    agent.on_message = lambda speaker, text: (_ for _ in ()).throw(RuntimeError("UI died"))
    agent.start()

    agent.handle_input("say something")

    assert agent.state == AuraState.ACTIVE


# ==========================================================================
# Mute
# ==========================================================================

def test_typed_mute_stops_speech_immediately():
    tts = RecordingTTS(delay=1.0)
    agent, provider, _ = make_agent(
        response={"actions": [{"action": "CONVERSE", "parameters": {"text": "a long answer"}}]},
        tts=tts,
    )
    agent.start()

    worker = threading.Thread(target=lambda: agent.handle_input("talk to me"))
    worker.start()

    assert _wait_until(lambda: tts.speaking.is_set())

    agent.handle_input("mute")

    assert tts.stop_calls >= 1
    assert _wait_until(lambda: agent.state != AuraState.SPEAKING)
    worker.join(timeout=3)


def test_mute_is_accepted_even_while_a_command_is_being_processed():
    """Mute must bypass the busy lock — it's useless if it has to queue
    behind the very response it's trying to interrupt."""
    tts = RecordingTTS(delay=0.8)
    agent, provider, _ = make_agent(
        response={"actions": [{"action": "CONVERSE", "parameters": {"text": "long"}}]},
        tts=tts,
    )
    agent.start()

    worker = threading.Thread(target=lambda: agent.handle_input("talk"))
    worker.start()
    assert _wait_until(lambda: tts.speaking.is_set())

    agent.handle_input("mute")
    assert tts.stop_calls >= 1

    worker.join(timeout=3)


def test_mute_when_nothing_is_playing_is_harmless():
    agent, provider, tts = make_agent()
    agent.start()

    agent.handle_input("mute")

    assert agent.state != AuraState.SPEAKING


# ==========================================================================
# Microphone gating — AURA must not hear itself
# ==========================================================================

def test_microphone_is_not_allowed_while_speaking():
    stt = GatedSTT()
    agent, provider, tts = make_agent(stt=stt, voice=True)

    agent._set_state(AuraState.SPEAKING)
    assert agent._listening_allowed(AuraState.ACTIVE) is False

    agent._set_state(AuraState.THINKING)
    assert agent._listening_allowed(AuraState.ACTIVE) is False

    agent._set_state(AuraState.EXECUTING)
    assert agent._listening_allowed(AuraState.ACTIVE) is False

    agent._set_state(AuraState.STOPPED)
    assert agent._listening_allowed(AuraState.ACTIVE) is False

    agent._set_state(AuraState.ACTIVE)
    assert agent._listening_allowed(AuraState.ACTIVE) is True


def test_capture_in_flight_is_told_to_abort_when_aura_starts_speaking():
    """
    The gate is a live callback, not a one-off check — a recording that
    started while ACTIVE must be able to see that it should stop.
    """
    stt = GatedSTT()
    agent, provider, tts = make_agent(stt=stt, voice=True)
    agent._set_state(AuraState.ACTIVE)

    gate = lambda: agent._listening_allowed(AuraState.ACTIVE, AuraState.HEARING)
    assert gate() is True

    agent._set_state(AuraState.SPEAKING)
    assert gate() is False


def test_shutdown_closes_the_microphone_gate():
    stt = GatedSTT()
    agent, provider, tts = make_agent(stt=stt, voice=True)
    agent._set_state(AuraState.ACTIVE)

    agent._stop_event.set()

    assert agent._listening_allowed(AuraState.ACTIVE) is False


# ==========================================================================
# Empty / junk transcriptions
# ==========================================================================

def test_empty_transcription_never_reaches_the_provider():
    agent, provider, tts = make_agent()
    agent.start()
    provider.calls.clear()

    for junk in ("", "   ", "\n", None):
        agent.handle_input(junk)

    assert provider.calls == []


def test_empty_transcription_produces_no_spoken_response():
    agent, provider, tts = make_agent()
    agent.start()
    tts.spoken.clear()

    agent.handle_input("")

    assert tts.spoken == []


# ==========================================================================
# Sleep / wake
# ==========================================================================

def test_spoken_sleep_command_sleeps_without_calling_the_provider():
    stt = GatedSTT()
    agent, provider, tts = make_agent(stt=stt, voice=True)
    agent.start()
    provider.calls.clear()

    agent.handle_input("go to sleep")

    assert _wait_until(lambda: agent.state == AuraState.SLEEPING)
    assert provider.calls == []
    agent.shutdown()


def test_sleep_and_wake_never_get_stuck_across_many_cycles():
    stt = GatedSTT()
    agent, provider, tts = make_agent(stt=stt, voice=True)
    agent.start()

    for _ in range(5):
        agent.handle_input("sleep")
        assert _wait_until(lambda: agent.state == AuraState.SLEEPING)
        agent.handle_input("wake up")
        assert _wait_until(lambda: agent.state == AuraState.ACTIVE)

    agent.shutdown()


def test_stop_listening_then_wake_up_recovers():
    """The stuck case: microphone off, and "wake up" used to do nothing."""
    stt = GatedSTT()
    agent, provider, tts = make_agent(stt=stt, voice=True)
    agent.start()

    agent.stop_listening()
    assert agent.state == AuraState.STOPPED

    agent.handle_input("wake up")
    assert _wait_until(lambda: agent.state == AuraState.ACTIVE)
    agent.shutdown()


# ==========================================================================
# Follow-up context
# ==========================================================================

def test_bare_search_asks_what_to_search_for_then_uses_the_answer():
    """
    User: "search youtube"  -> AURA: "What would you like me to search for?"
    User: "DevOps tutorials" -> must become a real search, not a new topic.
    """
    agent, provider, tts = make_agent(
        response={"actions": [{"action": "CONVERSE", "parameters": {"text": "ok"}}]},
    )
    agent.start()
    tts.spoken.clear()

    agent.handle_input("search youtube")
    assert any("search for" in s.lower() for s in tts.spoken), tts.spoken

    provider.calls.clear()
    agent.handle_input("DevOps tutorials")

    assert provider.calls, "the follow-up never reached the provider"
    assert "youtube" in provider.calls[0].lower()
    assert "devops tutorials" in provider.calls[0].lower()


def test_a_follow_up_that_is_itself_a_command_wins_over_the_pending_question():
    """If the user changes their mind and issues a real command instead of
    answering, don't mangle it into a search query."""
    agent, provider, tts = make_agent(
        response={"actions": [{"action": "CONVERSE", "parameters": {"text": "ok"}}]},
    )
    agent.start()

    agent.handle_input("search the web")
    provider.calls.clear()

    agent.handle_input("what is my cpu usage")

    assert provider.calls == ["what is my cpu usage"]


def test_pending_question_expires_and_does_not_hijack_a_later_message():
    agent, provider, tts = make_agent(
        response={"actions": [{"action": "CONVERSE", "parameters": {"text": "ok"}}]},
    )
    agent.start()

    agent.handle_input("search the web")
    agent._pending_clarification_until = time.monotonic() - 1  # expire it
    provider.calls.clear()

    agent.handle_input("nice weather today")

    assert provider.calls == ["nice weather today"]


# ==========================================================================
# Resilience
# ==========================================================================

def test_provider_failure_falls_back_instead_of_crashing():
    class BrokenProvider(FakeProvider):
        def generate_actions(self, user_text, context=""):
            raise RuntimeError("groq is down")

    agent = AuraAgent(
        provider=BrokenProvider(),
        stt=None,
        tts=RecordingTTS(),
        voice_enabled=False,
        confirmation_timeout=1.0,
    )
    agent.start()

    agent.handle_input("open something")

    assert agent.state == AuraState.ACTIVE


def test_action_execution_failure_is_reported_not_raised():
    agent, provider, tts = make_agent(
        response={"actions": [{"action": "OPEN_APP", "parameters": {"app": "nonexistent-app-xyz"}}]},
    )
    agent.start()
    tts.spoken.clear()

    agent.handle_input("open nonexistent-app-xyz")

    assert agent.state == AuraState.ACTIVE
    assert tts.spoken, "AURA said nothing about the failure"


def test_help_is_answered_locally_without_the_provider():
    agent, provider, tts = make_agent()
    agent.start()
    provider.calls.clear()
    tts.spoken.clear()

    agent.handle_input("help")

    assert provider.calls == []
    assert any("CONVERSATION" in s or "can do" in s for s in tts.spoken)


# ==========================================================================
# Voice error recovery
# ==========================================================================

class FailingSTT:
    """A microphone that is simply broken — no device, no PortAudio."""

    def __init__(self):
        self.calls = 0

    def _fail(self, **kwargs):
        from app.voice.speech_to_text import VoiceInputError
        self.calls += 1
        raise VoiceInputError("no microphone")

    listen_until_silence = _fail
    listen_for_wake_word = _fail

    def reset_wake_buffer(self):
        pass


def test_a_failed_listen_cycle_does_not_undo_a_sleep_request():
    """
    Regression: a capture that began while ACTIVE and then failed used to
    unconditionally set ACTIVE on its way out — silently cancelling a
    "sleep" the user had issued while the microphone was still open.
    """
    agent, provider, tts = make_agent(stt=FailingSTT(), voice=True)
    agent._set_state(AuraState.SLEEPING)

    from app.voice.speech_to_text import VoiceInputError

    # A stale ACTIVE cycle finishing after the sleep already happened.
    agent._recover_from_voice_error(AuraState.ACTIVE, VoiceInputError("boom"))

    assert agent.state == AuraState.SLEEPING, (
        "a stale failed listen cycle overwrote the user's sleep request"
    )


def test_a_single_microphone_glitch_does_not_flip_the_ui_into_error():
    """One dropped buffer is noise, not a fault worth alarming about."""
    agent, provider, tts = make_agent(stt=FailingSTT(), voice=True)
    agent._set_state(AuraState.ACTIVE)

    from app.voice.speech_to_text import VoiceInputError
    agent._recover_from_voice_error(AuraState.ACTIVE, VoiceInputError("glitch"))

    assert agent.state == AuraState.ACTIVE


def test_persistent_microphone_failure_is_eventually_reported():
    """But a genuinely broken microphone must not be hidden either."""
    agent, provider, tts = make_agent(stt=FailingSTT(), voice=True)
    agent._set_state(AuraState.ACTIVE)

    from app.voice.speech_to_text import VoiceInputError

    seen = []
    original = agent._set_state
    agent._set_state = lambda st: (seen.append(st), original(st))[1]

    for _ in range(6):
        agent._recover_from_voice_error(AuraState.ACTIVE, VoiceInputError("dead"))

    assert AuraState.ERROR in seen


def test_a_broken_microphone_never_crashes_the_voice_loop():
    """The loop must survive a mic that fails on every single call."""
    stt = FailingSTT()
    agent, provider, tts = make_agent(stt=stt, voice=True)
    agent.start()

    time.sleep(0.8)

    assert agent._voice_thread.is_alive(), "voice loop died on microphone failure"
    agent.shutdown()
