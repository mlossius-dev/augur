"""
Hand-curated alias seed data.

Each entry is (alias_text, canonical_name).  These are loaded before any
graph nodes exist; canonical_node_id is NULL at seed time and gets filled in
by the Applier when the matching Entity node is first created.

Coverage: major states, major currencies, major central banks, major
commodities, and key organisations relevant to the Phase 1 seed graph.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

# (alias_text, canonical_name)
ALIAS_SEED_DATA: list[tuple[str, str]] = [
    # ── States ────────────────────────────────────────────────────────────────
    ("United States", "United States"),
    ("USA", "United States"),
    ("US", "United States"),
    ("America", "United States"),
    ("United States of America", "United States"),
    ("China", "China"),
    ("PRC", "China"),
    ("People's Republic of China", "China"),
    ("Russia", "Russia"),
    ("Russian Federation", "Russia"),
    ("Germany", "Germany"),
    ("Federal Republic of Germany", "Germany"),
    ("France", "France"),
    ("French Republic", "France"),
    ("United Kingdom", "United Kingdom"),
    ("UK", "United Kingdom"),
    ("Britain", "United Kingdom"),
    ("Great Britain", "United Kingdom"),
    ("Japan", "Japan"),
    ("India", "India"),
    ("Republic of India", "India"),
    ("Brazil", "Brazil"),
    ("Federative Republic of Brazil", "Brazil"),
    ("Canada", "Canada"),
    ("Australia", "Australia"),
    ("South Korea", "South Korea"),
    ("Republic of Korea", "South Korea"),
    ("Korea", "South Korea"),
    ("Iran", "Iran"),
    ("Islamic Republic of Iran", "Iran"),
    ("Persia", "Iran"),
    ("Israel", "Israel"),
    ("State of Israel", "Israel"),
    ("Saudi Arabia", "Saudi Arabia"),
    ("Kingdom of Saudi Arabia", "Saudi Arabia"),
    ("KSA", "Saudi Arabia"),
    ("Ukraine", "Ukraine"),
    ("Turkey", "Turkey"),
    ("Republic of Turkey", "Turkey"),
    ("Türkiye", "Turkey"),
    ("Indonesia", "Indonesia"),
    ("Republic of Indonesia", "Indonesia"),
    ("Pakistan", "Pakistan"),
    ("Islamic Republic of Pakistan", "Pakistan"),
    ("Nigeria", "Nigeria"),
    ("Federal Republic of Nigeria", "Nigeria"),
    ("Mexico", "Mexico"),
    ("United Mexican States", "Mexico"),
    ("European Union", "European Union"),
    ("EU", "European Union"),

    # ── Central banks ─────────────────────────────────────────────────────────
    ("Federal Reserve", "Federal Reserve"),
    ("Fed", "Federal Reserve"),
    ("US Federal Reserve", "Federal Reserve"),
    ("Federal Reserve System", "Federal Reserve"),
    ("FOMC", "Federal Reserve"),
    ("European Central Bank", "European Central Bank"),
    ("ECB", "European Central Bank"),
    ("Bank of England", "Bank of England"),
    ("BoE", "Bank of England"),
    ("Bank of Japan", "Bank of Japan"),
    ("BoJ", "Bank of Japan"),
    ("People's Bank of China", "People's Bank of China"),
    ("PBOC", "People's Bank of China"),
    ("Reserve Bank of India", "Reserve Bank of India"),
    ("RBI", "Reserve Bank of India"),
    ("Bank of Canada", "Bank of Canada"),
    ("BoC", "Bank of Canada"),
    ("Reserve Bank of Australia", "Reserve Bank of Australia"),
    ("RBA", "Reserve Bank of Australia"),
    ("Swiss National Bank", "Swiss National Bank"),
    ("SNB", "Swiss National Bank"),

    # ── Currencies ────────────────────────────────────────────────────────────
    ("US Dollar", "US Dollar"),
    ("USD", "US Dollar"),
    ("Dollar", "US Dollar"),
    ("Euro", "Euro"),
    ("EUR", "Euro"),
    ("British Pound", "British Pound"),
    ("GBP", "British Pound"),
    ("Sterling", "British Pound"),
    ("Pound Sterling", "British Pound"),
    ("Japanese Yen", "Japanese Yen"),
    ("JPY", "Japanese Yen"),
    ("Yen", "Japanese Yen"),
    ("Chinese Yuan", "Chinese Yuan"),
    ("CNY", "Chinese Yuan"),
    ("Renminbi", "Chinese Yuan"),
    ("RMB", "Chinese Yuan"),
    ("Swiss Franc", "Swiss Franc"),
    ("CHF", "Swiss Franc"),
    ("Canadian Dollar", "Canadian Dollar"),
    ("CAD", "Canadian Dollar"),
    ("Australian Dollar", "Australian Dollar"),
    ("AUD", "Australian Dollar"),

    # ── Commodities ───────────────────────────────────────────────────────────
    ("Crude Oil", "Crude Oil"),
    ("Oil", "Crude Oil"),
    ("Brent Crude", "Crude Oil"),
    ("WTI", "Crude Oil"),
    ("Natural Gas", "Natural Gas"),
    ("LNG", "Natural Gas"),
    ("Wheat", "Wheat"),
    ("Hard Wheat", "Wheat"),
    ("Soft Wheat", "Wheat"),
    ("Corn", "Corn"),
    ("Maize", "Corn"),
    ("Soybeans", "Soybeans"),
    ("Soy", "Soybeans"),
    ("Soybean", "Soybeans"),
    ("Rice", "Rice"),
    ("Gold", "Gold"),
    ("XAU", "Gold"),
    ("Silver", "Silver"),
    ("XAG", "Silver"),
    ("Copper", "Copper"),
    ("Iron Ore", "Iron Ore"),
    ("Coal", "Coal"),
    ("Thermal Coal", "Coal"),

    # ── Key organisations (Phase 1 seed graph) ────────────────────────────────
    ("OPEC", "OPEC"),
    ("Organization of the Petroleum Exporting Countries", "OPEC"),
    ("OPEC+", "OPEC+"),
    ("World Trade Organization", "WTO"),
    ("WTO", "WTO"),
    ("International Monetary Fund", "IMF"),
    ("IMF", "IMF"),
    ("World Bank", "World Bank"),
    ("United Nations", "United Nations"),
    ("UN", "United Nations"),
    ("NATO", "NATO"),
    ("North Atlantic Treaty Organization", "NATO"),

    # ── Fertilizer / food chain entities (Phase 1 seed graph) ────────────────
    ("Natural Gas Supply", "Natural Gas Supply"),
    ("Ammonia Production", "Ammonia Production"),
    ("Nitrogen Fertilizer", "Nitrogen Fertilizer"),
    ("Fertilizer Supply", "Fertilizer Supply"),
    ("Global Crop Yields", "Global Crop Yields"),
    ("Global Food Prices", "Global Food Prices"),
    ("Food Security", "Food Security"),
]


async def load_aliases(pool) -> int:  # type: ignore[type-arg]
    """
    Insert all seed aliases into the aliases table.

    Skips entries that already exist (ON CONFLICT DO NOTHING on alias_text).
    Returns the number of rows inserted.
    """
    from augur.graph.alias_resolver import AliasResolver

    resolver = AliasResolver(pool)
    inserted = 0

    async with pool.acquire() as conn:
        for alias_text, canonical_name in ALIAS_SEED_DATA:
            existing = await conn.fetchval(
                "SELECT COUNT(*) FROM aliases WHERE lower(alias_text) = lower($1)",
                alias_text,
            )
            if existing:
                continue
            await resolver.register(
                alias_text=alias_text,
                canonical_name=canonical_name,
                canonical_node_id=None,
                added_by="seed",
                conn=conn,
            )
            inserted += 1

    log.info("aliases.seed_loaded", inserted=inserted, total=len(ALIAS_SEED_DATA))
    return inserted
