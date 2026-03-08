"""プロンプト生成のテスト"""
from realtime_translator.constants import SILENCE_SENTINEL
from realtime_translator.prompts import (
    build_prompt,
    build_stt_prompt,
    build_translation_prompt,
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
        assert "コンテキスト" in result

    def test_output_format_specified(self):
        result = build_prompt("listen", "ctx")
        assert "原文:" in result
        assert "訳文:" in result


class TestBuildSttPrompt:
    def test_listen_transcribes_english(self):
        result = build_stt_prompt("listen")
        assert "英語" in result
        assert "翻訳は不要" in result

    def test_speak_transcribes_japanese(self):
        result = build_stt_prompt("speak")
        assert "日本語" in result

    def test_silence_sentinel_included(self):
        result = build_stt_prompt("listen")
        assert SILENCE_SENTINEL in result


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

    def test_output_format_specified(self):
        result = build_translation_prompt("listen", "", "text")
        assert "訳文:" in result
