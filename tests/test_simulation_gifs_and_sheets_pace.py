"""Tests for paced Sheets writes (GIF catalog removed — Pokémon images only)."""

import time

import pytest

from services.sheets_write_batch import SheetsWriteRateLimiter


def test_rate_limiter_min_interval_matches_quota():
    limiter = SheetsWriteRateLimiter(55)
    assert abs(limiter._min_interval - 60.0 / 55) < 1e-6


@pytest.mark.asyncio
async def test_rate_limiter_paces_consecutive_acquires():
    limiter = SheetsWriteRateLimiter(120)  # 0.5s between writes
    t0 = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.45


@pytest.mark.asyncio
async def test_rate_limiter_serializes_concurrent_acquires():
    import asyncio

    limiter = SheetsWriteRateLimiter(60)  # 1s between writes
    stamps: list[float] = []

    async def grab() -> None:
        await limiter.acquire()
        stamps.append(time.monotonic())

    await asyncio.gather(grab(), grab(), grab())
    stamps.sort()
    gaps = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]
    assert all(gap >= 0.9 for gap in gaps)
