"""tests/test_emotion.py — pure logic, no audio/AI involved."""

from app.brain.emotion import EMOTIONS, select_emotion


def test_returns_only_known_emotions_for_arbitrary_text():
    for text in ["", "hello", "I am extremely upset about the format command",
                 "Congratulations on the launch!!!"]:
        assert select_emotion(reply_text=text) in EMOTIONS


def test_valid_ai_emotion_field_wins():
    assert select_emotion(reply_text="Done.", ai_emotion="playful") == "playful"


def test_invalid_ai_emotion_falls_through_to_rules_not_raised():
    result = select_emotion(reply_text="Done.", ai_emotion="furious")
    assert result in EMOTIONS
    assert result != "furious"


def test_user_terrible_day_is_empathetic():
    assert select_emotion(reply_text="I'm sorry to hear that.",
                           user_text="I had a terrible day") == "empathetic"


def test_user_teasing_is_playful():
    assert select_emotion(reply_text="Harsh. I'll try harder.",
                           user_text="You're basically my unpaid employee") == "playful"


def test_dangerous_confirmation_reply_is_serious():
    assert select_emotion(reply_text="This will shut down your computer. Confirm?") == "serious"


def test_unavailable_capability_reply_is_concerned():
    assert select_emotion(
        reply_text="I don't currently have access to your Kubernetes cluster."
    ) == "concerned"


def test_unknown_input_defaults_to_neutral():
    assert select_emotion(reply_text="Task queued for processing.") == "neutral"


def test_outcome_used_only_as_last_resort():
    # No text signal at all -> outcome decides.
    assert select_emotion(reply_text="", user_text="", outcome="success") == "confident"
    assert select_emotion(reply_text="", user_text="", outcome="error") == "concerned"
    # A clearer text signal still wins over outcome.
    assert select_emotion(reply_text="", user_text="I had a terrible day",
                           outcome="success") == "empathetic"


def test_never_raises_on_none_inputs():
    assert select_emotion(reply_text=None, user_text=None, ai_emotion=None, outcome=None) == "neutral"
