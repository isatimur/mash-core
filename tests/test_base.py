from mash_core import JudgeDim, JudgeInput, JudgeLabel, JudgeScore


class FakeJudge(JudgeDim):
    name = "fake"
    unit_type = "paragraph"
    model_id = "test-model"

    async def judge(self, input: JudgeInput) -> JudgeScore:
        return JudgeScore(
            dim_name=self.name,
            unit_id=input.unit_id,
            score_0_100=75.0,
            label=JudgeLabel.MODERATE,
            reasoning="fake reasoning",
            evidence_refs=[],
            model=self.model_id,
            cost_usd=0.0,
            derived=False,
        )


async def test_judge_returns_score():
    judge = FakeJudge()
    input = JudgeInput(
        unit_id="paragraph:x",
        unit_type="paragraph",
        unit_text="hello",
        dim_name="fake",
    )
    score = await judge.judge(input)
    assert score.score_0_100 == 75.0
    assert score.label == JudgeLabel.MODERATE
