def test_create_session_returns_isolated_sessions(voicechat2):
    mgr = voicechat2.ConversationManager()
    a = mgr.create_session()
    b = mgr.create_session()
    assert a != b
    assert mgr.sessions[a] is not mgr.sessions[b]


def test_add_user_message_appends_and_increments_turn(voicechat2):
    mgr = voicechat2.ConversationManager()
    sid = mgr.create_session()
    mgr.add_user_message(sid, "Hallo")
    assert mgr.sessions[sid]["conversation"][-1] == {"role": "user", "content": "Hallo"}
    assert mgr.sessions[sid]["current_turn"] == 1


def test_add_ai_message_appends_and_increments_turn(voicechat2):
    mgr = voicechat2.ConversationManager()
    sid = mgr.create_session()
    mgr.add_ai_message(sid, "Guten Tag")
    assert mgr.sessions[sid]["conversation"][-1] == {"role": "assistant", "content": "Guten Tag"}
    assert mgr.sessions[sid]["current_turn"] == 1


def test_audio_buffer_round_trip(voicechat2):
    mgr = voicechat2.ConversationManager()
    sid = mgr.create_session()
    mgr.add_to_audio_buffer(sid, b"abc")
    mgr.add_to_audio_buffer(sid, b"def")
    assert mgr.get_and_clear_audio_buffer(sid) == b"abcdef"
    assert mgr.sessions[sid]["audio_buffer"] == b""


def test_clean_old_sessions_removes_only_stale_sessions(voicechat2):
    mgr = voicechat2.ConversationManager()
    stale = mgr.create_session()
    fresh = mgr.create_session()
    mgr.sessions[stale]["last_activity"] -= mgr.session_timeout + 1

    mgr.clean_old_sessions()

    assert stale not in mgr.sessions
    assert fresh in mgr.sessions


def test_calculate_latencies(voicechat2):
    mgr = voicechat2.ConversationManager()
    sid = mgr.create_session()
    for metric, value in {
        "start_time": 0,
        "srt_start": 1,
        "srt_end": 2,
        "llm_start": 2,
        "llm_first_token": 3,
        "llm_first_sentence": 4,
        "tts_start": 4,
        "tts_end": 5,
        "first_audio_response": 5,
    }.items():
        mgr.update_latency_metric(sid, metric, value)

    assert mgr.calculate_latencies(sid) == {
        "total_voice_to_voice": 5,
        "srt_duration": 1,
        "llm_ttft": 1,
        "llm_ttfs": 2,
        "tts_duration": 1,
    }
