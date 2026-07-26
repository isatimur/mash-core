# mash-core

Provider-agnostic LLM-judge core: the `JudgeDim`/`JudgeScore` contract, cost-aware
model routing (Anthropic / OpenRouter / OpenAI-compatible via pydantic-ai), a
per-model pricing table, and retry-with-backoff tuned for judge calls.

Extracted from [`book-mash`](https://github.com/isatimur/book-mash)'s
`book_mash/judges/{base,models,_model_factory,_model_settings,_pricing,_retry}.py`,
where this cluster was already generic and decoupled from book-mash's own
manuscript-specific rollup/broadcast logic. book-mash depends on this package;
this package has no book-mash dependency in the other direction.

## Install

```bash
poetry install
```

Path-dependency install from a sibling consumer project (e.g. book-mash):

```toml
[tool.poetry.dependencies]
mash-core = {path = "../mash-core", develop = true}
```

## What's here

- `mash_core.models` — `JudgeLabel`, `UnitType`, `JudgeScore`, `JudgeInput`, `JudgeResult`.
- `mash_core.base` — the `JudgeDim` ABC every judge dimension implements.
- `mash_core.model_factory` — `build_judge_model()`, provider-configurable via
  `BOOK_MASH_JUDGE_PROVIDER` / `BOOK_MASH_JUDGE_MODEL` / `BOOK_MASH_JUDGE_BASE_URL` /
  `BOOK_MASH_JUDGE_API_KEY_ENV` env vars.
- `mash_core.model_settings` — `JUDGE_MODEL_SETTINGS` (temperature=0, timeout, max_tokens cap).
- `mash_core.pricing` — `estimate_cost(model_id, input_tokens, output_tokens)`.
- `mash_core.retry` — `run_with_backoff(factory, ...)`.

License: MIT.
