"""モジュール import 回帰テスト"""


def test_api_module_imports():
    """realtime_translator.api が正常に import できること"""
    from realtime_translator.api import ApiWorker, ApiRequest
    assert ApiWorker is not None
    assert ApiRequest is not None
