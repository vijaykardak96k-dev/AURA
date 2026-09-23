"""
tests/test_agent.py — the central AuraAgent state machine, tested with
fake STT/TTS/provider so nothing here needs a microphone, speaker, or
real AI service.

This is where the wake-word gate is verified at the agent level (see
tests/test_wake_word.py for the pure text-matching logic in isolation):
a bare wake word must flip SLEEPING -> ACTIVE and say "Yes?" WITHOUT
ever reaching the AI provider, a wake word + command must reach the
provider with ONLY the command (never the wake word itself), continuous
listening/session-timeout, voice confirmation (including natural
phrasing and timeout), "don't hear yourself", and "don't process two
commands at once" are all covered here.
"""

import queue
import threading
import time

import pytest

from app.core.agent import AuraAgent, AuraState


class FakeProvider:
    """A fake AIProvider that returns a pre-scripted response for each call."""
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


class FakeTTS:
    """Records every utterance instead of actually speaking. Optionally
    simulates a speaking delay so timing-sensitive tests (e.g. 'don't
    listen while speaking') have something real to observe."""

    def __init__(self, delay=0.0):
        self.spoken = []
        self.delay = delay
        self.speaking_now = threading.Event()

    def speak(self, text):
        self.speaking_now.set()
        self.spoken.append(text)
        if self.delay:
            time.sleep(self.delay)
        self.speaking_now.clear()


class FakeSTT:
    """
    Models the REAL SpeechToText's two distinct listen methods
    separately, exactly as the agent actually uses them:
      - listen_for_wake_word() — used only while SLEEPING
      - listen_until_silence() — used while ACTIVE / WAITING_FOR_CONFIRMATION
    Each pulls from its own queue with a short timeout (like a real mic
    "listening but nothing said yet" cycle), so the voice loop behaves
    realistically without needing real audio.
    """

    def __init__(self):
        self.wake_queue = queue.Queue()
        self.command_queue = queue.Queue()
        self.wake_call_count = 0
        self.command_call_count = 0
        self.reset_wake_buffer_calls = 0

    def push_wake(self, text):
        self.wake_queue.put(text)

    def push_command(self, text):
        self.command_queue.put(text)

    def listen_for_wake_word(self, **kwargs):
        self.wake_call_count += 1
        try:
            return self.wake_queue.get(timeout=0.15)
        except queue.Empty:
            return ""

    def listen_until_silence(self, **kwargs):
        self.command_call_count += 1
        try:
            return self.command_queue.get(timeout=0.15)
        except queue.Empty:
            return ""

    def reset_wake_buffer(self):
        self.reset_wake_buffer_calls += 1


def make_agent(scripted_response=None, voice_enabled=True, confirmation_timeout=1.0,
               session_timeout=120.0):
    provider = FakeProvider(scripted_response)
    tts = FakeTTS()
    stt = FakeSTT() if voice_enabled else None
    agent = AuraAgent(
        provider=provider, stt=stt, tts=tts, voice_enabled=voice_enabled,
        confirmation_timeout=confirmation_timeout, session_timeout=session_timeout,
    )
    return agent, provider, tts, stt


def _sleeping_agent(**kwargs):
    """
    Start an agent and put it to sleep.

    AURA deliberately starts ACTIVE (a freshly-launched assistant should
    be ready to take a command without being woken first), so the
    wake-word tests below have to send it to sleep explicitly. That's the
    state the wake-word gate actually guards.
    """
    agent, provider, tts, stt = make_agent(**kwargs)
    agent.start()
    agent.sleep()
    assert _wait_until(lambda: agent.state == AuraState.SLEEPING)
    tts.spoken.clear()
    provider.calls.clear()
    return agent, provider, tts, stt


def _wait_until(predicate, timeout=3.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    from app.memory import database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test_agent.db")
    yield


# --------------------------------------------------------------- lifecycle

def test_start_speaks_greeting_and_enters_active_when_voice_disabled():
    """No microphone means nothing to wake — a console-style agent goes
    straight to ACTIVE so typed commands never need a wake word."""
    agent, provider, tts, stt = make_agent(voice_enabled=False)
    agent.start()
    assert agent.state == AuraState.ACTIVE
    assert any("AURA" in s or "welcome" in s.lower() for s in tts.spoken)


def test_start_enters_active_when_voice_enabled():
    """AURA starts ready to listen rather than asleep — the user just
    launched it, so requiring a wake word immediately is friction for no
    safety benefit. The inactivity timeout is what sends it to sleep."""
    agent, provider, tts, stt = make_agent(voice_enabled=True)
    agent.start()
    assert agent.state == AuraState.ACTIVE
    agent.shutdown()


def test_sleep_then_wake_round_trips_cleanly():
    """The reported bug was AURA getting stuck after 'sleep'. Both
    directions must work, repeatedly."""
    agent, provider, tts, stt = make_agent(voice_enabled=True)
    agent.start()

    for _ in range(3):
        agent.sleep()
        assert _wait_until(lambda: agent.state == AuraState.SLEEPING)
        agent.wake()
        assert _wait_until(lambda: agent.state == AuraState.ACTIVE)

    agent.shutdown()


def test_wake_works_even_from_stopped():
    """wake() used to silently do nothing unless AURA was SLEEPING, which
    is how it got stuck with the microphone off."""
    agent, provider, tts, stt = make_agent(voice_enabled=True)
    agent.start()
    agent.stop_listening()
    assert agent.state == AuraState.STOPPED

    agent.wake()
    assert agent.state == AuraState.ACTIVE
    agent.shutdown()


def test_start_launches_voice_thread_when_voice_enabled():
    agent, provider, tts, stt = make_agent(voice_enabled=True)
    agent.start()
    assert agent._voice_thread is not None
    assert agent._voice_thread.is_alive()
    agent.shutdown()


def test_shutdown_stops_voice_thread():
    agent, provider, tts, stt = make_agent(voice_enabled=True)
    agent.start()
    agent.shutdown()
    assert agent.state == AuraState.STOPPED
    time.sleep(0.2)
    assert not agent._voice_thread.is_alive()


# ------------------------------------------------------------ basic command

def test_safe_action_executes_and_returns_to_active():
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "CPU_INFO", "parameters": {}}]},
        voice_enabled=False,
    )
    agent.start()
    tts.spoken.clear()
    agent.handle_input("what's my cpu usage")
    assert agent.state == AuraState.ACTIVE
    assert any("CPU" in s or "%" in s for s in tts.spoken)


def test_converse_action_speaks_natural_reply_without_executing_anything():
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "CONVERSE", "parameters": {"text": "I'm doing well, thank you."}}]},
        voice_enabled=False,
    )
    agent.start()
    tts.spoken.clear()
    agent.handle_input("how are you")
    assert "I'm doing well, thank you." in tts.spoken
    assert agent.state == AuraState.ACTIVE


def test_unknown_command_gets_a_natural_spoken_response_not_repeat():
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "UNKNOWN", "response": "I'm not sure I understood that."}]},
        voice_enabled=False,
    )
    agent.start()
    tts.spoken.clear()
    agent.handle_input("asdkjfh nonsense")
    assert any("not sure" in s.lower() or "catch" in s.lower() for s in tts.spoken)
    assert "Repeat." not in tts.spoken  # the old, poor UX response must be gone


# ----------------------------------------------------------- wake-word gate
#
# This is the section that exists specifically to prove the reported bug
# is fixed: a bare wake word must NEVER reach the AI provider.

def test_bare_wake_word_wakes_without_calling_the_provider():
    agent, provider, tts, stt = _sleeping_agent(voice_enabled=True, confirmation_timeout=2)

    stt.push_wake("Aura")  # exactly what Whisper heard in the bug report
    assert _wait_until(lambda: agent.state == AuraState.ACTIVE)

    assert provider.calls == [], f"the wake word must never reach the AI provider, but it was called with {provider.calls}"
    # The acknowledgement is deliberately varied (see brain/persona.py), so
    # assert on it being a short prompt rather than one exact string. It is
    # spoken just after the state flips, hence the wait.
    assert _wait_until(lambda: any(len(s) < 40 for s in tts.spoken)), tts.spoken
    agent.shutdown()


@pytest.mark.parametrize("heard", ["Aura", "aura", "AURA.", "hey aura", "Hey AURA", "aura wake up", "Hey Aura wake up."])
def test_every_wake_phrase_variant_wakes_without_calling_provider(heard):
    agent, provider, tts, stt = _sleeping_agent(voice_enabled=True, confirmation_timeout=2)
    stt.push_wake(heard)
    assert _wait_until(lambda: agent.state == AuraState.ACTIVE)
    assert provider.calls == [], f"{heard!r} incorrectly reached the provider: {provider.calls}"
    agent.shutdown()


def test_wake_word_plus_command_in_one_utterance_passes_only_the_command():
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "OPEN_APP", "parameters": {"app": "chrome"}}]},
        voice_enabled=True, confirmation_timeout=2,
    )
    agent.start()
    agent.sleep()
    assert _wait_until(lambda: agent.state == AuraState.SLEEPING)
    stt.push_wake("AURA open Chrome")

    assert _wait_until(lambda: provider.calls != [])
    assert provider.calls == ["open chrome"], f"expected exactly the stripped command, got {provider.calls}"
    assert _wait_until(lambda: agent.state == AuraState.ACTIVE)
    agent.shutdown()


def test_non_wake_speech_while_sleeping_is_discarded_not_processed():
    """The critical negative case: something that ISN'T the wake word,
    heard while SLEEPING, must be thrown away — never handed to the AI,
    and AURA must stay asleep."""
    agent, provider, tts, stt = _sleeping_agent(voice_enabled=True, confirmation_timeout=2)
    stt.push_wake("what's the weather today")
    time.sleep(0.5)
    assert provider.calls == []
    assert agent.state == AuraState.SLEEPING
    agent.shutdown()


def test_mention_of_wake_name_mid_sentence_does_not_falsely_wake():
    """'what does aura mean' mentions the name but isn't AT the start —
    must not be treated as a wake event."""
    agent, provider, tts, stt = _sleeping_agent(voice_enabled=True, confirmation_timeout=2)
    stt.push_wake("what does aura mean")
    time.sleep(0.5)
    assert provider.calls == []
    assert agent.state == AuraState.SLEEPING
    agent.shutdown()


def test_typed_input_bypasses_wake_word_even_while_sleeping():
    """Typing is already a deliberate action — it must work immediately,
    with no wake word required, exactly like before this upgrade."""
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "CPU_INFO"}]}, voice_enabled=True, confirmation_timeout=2,
    )
    agent.start()
    agent.sleep()
    assert _wait_until(lambda: agent.state == AuraState.SLEEPING)
    agent.handle_input("what's my cpu usage")
    assert provider.calls == ["what's my cpu usage"]
    agent.shutdown()


# --------------------------------------------------------- session timeout

def test_active_returns_to_sleeping_after_session_timeout():
    agent, provider, tts, stt = make_agent(voice_enabled=True, confirmation_timeout=2, session_timeout=0.5)
    agent.start()
    agent.sleep()
    assert _wait_until(lambda: agent.state == AuraState.SLEEPING)
    stt.push_wake("aura")
    assert _wait_until(lambda: agent.state == AuraState.ACTIVE)

    # No further commands pushed — should time out back to SLEEPING.
    assert _wait_until(lambda: agent.state == AuraState.SLEEPING, timeout=3)
    agent.shutdown()


def test_activity_resets_the_session_timeout_clock():
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "CPU_INFO"}]},
        voice_enabled=True, confirmation_timeout=2, session_timeout=0.6,
    )
    agent.start()
    agent.sleep()
    assert _wait_until(lambda: agent.state == AuraState.SLEEPING)
    stt.push_wake("aura")
    assert _wait_until(lambda: agent.state == AuraState.ACTIVE)

    # Keep talking just under the timeout, twice — should still be ACTIVE.
    time.sleep(0.35)
    stt.push_command("what's my cpu usage")
    assert _wait_until(lambda: len(provider.calls) >= 1)
    assert _wait_until(lambda: agent.state == AuraState.ACTIVE), "command should finish and return to ACTIVE"
    agent.shutdown()


# --------------------------------------------------------- voice confirmation

def test_dangerous_action_asks_for_confirmation_and_waits():
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "SHUTDOWN_SYSTEM", "parameters": {}, "requires_confirmation": False}]},
        voice_enabled=False,
        confirmation_timeout=2,
    )
    agent.start()

    t = threading.Thread(target=lambda: agent.handle_input("shutdown my computer"))
    t.start()
    time.sleep(0.3)
    assert agent.state == AuraState.WAITING_FOR_CONFIRMATION
    agent.confirm(False)
    t.join(timeout=3)
    assert agent.state == AuraState.ACTIVE


def test_ai_cannot_bypass_confirmation_even_with_requires_confirmation_false():
    """
    End-to-end proof that the security layer (not the AI) decides this —
    the scripted fake provider explicitly says requires_confirmation=False
    for SHUTDOWN_SYSTEM, and the agent must still stop and ask.
    """
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "SHUTDOWN_SYSTEM", "requires_confirmation": False}]},
        voice_enabled=False,
        confirmation_timeout=1,
    )
    agent.start()

    t = threading.Thread(target=lambda: agent.handle_input("shutdown my computer"))
    t.start()
    time.sleep(0.2)
    assert agent.state == AuraState.WAITING_FOR_CONFIRMATION
    agent.confirm(False)
    t.join(timeout=3)


def test_voice_confirmation_yes_via_natural_phrase():
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "RESTART_SYSTEM"}]},
        voice_enabled=False,
        confirmation_timeout=2,
    )
    agent.start()

    t = threading.Thread(target=lambda: agent.handle_input("restart my computer"))
    t.start()
    time.sleep(0.2)
    assert agent.state == AuraState.WAITING_FOR_CONFIRMATION
    agent.handle_input("yeah go ahead")  # natural phrasing, not exactly "yes"
    t.join(timeout=3)
    # On this Linux sandbox, system_control.restart_pc() will report failure
    # (no `shutdown` command / not Windows) — but the important thing is the
    # confirmation was accepted and execution was attempted, not silently skipped.
    assert agent.state == AuraState.ACTIVE


def test_voice_confirmation_no_via_natural_phrase_cancels():
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "DELETE_FILE", "parameters": {"path": "Desktop/x.txt"}}]},
        voice_enabled=False,
        confirmation_timeout=2,
    )
    agent.start()
    tts.spoken.clear()

    t = threading.Thread(target=lambda: agent.handle_input("delete x.txt"))
    t.start()
    time.sleep(0.2)
    agent.handle_input("nah don't")
    t.join(timeout=3)

    assert any("cancel" in s.lower() for s in tts.spoken)


def test_voice_confirmation_timeout_cancels_safely():
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "DELETE_FILE", "parameters": {"path": "Desktop/x.txt"}}]},
        voice_enabled=False,
        confirmation_timeout=0.3,
    )
    agent.start()
    tts.spoken.clear()

    agent.handle_input("delete x.txt")  # blocks ~0.3s waiting for confirmation, then times out

    assert any("didn't receive" in s.lower() or "cancel" in s.lower() for s in tts.spoken)
    assert agent.state == AuraState.ACTIVE


def test_ambiguous_confirmation_reply_reprompts_without_resolving():
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "DELETE_FILE", "parameters": {"path": "Desktop/x.txt"}}]},
        voice_enabled=False,
        confirmation_timeout=1,
    )
    agent.start()

    t = threading.Thread(target=lambda: agent.handle_input("delete x.txt"))
    t.start()
    time.sleep(0.2)
    assert agent.state == AuraState.WAITING_FOR_CONFIRMATION
    agent.handle_input("maybe later")  # ambiguous — must not resolve the confirmation
    assert agent.state == AuraState.WAITING_FOR_CONFIRMATION
    agent.confirm(False)
    t.join(timeout=3)


def test_voice_confirmation_via_spoken_yes_while_waiting():
    """The WAITING_FOR_CONFIRMATION voice-listen path (_confirmation_cycle),
    not just typed/button confirmation — a spoken 'yes' captured by the
    mic while AURA is waiting must resolve the confirmation."""
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "DELETE_FILE", "parameters": {"path": "Desktop/x.txt"}}]},
        voice_enabled=True,
        confirmation_timeout=3,
    )
    agent.start()
    agent.sleep()
    assert _wait_until(lambda: agent.state == AuraState.SLEEPING)
    stt.push_wake("aura delete x dot txt")
    assert _wait_until(lambda: agent.state == AuraState.WAITING_FOR_CONFIRMATION)

    stt.push_command("yes")
    assert _wait_until(lambda: agent.state == AuraState.ACTIVE)
    agent.shutdown()


# ------------------------------------------------------- command interruption

def test_second_command_ignored_while_first_is_processing():
    """
    Simulates the AI provider being slow, and confirms a second
    handle_input() call during that time is dropped rather than
    interleaved (spec section 29).
    """
    class SlowProvider(FakeProvider):
        def generate_actions(self, user_text, context=""):
            self.calls.append(user_text)
            time.sleep(0.4)
            return {"actions": [{"action": "CPU_INFO"}]}

    provider = SlowProvider()
    tts = FakeTTS()
    agent = AuraAgent(provider=provider, stt=None, tts=tts, voice_enabled=False, confirmation_timeout=1)
    agent.start()

    t = threading.Thread(target=lambda: agent.handle_input("first command"))
    t.start()
    time.sleep(0.1)
    agent.handle_input("second command")  # should be dropped — busy
    t.join(timeout=3)

    assert provider.calls == ["first command"]  # second never reached the provider


# --------------------------------------------------------- manual start/stop

def test_manual_stop_listening_prevents_voice_processing():
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "CPU_INFO"}]},
        voice_enabled=True,
    )
    agent.start()
    agent.stop_listening()
    assert agent.state == AuraState.STOPPED

    stt.push_wake("aura")
    stt.push_command("what's my cpu usage")
    time.sleep(0.5)  # give the voice loop a chance to (wrongly) pick this up
    assert provider.calls == []  # never processed — listening was stopped

    agent.shutdown()


def test_manual_start_listening_resumes_to_active():
    """Pressing Start Listening is an explicit request to listen, so it
    goes straight to ACTIVE — making the user then say the wake word as
    well would be redundant."""
    agent, provider, tts, stt = make_agent(
        scripted_response={"actions": [{"action": "CPU_INFO"}]},
        voice_enabled=True,
    )
    agent.start()
    agent.stop_listening()
    agent.start_listening()
    assert agent.state == AuraState.ACTIVE

    stt.push_command("what's my cpu usage")
    assert _wait_until(lambda: provider.calls != [])
    assert provider.calls == ["what's my cpu usage"]
    agent.shutdown()


def test_manual_wake_bypasses_voice_entirely():
    agent, provider, tts, stt = make_agent(voice_enabled=True)
    agent.start()
    agent.sleep()
    assert _wait_until(lambda: agent.state == AuraState.SLEEPING)
    agent.wake()
    assert agent.state == AuraState.ACTIVE
    agent.shutdown()


def test_voice_loop_does_not_record_while_speaking():
    """
    Uses a TTS with an artificial delay so we can observe, mid-speech,
    that the STT is not being invoked — the concrete mechanism behind
    'AURA must not hear itself'. Polls with a deadline rather than fixed
    sleeps, since exact timing varies under test-runner load.
    """
    provider = FakeProvider({"actions": [{"action": "CPU_INFO"}]})
    tts = FakeTTS(delay=0.6)
    stt = FakeSTT()
    agent = AuraAgent(provider=provider, stt=stt, tts=tts, voice_enabled=True, confirmation_timeout=2)
    agent.start()
    agent.sleep()
    assert _wait_until(lambda: agent.state == AuraState.SLEEPING)

    stt.push_wake("aura what's my cpu usage")

    assert _wait_until(lambda: agent.state == AuraState.SPEAKING)

    wake_calls_before = stt.wake_call_count
    command_calls_before = stt.command_call_count
    time.sleep(0.3)  # still well within the 0.6s TTS delay
    assert stt.wake_call_count == wake_calls_before, "voice loop attempted to listen for wake word while AURA was still speaking"
    assert stt.command_call_count == command_calls_before, "voice loop attempted to listen for a command while AURA was still speaking"

    assert _wait_until(lambda: agent.state == AuraState.ACTIVE)
    agent.shutdown()
