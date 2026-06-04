"""
Phase 1 seed graph: fertilizer → food chain.

Loads the example from docs/augur-graph-schema.md via the Applier so that
the graph is populated with a non-trivial connected subgraph for smoke-testing
and UI development.

Nodes (8 entities, 5 conditions, 1 event):
  Entities:  Russia, Ukraine, Natural Gas Supply, Ammonia Production,
             Nitrogen Fertilizer, Global Crop Yields, Global Food Prices, Food Security
  Conditions: Russian Gas Export Restrictions, Ukraine Grain Export Blockade,
               Fertilizer Supply Shortage, Food Price Inflation, Food Insecurity Crisis
  Event:     2022 Russia-Ukraine War (geopolitical)

Edges (7):
  Russian Gas Export Restrictions  --causes-->    Ammonia Production      (strong)
  Ukraine Grain Export Blockade    --constrains--> Global Crop Yields      (strong)
  Ammonia Production               --produces-->   Nitrogen Fertilizer     (moderate)
  Nitrogen Fertilizer              --causes-->     Global Crop Yields      (moderate)
  Global Crop Yields               --causes-->     Global Food Prices      (strong)
  Global Food Prices               --causes-->     Food Security           (moderate)
  2022 Russia-Ukraine War          --enables-->    Russian Gas Export Restrictions (strong)
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from augur.graph.applier import Applier

log = structlog.get_logger(__name__)

# All seed content is anchored to this timestamp (the 2022 invasion date).
_SEED_TIMESTAMP = datetime(2022, 2, 24, tzinfo=timezone.utc)
_SEED_SOURCE = "seed"


async def load_seed_graph(pool) -> dict[str, int]:  # type: ignore[type-arg]
    """
    Load the fertilizer→food chain seed graph via the Applier.

    Returns a dict with keys "applied" and "rejected".
    Idempotent: re-running will produce alias-rewrite events rather than
    duplicate nodes (entity names are registered in the alias table on first run).
    """
    from augur.graph.models import (
        CreateEdgeOperation,
        CreateNodeOperation,
    )

    applier = Applier(pool)

    # ── Nodes ─────────────────────────────────────────────────────────────────

    node_ops = [
        # Entities
        CreateNodeOperation(
            node_type="entity",
            proposed_id="russia",
            fields={
                "name": "Russia",
                "entity_kind": "state",
                "description": "The Russian Federation, a major natural gas exporter.",
            },
            reasoning="Seed: major actor in fertilizer supply chain via gas exports.",
        ),
        CreateNodeOperation(
            node_type="entity",
            proposed_id="ukraine",
            fields={
                "name": "Ukraine",
                "entity_kind": "state",
                "description": "Ukraine, a major global wheat and grain exporter.",
            },
            reasoning="Seed: major actor in food supply chain via grain exports.",
        ),
        CreateNodeOperation(
            node_type="entity",
            proposed_id="natural_gas_supply",
            fields={
                "name": "Natural Gas Supply",
                "entity_kind": "commodity",
                "description": "Global natural gas supply, feedstock for ammonia/fertilizer production.",
            },
            reasoning="Seed: key input in the fertilizer→food chain.",
        ),
        CreateNodeOperation(
            node_type="entity",
            proposed_id="ammonia_production",
            fields={
                "name": "Ammonia Production",
                "entity_kind": "sector",
                "description": "Global ammonia production, the primary nitrogen fertilizer precursor.",
            },
            reasoning="Seed: intermediate step in fertilizer supply chain.",
        ),
        CreateNodeOperation(
            node_type="entity",
            proposed_id="nitrogen_fertilizer",
            fields={
                "name": "Nitrogen Fertilizer",
                "entity_kind": "commodity",
                "description": "Nitrogen-based fertilizers (urea, ammonium nitrate) derived from ammonia.",
            },
            reasoning="Seed: key agricultural input.",
        ),
        CreateNodeOperation(
            node_type="entity",
            proposed_id="global_crop_yields",
            fields={
                "name": "Global Crop Yields",
                "entity_kind": "sector",
                "description": "Aggregate global crop production levels.",
            },
            reasoning="Seed: output variable connecting fertilizer to food prices.",
        ),
        CreateNodeOperation(
            node_type="entity",
            proposed_id="global_food_prices",
            fields={
                "name": "Global Food Prices",
                "entity_kind": "commodity",
                "description": "World food price index (FAO FFPI benchmark).",
            },
            reasoning="Seed: price signal connecting crop yields to food security.",
        ),
        CreateNodeOperation(
            node_type="entity",
            proposed_id="food_security",
            fields={
                "name": "Food Security",
                "entity_kind": "sector",
                "description": "Global food security as measured by access, availability, and affordability.",
            },
            reasoning="Seed: terminal outcome node in the causal chain.",
        ),

        # Conditions
        CreateNodeOperation(
            node_type="condition",
            proposed_id="russian_gas_restrictions",
            fields={
                "name": "Russian Gas Export Restrictions",
                "current_state": "active",
                "description": "Russia limiting or weaponising natural gas exports to Europe.",
                "subject_entities": [],
            },
            reasoning="Seed: root condition driving fertilizer supply disruption.",
        ),
        CreateNodeOperation(
            node_type="condition",
            proposed_id="ukraine_grain_blockade",
            fields={
                "name": "Ukraine Grain Export Blockade",
                "current_state": "active",
                "description": "Black Sea shipping blockade preventing Ukrainian grain exports.",
                "subject_entities": [],
            },
            reasoning="Seed: direct constraint on global crop supply.",
        ),
        CreateNodeOperation(
            node_type="condition",
            proposed_id="fertilizer_shortage",
            fields={
                "name": "Fertilizer Supply Shortage",
                "current_state": "active",
                "description": "Global shortage of nitrogen fertilizers driven by gas price spikes.",
                "subject_entities": [],
            },
            reasoning="Seed: intermediate condition linking gas to food.",
        ),
        CreateNodeOperation(
            node_type="condition",
            proposed_id="food_price_inflation",
            fields={
                "name": "Food Price Inflation",
                "current_state": "active",
                "description": "Elevated global food price inflation above historical averages.",
                "subject_entities": [],
            },
            reasoning="Seed: consequence of constrained crop supply.",
        ),
        CreateNodeOperation(
            node_type="condition",
            proposed_id="food_insecurity_crisis",
            fields={
                "name": "Food Insecurity Crisis",
                "current_state": "active",
                "description": "Acute food insecurity in import-dependent and low-income countries.",
                "subject_entities": [],
            },
            reasoning="Seed: terminal outcome node measuring humanitarian impact.",
        ),

        # Event
        CreateNodeOperation(
            node_type="event",
            proposed_id="russia_ukraine_war_2022",
            fields={
                "name": "2022 Russia-Ukraine War",
                "event_kind": "geopolitical",
                "occurred_at": "2022-02-24T00:00:00Z",
                "description": "Russia's full-scale invasion of Ukraine, commenced 24 Feb 2022.",
                "subject_entities": [],
            },
            reasoning="Seed: initiating event for the causal chain.",
        ),
    ]

    # ── Edges ─────────────────────────────────────────────────────────────────

    edge_ops = [
        CreateEdgeOperation(
            source_node_id="russia_ukraine_war_2022",
            target_node_id="russian_gas_restrictions",
            edge_type="enables",
            proposed_weight_band="strong",
            reasoning=(
                "The 2022 invasion created the geopolitical context in which Russia "
                "deployed gas exports as a coercive instrument against Europe."
            ),
            falsification_criteria=(
                "Russia resumes full pre-invasion gas supply volumes to Europe "
                "while the war is ongoing."
            ),
        ),
        CreateEdgeOperation(
            source_node_id="russian_gas_restrictions",
            target_node_id="ammonia_production",
            edge_type="causes",
            proposed_weight_band="strong",
            reasoning=(
                "Natural gas is the primary feedstock (Haber-Bosch process) for ammonia. "
                "A 40-80% gas price spike forces European ammonia plants to curtail output."
            ),
            falsification_criteria=(
                "European ammonia production remains at ≥90% of 2021 levels despite "
                "gas price spike above $50/MMBtu."
            ),
        ),
        CreateEdgeOperation(
            source_node_id="ammonia_production",
            target_node_id="nitrogen_fertilizer",
            edge_type="produces",
            proposed_weight_band="moderate",
            reasoning=(
                "Ammonia is the direct precursor to all major nitrogen fertilizers "
                "(urea, ammonium nitrate, UAN). Production shortfalls flow through directly."
            ),
            falsification_criteria=(
                "Nitrogen fertilizer supply remains stable despite a ≥20% drop in "
                "ammonia production, implying inventory buffers absorbed the shock."
            ),
        ),
        CreateEdgeOperation(
            source_node_id="nitrogen_fertilizer",
            target_node_id="global_crop_yields",
            edge_type="causes",
            proposed_weight_band="moderate",
            reasoning=(
                "Nitrogen fertilizer is the single largest yield-determining input. "
                "A 20% reduction in application rates typically reduces cereal yields by 10-15%."
            ),
            falsification_criteria=(
                "Global cereal yields remain within 5% of trend despite a ≥20% "
                "reduction in nitrogen fertilizer application, implying substitution "
                "or weather offset."
            ),
        ),
        CreateEdgeOperation(
            source_node_id="ukraine_grain_blockade",
            target_node_id="global_crop_yields",
            edge_type="constrains",
            proposed_weight_band="strong",
            reasoning=(
                "Ukraine supplies ~10% of global wheat and ~15% of global corn. "
                "Export blockade removes this supply from world markets."
            ),
            falsification_criteria=(
                "Alternative export routes (rail, Danube, other Black Sea ports) "
                "restore ≥80% of normal Ukrainian export volumes within 6 months."
            ),
        ),
        CreateEdgeOperation(
            source_node_id="global_crop_yields",
            target_node_id="global_food_prices",
            edge_type="causes",
            proposed_weight_band="strong",
            reasoning=(
                "Reduced supply relative to demand drives up commodity prices. "
                "FAO FFPI historically correlates strongly (r>0.8) with crop supply shocks."
            ),
            falsification_criteria=(
                "Global food prices remain within 10% of pre-shock baseline "
                "despite a ≥10% supply reduction, implying demand destruction "
                "or reserve drawdown offset."
            ),
        ),
        CreateEdgeOperation(
            source_node_id="global_food_prices",
            target_node_id="food_security",
            edge_type="causes",
            proposed_weight_band="moderate",
            reasoning=(
                "Higher world food prices transmit to domestic markets in food-importing "
                "countries, reducing caloric access for price-sensitive households. "
                "Impact concentrated in low-income import-dependent nations."
            ),
            falsification_criteria=(
                "Food insecurity metrics (FIES, IPC Phase 3+) do not worsen despite "
                "a ≥20% increase in world food prices, implying subsidy or aid programmes "
                "fully absorbed the price signal."
            ),
        ),
    ]

    # Apply nodes first, then edges
    node_result = await applier.apply(
        node_ops,
        content_timestamp=_SEED_TIMESTAMP,
        source=_SEED_SOURCE,
    )
    edge_result = await applier.apply(
        edge_ops,
        content_timestamp=_SEED_TIMESTAMP,
        source=_SEED_SOURCE,
    )

    total_applied = len(node_result.applied) + len(edge_result.applied)
    total_rejected = len(node_result.rejected) + len(edge_result.rejected)

    log.info(
        "seed_graph.loaded",
        nodes_applied=len(node_result.applied),
        nodes_rejected=len(node_result.rejected),
        edges_applied=len(edge_result.applied),
        edges_rejected=len(edge_result.rejected),
    )

    if total_rejected:
        for event in node_result.rejected + edge_result.rejected:
            log.warning(
                "seed_graph.rejected",
                event_type=event.event_type,
                reason=event.rejection_reason,
            )

    return {"applied": total_applied, "rejected": total_rejected}
