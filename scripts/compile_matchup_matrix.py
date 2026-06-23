#!/usr/bin/env python3
import sys
import os
import itertools
import asyncio
import asyncpg
from typing import List, Dict, Any
from monte_carlo_sim import SimulatedPokemon, SimulationDeckProfile, execute_full_matchup_evaluation

# DB Connection URI configuration fallback assignment
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ptcgp_analytics")


async def fetch_archetype_profiles(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
    """
    Retrieves all available recorded meta archetype records from the primary data layer.

    Joins against archetype_tech_cards + cards to pull the real weakness_type
    and is_ex status of each archetype's primary attacker, replacing the
    hard-coded proxy values that caused weakness checks to never fire.
    """
    query = """
        SELECT
            a.archetype_id,
            a.name,
            a.energy_type_1,
            a.energy_type_2,
            COALESCE(a.primary_attacker_is_ex, TRUE) AS primary_attacker_is_ex,
            c.weakness_type,
            c.hp
        FROM archetypes a
        LEFT JOIN LATERAL (
            SELECT ca.weakness_type, ca.hp
            FROM archetype_tech_cards atc
            JOIN cards ca ON ca.card_id = atc.card_id
            WHERE atc.archetype_id = a.archetype_id
              AND atc.is_core = TRUE
              AND ca.supertype = 'Pokemon'
            ORDER BY ca.hp DESC
            LIMIT 1
        ) c ON TRUE;
    """
    return await conn.fetch(query)


async def store_matchup_matrix_results(conn: asyncpg.Connection, results: Dict[str, Any]):
    """
    Performs atomic PostgreSQL UPSERT actions mapping metrics safely into structural tables.

    Fixes applied vs. original:
      - simulated_states now uses ON CONFLICT DO UPDATE so re-runs never
        accumulate duplicate rows. Requires the unique index added in schema.sql:
          CREATE UNIQUE INDEX IF NOT EXISTS uq_simulated_states
            ON simulated_states (archetype_a_id, archetype_b_id, turn_number);
      - Turn-probability values are the real per-turn win rates returned by
        execute_full_matchup_evaluation, not an alternating proxy alias.
    """
    upsert_matchup_query = """
        INSERT INTO deck_matchups (
            archetype_a_id, archetype_b_id,
            games_played_a_first, win_rate_a_first,
            games_played_a_second, win_rate_a_second
        ) VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (archetype_a_id, archetype_b_id) DO UPDATE SET
            games_played_a_first  = EXCLUDED.games_played_a_first,
            win_rate_a_first      = EXCLUDED.win_rate_a_first,
            games_played_a_second = EXCLUDED.games_played_a_second,
            win_rate_a_second     = EXCLUDED.win_rate_a_second;
    """

    # Uses ON CONFLICT to safely upsert. Requires unique index on
    # (archetype_a_id, archetype_b_id, turn_number) — see schema.sql migration note.
    upsert_states_query = """
        INSERT INTO simulated_states (
            archetype_a_id, archetype_b_id, turn_number, state_description, win_probability_a
        ) VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (archetype_a_id, archetype_b_id, turn_number) DO UPDATE SET
            win_probability_a = EXCLUDED.win_probability_a,
            state_description = EXCLUDED.state_description,
            last_updated      = CURRENT_TIMESTAMP;
    """

    # Store aggregated matchup matrix node.
    await conn.execute(
        upsert_matchup_query,
        results['a_id'], results['b_id'],
        10000, results['win_rate_going_first'],
        10000, results['win_rate_going_second'],
    )

    # Store the real turn-probability curve (going first).
    # Each row is P(A wins | game ended at exactly turn N).
    for turn, prob in results['turn_win_probs_first'].items():
        await conn.execute(
            upsert_states_query,
            results['a_id'], results['b_id'], turn,
            f"P(A wins | game ended at turn {turn}, A went first).",
            round(prob * 100, 2),
        )


async def main_pipeline_executor():
    """
    Core sequence routing control logic for Task 2.1.
    """
    print("=== Launching Task 2.1: Automated Matchup Matrix Compilation Pipeline ===")

    try:
        conn = await asyncpg.connect(DATABASE_URL)
    except Exception as e:
        print(f"PostgreSQL Connection Failure: {e}. Exiting execution context gracefully.")
        return

    try:
        records = await fetch_archetype_profiles(conn)
        if not records:
            print("Archetypes empty. Run 'seed_archetypes.py' before executing this step.")
            return

        print(f"Successfully loaded {len(records)} active archetype profiles for matrix cross-evaluation.")

        for arch_a, arch_b in itertools.combinations(records, 2):
            print(f"Simulating: {arch_a['name']} VS {arch_b['name']}...")

            # Build proxy SimulatedPokemon using real DB values where available.
            # weakness_type falls back to "Colorless" only if the JOIN returned NULL,
            # which should not happen once seed_cards.py has run.
            weakness_a = arch_a['weakness_type'] or "Colorless"
            weakness_b = arch_b['weakness_type'] or "Colorless"
            hp_a = arch_a['hp'] or 130
            hp_b = arch_b['hp'] or 140

            pokemon_a = SimulatedPokemon(
                card_id="PROXY-A",
                hp=hp_a,
                energy_type=arch_a['energy_type_1'],
                weakness=weakness_a,
                base_damage=80,
                energy_cost=2,
                is_ex=arch_a['primary_attacker_is_ex'],
            )
            pokemon_b = SimulatedPokemon(
                card_id="PROXY-B",
                hp=hp_b,
                energy_type=arch_b['energy_type_1'],
                weakness=weakness_b,
                base_damage=90,
                energy_cost=2,
                is_ex=arch_b['primary_attacker_is_ex'],
            )

            profile_a = SimulationDeckProfile(
                name=arch_a['name'],
                energy_zone_type=arch_a['energy_type_1'],
                main_attacker=pokemon_a,
            )
            profile_b = SimulationDeckProfile(
                name=arch_b['name'],
                energy_zone_type=arch_b['energy_type_1'],
                main_attacker=pokemon_b,
            )

            # Execute Simulation Engine.
            raw_data = execute_full_matchup_evaluation(profile_a, profile_b, iterations=10000)

            # Append reference entity keys.
            raw_data['a_id'] = arch_a['archetype_id']
            raw_data['b_id'] = arch_b['archetype_id']

            # Write to PostgreSQL DB.
            await store_matchup_matrix_results(conn, raw_data)

        print("\nAll target archetype combinations evaluated and written to the database successfully.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main_pipeline_executor())