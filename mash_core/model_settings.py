"""Shared model settings for every judge Agent.

Two reliability properties live here so every judge dimension gets them
identically — there is one definition, not N copies that can drift apart.

1. ``temperature=0.0`` — makes cold re-runs near-reproducible. (No provider
   guarantees *bit*-exact determinism even at 0, but it removes the dominant
   sampling-variance source.)

2. ``timeout`` — a per-request httpx timeout, so one stalled connection can't
   block an entire run. A timed-out request raises an APITimeout-class error,
   which ``run_with_backoff`` treats as retryable (bounded ``max_attempts``);
   if it keeps timing out the exception re-raises and the caller's existing
   handler records a clean ERROR-labeled score instead of hanging forever.

pydantic-ai 0.0.40: ``ModelSettings`` is a TypedDict; ``AnthropicModel`` reads
``temperature`` and ``timeout`` from it and passes them straight into the
Anthropic SDK ``messages.create`` call. Pass it to ``Agent(model_settings=...)``.
"""

from pydantic_ai.settings import ModelSettings

# Per-request HTTP timeout in seconds. Bounds a single stalled call; combined
# with run_with_backoff's bounded retries this caps total time per unit instead
# of allowing an unbounded hang.
JUDGE_REQUEST_TIMEOUT_S = 120.0

# Single source of truth for every judge Agent's sampling + timeout behavior.
JUDGE_MODEL_SETTINGS = ModelSettings(
    temperature=0.0,
    timeout=JUDGE_REQUEST_TIMEOUT_S,
    # Judges return a short structured verdict (~300 tokens). Without an explicit
    # cap, OpenAI-compatible gateways (OpenRouter) pre-authorize the model's full
    # output window (16k+) per request against remaining credits, which 402s
    # whole runs at high concurrency long before any real spend.
    max_tokens=2048,
)
