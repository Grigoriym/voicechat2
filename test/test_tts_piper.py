# Regression coverage for the bug documented in CLAUDE.md: the reference
# Piper TTS server (test/piper-server.py, Python `piper` package) stripped
# all non-ASCII text, silently destroying German umlauts/ß. clean_text()
# here is the fixed version - it must never strip valid German characters.


def test_clean_text_preserves_umlauts_and_eszett(tts_piper):
    text = "Grüße aus München, schön dass es klappt!"
    assert tts_piper.clean_text(text) == text


def test_clean_text_strips_parenthetical_asides(tts_piper):
    assert tts_piper.clean_text("Hallo (leise) Welt") == "Hallo  Welt"


def test_clean_text_strips_markdown_emphasis(tts_piper):
    assert tts_piper.clean_text("Das ist *wichtig* und _auch das_.") == "Das ist  und ."


def test_clean_text_converts_tilde_runs_to_exclamation(tts_piper):
    assert tts_piper.clean_text("Wow~~~") == "Wow!"


def test_clean_text_strips_surrounding_whitespace(tts_piper):
    assert tts_piper.clean_text("  Hallo Welt  ") == "Hallo Welt"


def test_clean_text_empty_input_stays_empty(tts_piper):
    assert tts_piper.clean_text("   ") == ""
