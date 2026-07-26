from mash_core import JUDGE_MODEL_SETTINGS, JUDGE_REQUEST_TIMEOUT_S


def test_judge_model_settings_are_deterministic_and_bounded():
    assert JUDGE_MODEL_SETTINGS["temperature"] == 0.0
    assert JUDGE_MODEL_SETTINGS["timeout"] == JUDGE_REQUEST_TIMEOUT_S
    assert JUDGE_MODEL_SETTINGS["max_tokens"] == 2048


def test_judge_request_timeout_is_120_seconds():
    assert JUDGE_REQUEST_TIMEOUT_S == 120.0
