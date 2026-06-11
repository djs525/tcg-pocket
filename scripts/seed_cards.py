"""
Task 1.1: TCGdex SDK Base Seeding
Asynchronously fetches all cards from the 'tcgp' (Pokémon TCG Pocket) series
and upserts them into the `cards` table.
"""

import asyncio
import os
import logging

import asyncpg
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from tcgdexsdk import TCGdex

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]

# Concurrency limit to avoid hammering the TCGdex API
MAX_CONCURRENT_REQUESTS = 8

async def fetch_series_sets(tcgdex: TCGdex) -> list[str]:
    """Return the set IDs belonging to the 'tcgp' series."""
    series = await tcgdex.serie.get("tcgp")
    return [s.id for s in series.sets]

async def fetch_set_card_ids(tcgdex: TCGdex, set_id: str) -> list[str]:
    """Return all card IDs within a given set."""
    set_data = await tcgdex.set.get(set_id)
    return [c.id for c in set_data.cards]

async def fetch_card_detail(tcgdex: TCGdex, card_id: str, sem: asyncio.Semaphore):
    """Fetch full card detail, respecting the concurrency semaphore."""
    async with sem:
        try:
            return await tcgdex.card.get(card_id)
        except Exception as e:
            log.warning(f"Failed to fetch card {card_id}: {e}")
            return None

def map_to_card_row(card) -> tuple | None:
    """
    Map a TCGdex card object to a row tuple matching the `cards` table.
    Returns None if the card can't be mapped (defensive skip).
    """
    if not card:
        return None

    # supertype: TCGdex categorizes cards via `card.category`
    # Expected values: 'Pokemon', 'Trainer'
    supertype = getattr(card, "category", None) or "Unknown"

    # Pokémon-specific fields are absent on Trainer cards.
    hp = getattr(card, "hp", None)

    types = getattr(card, "types", None) or []
    pokemon_type = types[0] if types else None

    weaknesses = getattr(card, "weaknesses", None) or []
    weakness_type = weaknesses[0].type if weaknesses else None

    retreat = getattr(card, "retreat", None)  # often an int already
    retreat_cost = retreat if isinstance(retreat, int) else None

    image_url = getattr(card, "image", None)
    # TCGdex images often come without extension/quality suffix
    if image_url:
        image_url = f"{image_url}/high.webp"

    # Subtype for Trainer cards: 'Item', 'Supporter', 'Tool', etc.
    # None for Pokémon cards.
    trainer_subtype = getattr(card, "trainerType", None)

    # Trainer cards use `effect`; Pokémon cards use `description`.
    effect_text = getattr(card, "effect", None) or getattr(card, "description", None)

    return (
        card.id,            # card_id
        card.name,          # name
        supertype,          # supertype
        hp,                 # hp
        pokemon_type,       # pokemon_type
        weakness_type,      # weakness_type
        retreat_cost,       # retreat_cost
        image_url,          # image_url
        trainer_subtype,    # trainer_subtype
        effect_text,        # effect_text
    )

async def upsert_cards(pool: asyncpg.Pool, rows: list[tuple]):
    """Bulk upsert card rows into the `cards` table."""
    query = """
        INSERT INTO cards (
            card_id, name, supertype, hp, pokemon_type,
            weakness_type, retreat_cost, image_url,
            trainer_subtype, effect_text
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (card_id) DO UPDATE SET
            name = EXCLUDED.name,
            supertype = EXCLUDED.supertype,
            hp = EXCLUDED.hp,
            pokemon_type = EXCLUDED.pokemon_type,
            weakness_type = EXCLUDED.weakness_type,
            retreat_cost = EXCLUDED.retreat_cost,
            image_url = EXCLUDED.image_url,
            trainer_subtype = EXCLUDED.trainer_subtype,
            effect_text = EXCLUDED.effect_text;
    """
    async with pool.acquire() as conn:
        await conn.executemany(query, rows)

async def main():
    tcgdex = TCGdex("en")  # English locale

    pool = await asyncpg.create_pool(DATABASE_URL, statement_cache_size=0)
    sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    try:
        log.info("Fetching tcgp series set list...")
        set_ids = await fetch_series_sets(tcgdex)
        log.info(f"Found {len(set_ids)} sets: {set_ids}")

        all_card_ids: list[str] = []
        for set_id in set_ids:
            card_ids = await fetch_set_card_ids(tcgdex, set_id)
            log.info(f"Set {set_id}: {len(card_ids)} cards")
            all_card_ids.extend(card_ids)

        log.info(f"Fetching detail for {len(all_card_ids)} cards...")
        tasks = [fetch_card_detail(tcgdex, cid, sem) for cid in all_card_ids]
        cards = await asyncio.gather(*tasks)

        rows = [r for r in (map_to_card_row(c) for c in cards) if r is not None]
        log.info(f"Mapped {len(rows)} valid card rows")

        await upsert_cards(pool, rows)
        log.info("Seeding complete.")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())