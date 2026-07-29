from mash_core import estimate_cost


def test_known_model_computes_input_and_output_cost():
    # claude-sonnet-4-6: $3/1M input, $15/1M output.
    cost = estimate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert cost == 3.0 + 15.0


def test_unknown_model_returns_zero():
    assert estimate_cost("nonexistent-model", 1000, 1000) == 0.0


def test_none_token_counts_treated_as_zero():
    assert estimate_cost("claude-sonnet-4-6", None, None) == 0.0


def test_3model_panel_members_have_nonzero_pricing():
    # These are the exact model_ids used by the published panel-3model runs
    # (see judges/*.py build_judge_model). Before these were priced, every
    # panel run silently reported total_cost_usd=0.0 regardless of real spend.
    for model_id in (
        "openrouter:meta-llama/llama-3.3-70b-instruct",
        "openrouter:qwen/qwen-2.5-72b-instruct",
        "openrouter:deepseek/deepseek-chat",
    ):
        assert estimate_cost(model_id, 1_000_000, 1_000_000) > 0.0
