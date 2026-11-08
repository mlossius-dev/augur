"""
FRED through its public CSV download, and the stress radar
(docs/augur-sources.md, FRED; docs/augur-signal-pipeline.md, "The radar").

The radar fixture is FRED's real US high-yield spread series, 2023-11-06 to
2026-11-05: the widening an operator spotted by hand that Augur missed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from augur.ingestion.api_clients.fred import (
    FredClient,
    assess_radar,
    parse_fred_csv,
    radar_statement,
)
from augur.ingestion.models import SourceConfig

HY_CSV = (Path(__file__).parent / "fixtures" / "fred_BAMLH0A0HYM2_20231106_20261105.csv").read_text(encoding="utf-8")


def _series(values: list[float], start_day: int = 0) -> list[dict[str, str]]:
    """Daily observations, newest first, from oldest-first values."""
    from datetime import date, timedelta

    d0 = date(2024, 1, 1) + timedelta(days=start_day)
    obs = [{"date": (d0 + timedelta(days=i)).isoformat(), "value": str(v)} for i, v in enumerate(values)]
    return list(reversed(obs))


def test_csv_parses_to_newest_first_observations() -> None:
    obs = parse_fred_csv("observation_date,X\n2026-11-04,3.32\n2026-11-05,3.48\n")
    assert obs == [{"date": "2026-11-05", "value": "3.48"}, {"date": "2026-11-04", "value": "3.32"}]


def test_the_high_yield_widening_is_stretched() -> None:
    r = assess_radar(parse_fred_csv(HY_CSV), direction=1)
    assert r is not None and r.flag == "stretched"
    assert r.latest == pytest.approx(3.48)
    assert r.latest_date == "2026-11-05"
    assert r.change > 0.7, "about 80 bp wider over 60 days"
    assert r.z >= 1.5


def test_the_statement_reads_in_basis_points_for_spreads() -> None:
    r = assess_radar(parse_fred_csv(HY_CSV), direction=1)
    text = radar_statement("US high-yield spread", "percent", r)
    assert text.startswith("Building risk: US high-yield spread at 3.48 %, +")
    assert " bp over 60 days" in text and "percentile of three years" in text


def test_a_quiet_series_is_not_flagged() -> None:
    wiggle = [100 + (i % 7) * 0.1 for i in range(800)]
    r = assess_radar(_series(wiggle))
    assert r is not None and r.flag is None


def test_direction_minus_one_flags_a_fall() -> None:
    # A curve that flattens sharply at the end: risky when falling
    values = [1.0 + (i % 5) * 0.01 for i in range(760)] + [1.0 - 0.02 * i for i in range(1, 41)]
    assert assess_radar(_series(values), direction=-1).flag == "stretched"
    assert assess_radar(_series(values), direction=1).flag is None


def test_too_short_a_history_is_not_judged() -> None:
    assert assess_radar(_series([1.0] * 20)) is None


def test_missing_values_are_skipped() -> None:
    obs = parse_fred_csv(HY_CSV)
    obs.insert(3, {"date": "2026-10-30", "value": "."})
    assert assess_radar(obs, direction=1).flag == "stretched"


def _source(radar: bool) -> SourceConfig:
    config = ({"radar": True, "series": {"BAMLH0A0HYM2": {"label": "US high-yield spread", "unit": "percent",
                                                         "direction": 1}}}
              if radar else {"series_ids": ["BAMLH0A0HYM2"]})
    return SourceConfig(source_id="fred_radar" if radar else "fred", canonical_name="FRED",
                        url_base="https://fred.stlouisfed.org", tier="structured_data", perspective="us_eu",
                        languages=["en"], access_method="api", access_config=config, update_cadence="daily",
                        domains=["financial"], starting_source_weight=0.95)


def _patched_client(text: str):
    response = MagicMock(text=text, raise_for_status=MagicMock())
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return patch("httpx.AsyncClient", return_value=client), client


@pytest.mark.asyncio
async def test_the_radar_emits_one_payload_for_a_flagged_series() -> None:
    patcher, client = _patched_client(HY_CSV)
    with patcher:
        results = await FredClient().fetch_source(_source(radar=True))
    [r] = results
    assert r.metadata["kind"] == "radar" and r.metadata["flag"] == "stretched"
    assert r.metadata["lens"] == "financial" and r.metadata["dimension"] == "economic_stability"
    assert r.metadata["source_native_id"] == "fred_radar:BAMLH0A0HYM2:2026-11-05:stretched"
    assert r.raw_content.startswith("Building risk: US high-yield spread")
    assert client.get.call_args.kwargs["params"]["id"] == "BAMLH0A0HYM2", "the public CSV, no key"


@pytest.mark.asyncio
async def test_the_radar_is_silent_on_a_quiet_series() -> None:
    quiet = "observation_date,X\n" + "\n".join(
        f"{o['date']},{o['value']}" for o in reversed(_series([100 + (i % 7) * 0.1 for i in range(800)])))
    patcher, _ = _patched_client(quiet)
    with patcher:
        assert await FredClient().fetch_source(_source(radar=True)) == []


@pytest.mark.asyncio
async def test_market_mode_reads_the_csv_too() -> None:
    patcher, _ = _patched_client(HY_CSV)
    with patcher:
        [r] = await FredClient().fetch_source(_source(radar=False))
    assert r.raw_content.startswith("Market move:") and r.metadata["latest_value"] == pytest.approx(3.48)


def test_percentiles_read_as_english_ordinals() -> None:
    from augur.ingestion.api_clients.fred import _ordinal

    assert [_ordinal(n) for n in (1, 2, 3, 11, 12, 13, 21, 63, 90, 99, 100)] == [
        "1st", "2nd", "3rd", "11th", "12th", "13th", "21st", "63rd", "90th", "99th", "100th"]
