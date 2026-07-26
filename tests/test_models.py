from mash_core import JudgeLabel, JudgeScore


def test_judge_score_label_enum():
    s = JudgeScore(
        dim_name="humanness",
        unit_id="paragraph:x",
        score_0_100=72.0,
        label=JudgeLabel.MODERATE,
        reasoning="Has a point of view but generic intro.",
        evidence_refs=["phrase:'in today's evolving landscape'"],
        model="claude-sonnet-4-6",
        cost_usd=0.011,
        derived=False,
    )
    assert s.label == JudgeLabel.MODERATE
    assert s.score_0_100 == 72.0


def test_judge_score_error_allows_null_score():
    s = JudgeScore(
        dim_name="humanness",
        unit_id="paragraph:x",
        score_0_100=None,
        label=JudgeLabel.ERROR,
        reasoning="API timeout after 3 retries",
        evidence_refs=[],
        model="claude-sonnet-4-6",
        cost_usd=0.0,
        derived=False,
    )
    assert s.score_0_100 is None
    assert s.label == JudgeLabel.ERROR
