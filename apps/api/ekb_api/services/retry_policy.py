"""Transport retry policy (PH3-5).

Classifies Provider errors into retryable / non-retryable categories and
computes exponential backoff with jitter (spec 02 §6.1).

Error classification:
* Retryable: HTTP 408, 429, network reset, timeout, selected 5xx
* Non-retryable: auth/authorization, invalid request, attachment ACL,
  model disabled, schema/persistence errors
* Context overflow: not retried as network error; triggers compaction once

Backoff:
* Default delays: 2, 4, 8, 16 seconds, capped at 30 seconds
* 429 has a separately configurable maximum
* ``Retry-After`` header is honored within a bounded cap
* Exponential backoff with jitter
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

# Error codes that must NOT be retried (spec 02 §6.1)
NON_RETRYABLE_ERROR_CODES = frozenset(
    {
        "AUTHENTICATION_FAILED",
        "AUTHORIZATION_DENIED",
        "TENANT_FORBIDDEN",
        "ACL_REVOKED",
        "ATTACHMENT_POLICY_VIOLATION",
        "EGRESS_POLICY_BLOCKED",
        "INVALID_REQUEST",
        "MODEL_DISABLED",
        "MODEL_NOT_FOUND",
        "SCHEMA_MISMATCH",
        "MIGRATION_REQUIRED",
        "PERSISTENCE_FAILED",
        "CONTEXT_BUDGET_EXCEEDED",
    }
)

# HTTP status codes that are retryable
RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

# Default backoff schedule (seconds)
DEFAULT_BACKOFF_SCHEDULE = (2, 4, 8, 16)
DEFAULT_BACKOFF_CAP = 30
DEFAULT_429_MAX_RETRIES = 3
DEFAULT_429_BACKOFF_CAP = 60


@dataclass(frozen=True)
class RetryDecision:
    """Result of error classification."""

    retryable: bool
    error_code: str
    error_category: str
    retry_after_seconds: Optional[float] = None
    attempt: int = 0
    max_attempts: int = 0
    next_retry_at: Optional[float] = None  # seconds from now
    reason: str = ""


class RetryPolicy:
    """Classifies errors and computes backoff (spec 02 §6.1)."""

    def __init__(
        self,
        *,
        backoff_schedule: tuple[int, ...] = DEFAULT_BACKOFF_SCHEDULE,
        backoff_cap: int = DEFAULT_BACKOFF_CAP,
        max_429_retries: int = DEFAULT_429_MAX_RETRIES,
        backoff_429_cap: int = DEFAULT_429_BACKOFF_CAP,
    ) -> None:
        self.backoff_schedule = backoff_schedule
        self.backoff_cap = backoff_cap
        self.max_429_retries = max_429_retries
        self.backoff_429_cap = backoff_429_cap

    def classify(
        self,
        *,
        error_code: str = "",
        http_status: Optional[int] = None,
        error_message: str = "",
        attempt: int = 1,
        retry_after_header: Optional[float] = None,
    ) -> RetryDecision:
        """Classify an error and decide whether to retry.

        Args:
            error_code: Application-level error code (e.g. "AUTHENTICATION_FAILED")
            http_status: HTTP status code from the Provider response
            error_message: Human-readable error message
            attempt: Current attempt number (1-based)
            retry_after_header: Value of Retry-After header (seconds), if present

        Returns:
            RetryDecision with retryable flag and backoff timing
        """
        # 1. Check non-retryable error codes first
        if error_code in NON_RETRYABLE_ERROR_CODES:
            return RetryDecision(
                retryable=False,
                error_code=error_code,
                error_category="non_retryable",
                attempt=attempt,
                reason=f"Error code {error_code} is non-retryable",
            )

        # 2. Context overflow is not a network error — triggers compaction, not retry
        if error_code == "CONTEXT_OVERFLOW" or "context" in error_message.lower():
            return RetryDecision(
                retryable=False,
                error_code=error_code or "CONTEXT_OVERFLOW",
                error_category="context_overflow",
                attempt=attempt,
                reason="Context overflow triggers compaction, not retry",
            )

        # 3. Check HTTP status for retryable codes
        is_429 = http_status == 429
        is_retryable_status = http_status in RETRYABLE_HTTP_STATUSES

        # Network-level errors (no HTTP status) are retryable
        is_network_error = http_status is None and (
            "timeout" in error_message.lower()
            or "connection" in error_message.lower()
            or "reset" in error_message.lower()
            or "network" in error_message.lower()
        )

        if not (is_retryable_status or is_network_error):
            return RetryDecision(
                retryable=False,
                error_code=error_code or "PROVIDER_ERROR",
                error_category="non_retryable",
                attempt=attempt,
                reason=f"HTTP {http_status} with code {error_code} is non-retryable",
            )

        # 4. Determine max attempts based on error type
        if is_429:
            max_attempts = self.max_429_retries + 1
            cap = self.backoff_429_cap
        else:
            max_attempts = len(self.backoff_schedule) + 1
            cap = self.backoff_cap

        if attempt >= max_attempts:
            return RetryDecision(
                retryable=False,
                error_code=error_code or ("RATE_LIMITED" if is_429 else "PROVIDER_ERROR"),
                error_category="max_retries_exceeded",
                attempt=attempt,
                max_attempts=max_attempts,
                reason=f"Max attempts ({max_attempts}) reached",
            )

        # 5. Compute backoff with jitter
        if retry_after_header is not None:
            # Honor Retry-After header, capped
            delay = min(retry_after_header, cap)
        else:
            # Exponential backoff from schedule
            schedule_index = min(attempt - 1, len(self.backoff_schedule) - 1)
            base_delay = self.backoff_schedule[schedule_index]
            # Add jitter: 50%-100% of base delay
            jitter = random.uniform(0.5, 1.0)
            delay = min(base_delay * jitter, cap)

        error_category = "rate_limited" if is_429 else "transient"

        return RetryDecision(
            retryable=True,
            error_code=error_code or ("RATE_LIMITED" if is_429 else "TRANSIENT_ERROR"),
            error_category=error_category,
            attempt=attempt,
            max_attempts=max_attempts,
            next_retry_at=delay,
            reason=f"Retryable {error_category} error, retry in {delay:.1f}s",
        )

    def should_create_new_attempt(
        self, decision: RetryDecision, current_attempt: int
    ) -> bool:
        """Check if a new turn_attempts row should be created."""
        return decision.retryable and current_attempt < decision.max_attempts
