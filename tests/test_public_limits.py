from __future__ import annotations

import pytest

from disclosure_db.public_limits import PublicLimitSettings, PublicRequestLimiter


def test_rate_limit_reopens_after_rolling_window() -> None:
    limiter = PublicRequestLimiter(
        PublicLimitSettings(per_minute=2, per_ip_concurrency=4, global_concurrency=8)
    )
    first = limiter.try_acquire("198.51.100.1", now=0.0)
    limiter.release("198.51.100.1")
    second = limiter.try_acquire("198.51.100.1", now=1.0)
    limiter.release("198.51.100.1")
    denied = limiter.try_acquire("198.51.100.1", now=2.0)

    assert first.allowed and second.allowed
    assert not denied.allowed
    assert denied.status_code == 429
    assert denied.detail == "rate_limited"
    assert denied.retry_after == 58
    assert limiter.try_acquire("198.51.100.1", now=61.0).allowed


def test_per_ip_and_global_concurrency_release_cleanly() -> None:
    limiter = PublicRequestLimiter(
        PublicLimitSettings(per_minute=120, per_ip_concurrency=1, global_concurrency=2)
    )

    assert limiter.try_acquire("198.51.100.1", now=0.0).allowed
    assert limiter.try_acquire("198.51.100.1", now=0.1).detail == "ip_busy"
    assert limiter.try_acquire("198.51.100.2", now=0.1).allowed
    assert limiter.try_acquire("198.51.100.3", now=0.1).detail == "server_busy"
    limiter.release("198.51.100.1")
    assert limiter.try_acquire("198.51.100.3", now=0.2).allowed


def test_limit_settings_parse_exact_environment_and_reject_disable_values() -> None:
    settings = PublicLimitSettings.from_env(
        {
            "DISCLOSURE_PUBLIC_RATE_PER_MINUTE": "30",
            "DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY": "2",
            "DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY": "5",
        }
    )

    assert settings == PublicLimitSettings(30, 2, 5)
    with pytest.raises(ValueError):
        PublicLimitSettings.from_env({"DISCLOSURE_PUBLIC_RATE_PER_MINUTE": "0"})
    with pytest.raises(ValueError):
        PublicLimitSettings.from_env({"DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY": "10001"})
    with pytest.raises(ValueError):
        PublicLimitSettings.from_env({"DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY": "not-an-int"})


def test_expired_idle_ip_state_is_removed_on_next_acquire() -> None:
    limiter = PublicRequestLimiter(PublicLimitSettings())
    assert limiter.try_acquire("198.51.100.1", now=0.0).allowed
    limiter.release("198.51.100.1")

    assert limiter.try_acquire("198.51.100.2", now=61.0).allowed
    assert limiter.tracked_ip_count == 1


def test_extra_release_does_not_reduce_global_active_count() -> None:
    limiter = PublicRequestLimiter(
        PublicLimitSettings(per_minute=120, per_ip_concurrency=2, global_concurrency=1)
    )
    limiter.release("198.51.100.1")
    assert limiter.try_acquire("198.51.100.1", now=0.0).allowed
    limiter.release("198.51.100.1")
    limiter.release("198.51.100.1")
    assert limiter.try_acquire("198.51.100.2", now=0.1).allowed
