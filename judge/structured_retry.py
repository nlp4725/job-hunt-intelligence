"""Retry wrapper for structured-output LLM calls.

DeepSeek intermittently returns a tool call that fails Pydantic validation —
a required field simply absent, or a Literal given a near-miss value. Observed
in production four times across three distinct fields:

  2026-08-20  ExpertiseMatch.score       Field required [type=missing]
  2026-08-20  ExpertiseMatch.confidence  "median" instead of "medium" (literal_error)
  2026-08-27  ExpertiseMatch.score       Field required (Thomson Reuters)
  2026-08-27  ExpertiseMatch.score       Field required (Arango)

Every occurrence recovered on a manual retry of the identical prompt, so this
is model nondeterminism rather than a bad prompt or a bad posting. Without a
retry the job is left unscored and the LLM call is wasted; the user has to
notice the error in the extension panel and click Retry by hand.

`invoke_with_retry` retries the same call on ValidationError only. Other
exceptions (network, auth, rate limit) propagate untouched — those have
different handling and should not be silently re-attempted here.
"""

import logging
import time

from pydantic import ValidationError

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.0


def invoke_with_retry(structured_llm, messages, *, label: str = "structured call"):
    """Invoke a with_structured_output() runnable, retrying on ValidationError.

    Retries the identical prompt — the failures this guards against are
    nondeterministic, so re-sending unchanged is exactly the right move and
    keeps the call deterministic from the caller's point of view.
    """
    last_error: ValidationError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return structured_llm.invoke(messages)
        except ValidationError as exc:
            last_error = exc
            if attempt == MAX_ATTEMPTS:
                break
            logger.warning(
                "%s failed schema validation on attempt %d/%d, retrying: %s",
                label, attempt, MAX_ATTEMPTS, exc,
            )
            time.sleep(BACKOFF_SECONDS * attempt)

    logger.error("%s failed schema validation %d times, giving up", label, MAX_ATTEMPTS)
    raise last_error
