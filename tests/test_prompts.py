"""プロンプト生成のテスト"""
from realtime_translator.constants import SILENCE_SENTINEL
from realtime_translator.prompts import (
    build_prompt,
    build_stt_prompt,
    build_translation_prompt,
    build_retranslation_prompt,
    build_reply_assist_prompt,
    build_minutes_prompt,
)
from realtime_translator.auto_direction import (
    DirectionHeaderParser,
    normalize_stt_language,
    parse_direction_header,
)


class TestBuildPrompt:
    def test_listen_contains_english_to_japanese(self):
        result = build_prompt("listen", "テスト会議")
        assert "英語" in result
        assert "日本語" in result
        assert "テスト会議" in result

    def test_speak_contains_japanese_to_english(self):
        result = build_prompt("speak", "テスト会議")
        assert "日本語" in result
        assert "英語" in result

    def test_silence_instruction(self):
        result = build_prompt("listen", "")
        assert SILENCE_SENTINEL in result

    def test_empty_context(self):
        result = build_prompt("listen", "")
        assert "Context" in result

    def test_prompt_is_english(self):
        result = build_prompt("listen", "ctx")
        assert "You are a realtime translation assistant" in result
        assert "translate" in result.lower()

    def test_show_original_true_includes_format(self):
        result = build_prompt("listen", "ctx", show_original=True)
        assert "原文:" in result
        assert "訳文:" in result

    def test_show_original_false_translation_only(self):
        result = build_prompt("listen", "ctx", show_original=False)
        assert "原文:" not in result
        assert "訳文:" not in result
        assert "Output only the translation" in result

    def test_show_original_default_is_true(self):
        result = build_prompt("listen", "ctx")
        assert "原文:" in result

    def test_virtual_listen_auto_prompt_can_be_built(self):
        result = build_prompt("listen_auto", "ctx")
        assert "ctx" in result
        assert SILENCE_SENTINEL in result

    def test_build_prompt_for_llm_auto_contains_direction_and_translation_contract(self):
        prompt = build_prompt("listen_auto", "会議", show_original=False)
        assert "DIRECTION:" in prompt
        assert "TRANSLATION:" in prompt

    def test_build_prompt_for_fixed_direction_stream_still_returns_legacy_contract(self):
        prompt = build_prompt("listen_en_ja", "会議", show_original=True)
        assert "原文:" in prompt
        assert "訳文:" in prompt

    def test_build_prompt_requests_spoken_translation(self):
        prompt = build_prompt("listen_en_ja", "factory meeting", show_original=False)
        lowered = prompt.lower()
        assert "spoken" in lowered
        assert "natural" in lowered
        assert "negation" in lowered
        assert "conditions" in lowered

    def test_auto_direction_prompt_preserves_numbers_and_conditions(self):
        prompt = build_prompt("listen_auto", "factory meeting", show_original=False)
        lowered = prompt.lower()
        assert "numbers" in lowered
        assert "units" in lowered
        assert "conditions" in lowered


class TestBuildSttPrompt:
    def test_listen_transcribes_english(self):
        result = build_stt_prompt("listen")
        assert "英語" in result
        assert "Do not translate" in result

    def test_speak_transcribes_japanese(self):
        result = build_stt_prompt("speak")
        assert "日本語" in result

    def test_silence_sentinel_included(self):
        result = build_stt_prompt("listen")
        assert SILENCE_SENTINEL in result

    def test_prompt_is_english(self):
        result = build_stt_prompt("listen")
        assert "Transcribe" in result


class TestBuildTranslationPrompt:
    def test_listen_translates_to_japanese(self):
        result = build_translation_prompt("listen", "会議", "Hello world")
        assert "日本語" in result
        assert "Hello world" in result
        assert "会議" in result

    def test_speak_translates_to_english(self):
        result = build_translation_prompt("speak", "会議", "こんにちは")
        assert "英語" in result
        assert "こんにちは" in result

    def test_transcript_embedded_in_prompt(self):
        result = build_translation_prompt("listen", "", "test transcript")
        assert "test transcript" in result

    def test_output_only_translation(self):
        result = build_translation_prompt("listen", "", "text")
        assert "Output only the translation" in result

    def test_prompt_is_english(self):
        result = build_translation_prompt("listen", "ctx", "text")
        assert "Translate" in result

    def test_auto_translation_prompt_contains_direction_contract(self):
        result = build_translation_prompt("listen_auto", "ctx", "hello")
        assert "DIRECTION:" in result
        assert "TRANSLATION:" in result

    def test_build_translation_prompt_preserves_numbers_and_conditions(self):
        prompt = build_translation_prompt("listen_en_ja", "factory meeting", "torque is 10 Nm")
        lowered = prompt.lower()
        assert "do not change" in lowered
        assert "numbers" in lowered
        assert "units" in lowered
        assert "deadlines" in lowered


class TestBuildRetranslationPrompt:
    def test_contains_context(self):
        result = build_retranslation_prompt("listen", "会議", "history block")
        assert "会議" in result

    def test_contains_history_block(self):
        block = ">>> [英語→日本語] Hello → こんにちは"
        result = build_retranslation_prompt("listen", "ctx", block)
        assert block in result

    def test_contains_language_direction(self):
        result = build_retranslation_prompt("listen", "ctx", "block")
        assert "英語" in result
        assert "日本語" in result

    def test_output_only_translation(self):
        result = build_retranslation_prompt("listen", "ctx", "block")
        assert "Output only the translation" in result

    def test_prompt_is_english(self):
        result = build_retranslation_prompt("listen", "ctx", "block")
        assert "Re-translate" in result

    def test_build_retranslation_prompt_requests_spoken_translation(self):
        prompt = build_retranslation_prompt("listen_en_ja", "factory meeting", ">>> hello")
        lowered = prompt.lower()
        assert "spoken" in lowered
        assert "natural" in lowered


class TestBuildReplyAssistPrompt:
    def test_contains_context(self):
        result = build_reply_assist_prompt("生産会議", "history")
        assert "生産会議" in result

    def test_contains_history(self):
        block = "[英語→日本語] Hello → こんにちは"
        result = build_reply_assist_prompt("ctx", block)
        assert block in result

    def test_requests_three_suggestions(self):
        result = build_reply_assist_prompt("ctx", "history")
        assert "3" in result

    def test_requests_bilingual_output(self):
        result = build_reply_assist_prompt("ctx", "history")
        assert "[日本語]" in result
        assert "[English]" in result


class TestBuildMinutesPrompt:
    def test_new_minutes(self):
        result = build_minutes_prompt("会議", "history block")
        assert "会議" in result
        assert "history block" in result
        assert "Create new" in result
        assert "Previous Minutes" not in result

    def test_append_minutes(self):
        result = build_minutes_prompt("ctx", "history", previous_minutes="existing minutes")
        assert "existing minutes" in result
        assert "Append" in result
        assert "Previous Minutes" in result

    def test_empty_previous_is_new(self):
        result = build_minutes_prompt("ctx", "history", previous_minutes="")
        assert "Create new" in result

    def test_japanese_output_instruction(self):
        result = build_minutes_prompt("ctx", "history")
        assert "Japanese" in result


class TestAutoDirection:
    def test_parse_direction_header_accepts_crlf(self):
        event = parse_direction_header("DIRECTION: en_ja\r\n")
        assert event.resolved_direction == "en_ja"

    def test_parse_direction_header_buffers_partial_chunks_until_newline(self):
        parser = DirectionHeaderParser()
        assert parser.feed("DIREC") is None
        event = parser.feed("TION: en_ja\r\n")
        assert event.resolved_direction == "en_ja"

    def test_normalize_stt_language_handles_unknown_and_regional_codes(self):
        assert normalize_stt_language("en-AU") == "en"
        assert normalize_stt_language("zh-CN") is None
        assert normalize_stt_language("") is None
        assert normalize_stt_language(None) is None
