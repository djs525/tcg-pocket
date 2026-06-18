"""
Task 2.0: Automated Archetype Seeding from Limitless TCG Pocket
Scrapes top tournament decks played 100+ times, extracts their metrics, 
and resolves their Energy Zone types by mapping card IDs found in player decklists.
"""

import asyncio
import logging
import os
import re
import sys
import time
import random
import requests
from bs4 import BeautifulSoup
import asyncpg
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]

def to_db_prefix(set_id: str) -> str:
    """Normalize Limitless URL set codes to match our standard database casing."""
    cleaned = set_id.strip().upper().replace("-", "")
    if cleaned in ("PA", "P-A"):
        return "P-A"
    if cleaned in ("PB", "P-B"):
        return "P-B"
    match = re.match(r"^([A-Z]\d+)([A-Z]+)$", cleaned)
    if match:
        return match.group(1) + match.group(2).lower()
    return cleaned

def fetch_soup(url: str) -> BeautifulSoup | None:
    """Helper to fetch a URL with basic retry and a protective user-agent to prevent IP bans."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    for attempt in range(3):
        try:
            time.sleep(2.0 + random.uniform(0.0, 0.5))  # Anti-banning jittered delay loop
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            if attempt == 2:
                log.error(f"Failed to fetch {url}: {e}")
                return None
            time.sleep(2 + attempt)
    return None

async def resolve_energy_types(pool: asyncpg.Pool, list_url: str) -> tuple[str, str | None]:
    """Navigates to a player decklist URL and infers Energy Zone types from card IDs."""
    log.info(f"    Fetching sample player decklist: {list_url}")
    soup = fetch_soup(list_url)
    if not soup:
        return "Colorless", None

    found_ids = set()
    page_text = soup.get_text()

    # Limitless renders Pocket IDs as plain text in parentheses: e.g., "Venusaur ex (A1-4)"
    # Regex captures the prefix and the number separately: group 1 = A1, group 2 = 4
    id_matches = re.findall(r'\(([A-Za-z0-9]+)-(\d+)\)', page_text)
    
    for set_code, num_part in id_matches:
        db_set = to_db_prefix(set_code)
        # Limitless strips leading zeros; our DB (via TCGdex) expects 3 digits e.g., '004'
        db_num = num_part.zfill(3)
        found_ids.add(f"{db_set}-{db_num}")

    if not found_ids:
        log.warning("    No card IDs resolved from decklist text. Defaulting to Colorless.")
        return "Colorless", None

    # Query the cards table to find unique Pokémon type configurations
    async with pool.acquire() as conn:
        query = """
            SELECT DISTINCT pokemon_type 
            FROM cards 
            WHERE card_id = ANY($1)
              AND supertype = 'Pokemon' 
              AND pokemon_type IS NOT NULL;
        """
        rows = await conn.fetch(query, list(found_ids))
        types = [r['pokemon_type'] for r in rows if r['pokemon_type'] != 'Colorless']

    energy_1 = types[0] if len(types) > 0 else "Colorless"
    energy_2 = types[1] if len(types) > 1 else None
    return energy_1, energy_2

def assign_tier(count: int) -> str:
    """Heuristic tier mapping based on competitive meta representation."""
    if count >= 500:
        return "Tier 1"
    elif count >= 150:
        return "Tier 2"
    return "Rogue"

async def seed_set_archetypes(pool: asyncpg.Pool, set_code: str):
    """Scrapes the Limitless Pocket decks matrix, filters for 100+ plays, and upserts."""
    url = f"https://play.limitlesstcg.com/decks?game=pocket&set={set_code}"
    log.info(f"Targeting Set Matrix: {set_code} via {url}")
    
    soup = fetch_soup(url)
    if not soup:
        log.error(f"Could not load archetype matrix page for set {set_code}")
        return

    rows = soup.find_all('tr')
    valid_decks = []

    for row in rows:
        cells = row.find_all('td')
        if len(cells) < 3:
            continue

        # Match structural links going to archetype profiles
        link_el = row.find('a', href=re.compile(r'/decks/[\w-]+'))
        if not link_el or '/matchups' in link_el['href']:
            continue

        deck_name = link_el.get_text(strip=True)
        deck_href = link_el['href']

        count = None
        win_rate = None

        for cell in cells:
            text = cell.get_text(strip=True)
            if text.isdigit():
                count = int(text)
            elif '%' in text:
                match = re.search(r'([\d.]+)', text)
                if match:
                    win_rate = float(match.group(1))

        # Filter requirement: Only process decks played 100+ times
        if count is not None and count >= 100:
            valid_decks.append({
                "name": deck_name,
                "count": count,
                "win_rate": win_rate or 50.00,
                "href": deck_href
            })

    log.info(f"Found {len(valid_decks)} meta archetypes meeting the 100+ play threshold.")

    for idx, deck in enumerate(valid_decks):
        log.info(f"Processing Archetype [{idx+1}/{len(valid_decks)}]: {deck['name']} (Plays: {deck['count']})")
        
        clean_path = deck['href'].split('?')[0]

        # Build a clean, properly formatted detail URL
        deck_detail_url = f"https://play.limitlesstcg.com{clean_path}?game=POCKET&set={set_code}"
        detail_soup = fetch_soup(deck_detail_url)
        
        list_href = None
        if detail_soup:
            # Locate any player or tournament decklist page pattern
            list_link_el = detail_soup.find('a', href=re.compile(r'/tournament/.+/player/.+/decklist|/player/.+/decklist'))
            if list_link_el:
                list_href = list_link_el.get('href')

        if list_href:
            energy_1, energy_2 = await resolve_energy_types(pool, f"https://play.limitlesstcg.com{list_href}")
        else:
            log.warning(f"    No sample player decklist available for {deck['name']}. Defaulting to Colorless.")
            energy_1, energy_2 = "Colorless", None

        tier_rating = assign_tier(deck['count'])

        # Enforce atomic upsert into database schema
        query = """
            INSERT INTO archetypes (name, tier_rating, win_rate, energy_type_1, energy_type_2)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (name) DO UPDATE SET
                tier_rating = EXCLUDED.tier_rating,
                win_rate = EXCLUDED.win_rate,
                energy_type_1 = EXCLUDED.energy_type_1,
                energy_type_2 = EXCLUDED.energy_type_2;
        """
        async with pool.acquire() as conn:
            await conn.execute(query, deck['name'], tier_rating, deck['win_rate'], energy_1, energy_2)
            log.info(f"    Successfully synchronized: {deck['name']} -> [{tier_rating} | {energy_1} / {energy_2}]")

async def main():
    if len(sys.argv) < 2:
        log.info("No set codes provided via CLI. Defaulting pipeline run to 'B3a'.")
        targets = ["B3a"]
    else:
        targets = sys.argv[1:]

    pool = await asyncpg.create_pool(DATABASE_URL, statement_cache_size=0)
    try:
        for target in targets:
            await seed_set_archetypes(pool, target)
    finally:
        await pool.close()
    log.info("Archetype meta seeding pipeline complete.")

if __name__ == "__main__":
    asyncio.run(main())