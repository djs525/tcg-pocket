"""
Task: Standardized Seeding from Limitless TCG Pocket & Local JSON
Seeds and corrects card details (weakness_type, retreat_cost, trainer_subtype, effect_text)
directly from Limitless TCG Pocket while leveraging the local submodule json file.
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
import random
from concurrent.futures import ThreadPoolExecutor

import asyncpg
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]
SUBMODULE_JSON_PATH = os.path.join("pokemon-cards", "v4.json")

def to_db_prefix(set_id: str) -> str:
    """Normalize user input to match standard TCG Pocket set casing."""
    cleaned = set_id.strip().upper().replace("-", "")
    if cleaned in ("PA", "P-A"):
        return "P-A"
    if cleaned in ("PB", "P-B"):
        return "P-B"
    # Matches codes like B3A -> B3a, A2B -> A2b
    match = re.match(r"^([A-Z]\d+)([A-Z]+)$", cleaned)
    if match:
        return match.group(1) + match.group(2).lower()
    return cleaned

def clean_text(text: str) -> str | None:
    """Clean card rules text and map bracketed energy symbols to curly braces."""
    if not text:
        return None
    # Replace bracketed energy symbols (e.g. [W] -> {W})
    text = re.sub(r"\[([A-Z])\]", r"{\1}", text)
    # Clear redundant whitespaces line-by-line to preserve newlines
    lines = []
    for line in text.splitlines():
        cleaned_line = re.sub(r"[ \t\xa0]+", " ", line).strip()
        if cleaned_line:
            lines.append(cleaned_line)
    return "\n".join(lines)

def scrape_limitless_card(set_code: str, number: str) -> dict | None:
    """Scrape weakness, retreat, and text from Limitless card page."""
    url = f"https://pocket.limitlesstcg.com/cards/{set_code}/{number}"
    
    # Retry logic
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 404:
                log.warning(f"Card page not found at: {url}")
                return None
            resp.raise_for_status()
            break
        except Exception as e:
            if attempt == 2:
                log.error(f"Failed to fetch {url}: {e}")
                return None
            time.sleep(1 + attempt)

    soup = BeautifulSoup(resp.text, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")

    # 1. Weakness & Retreat
    weakness_type = None
    retreat_cost = None
    wrr_el = soup.find(class_="card-text-wrr")
    if wrr_el:
        wrr_text = wrr_el.get_text()
        m_weakness = re.search(r"Weakness:\s*(\w+)", wrr_text)
        if m_weakness:
            weakness_type = m_weakness.group(1).strip()
        
        m_retreat = re.search(r"Retreat:\s*(\d+)", wrr_text)
        if m_retreat:
            retreat_cost = int(m_retreat.group(1).strip())

    # 2. Supertype & Trainer Subtype
    supertype = "Pokemon"
    trainer_subtype = None
    type_el = soup.find(class_="card-text-type")
    if type_el:
        type_text = type_el.get_text()
        if "Trainer" in type_text:
            supertype = "Trainer"
            if "-" in type_text:
                trainer_subtype = type_text.split("-")[-1].strip()
            else:
                trainer_subtype = "Item"
        else:
            supertype = "Pokemon"

    # 3. Effect / Flavor Text
    effect_text = None
    if supertype == "Trainer":
        sections = soup.find_all(class_="card-text-section")
        cand = []
        for sec in sections:
            classes = sec.get("class", [])
            sec_text = sec.get_text()
            if "card-text-artist" in classes or "card-text-flavor" in classes:
                continue
            if "Trainer" in sec_text and ("Supporter" in sec_text or "Item" in sec_text or "Tool" in sec_text):
                continue
            cand.append(sec_text)
        if cand:
            effect_text = clean_text("\n".join(cand))
    else:
        flavor_el = soup.find(class_="card-text-flavor")
        if flavor_el:
            effect_text = clean_text(flavor_el.get_text())

    return {
        "supertype": supertype,
        "weakness_type": weakness_type,
        "retreat_cost": retreat_cost,
        "trainer_subtype": trainer_subtype,
        "effect_text": effect_text
    }

# Variables to track multi-threaded progress printing
scraped_count = 0
total_count = 0
progress_lock = asyncio.Lock()

def process_card(card: dict, target_set: str) -> tuple:
    """Thread function to process card data and scrape Limitless details."""
    global scraped_count, total_count
    
    raw_id = card["id"] # e.g. b3-001
    parts = raw_id.split("-")
    number_part = parts[-1]
    number = str(int(number_part))
    
    # Delay to avoid hitting Limitless rate limits
    time.sleep(2.0 + random.uniform(0.0, 0.5))
    
    details = scrape_limitless_card(target_set, number)
    if details is None:
        details = {
            "supertype": "Pokemon" if card.get("health") else "Trainer",
            "weakness_type": None,
            "retreat_cost": None,
            "trainer_subtype": None,
            "effect_text": None
        }
        
    name = card.get("name", "Unknown")
    hp_val = None
    raw_health = card.get("health")
    if raw_health:
        try:
            hp_val = int(raw_health)
        except ValueError:
            pass
            
    supertype = details.get("supertype", "Pokemon")
    pokemon_type = card.get("type") if supertype == "Pokemon" else None
    image_url = card.get("image")
    
    # Construct standard uppercase ID
    upper_id = f"{target_set.upper()}-{number_part}"
    
    return (
        upper_id,
        name[:100],
        supertype,
        hp_val,
        pokemon_type,
        details["weakness_type"],
        details["retreat_cost"],
        image_url,
        details["trainer_subtype"],
        details["effect_text"]
    )

async def seed_set(pool: asyncpg.Pool, target_set: str, raw_cards: list[dict]):
    global scraped_count, total_count
    
    db_prefix = to_db_prefix(target_set)
    log.info(f"Target Set: '{target_set}' maps to Database Prefix: '{db_prefix}'")
    
    # Filter matching cards
    target_lower = db_prefix.lower()
    matching = [c for c in raw_cards if c["id"].lower().startswith(f"{target_lower}-")]
    
    if not matching:
        log.warning(f"No cards found matching prefix '{target_lower}-' in v4.json.")
        return
        
    total_count = len(matching)
    scraped_count = 0
    log.info(f"Found {total_count} cards for {db_prefix}. Scraping details from Limitless TCG...")
    
    loop = asyncio.get_running_loop()
    rows = []
    
    # Execute web scraping sequentially to avoid rate limits
    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = []
        for card in matching:
            fut = loop.run_in_executor(executor, process_card, card, db_prefix)
            futures.append(fut)
            
        for index, fut in enumerate(asyncio.as_completed(futures)):
            row = await fut
            rows.append(row)
            scraped_count += 1
            if scraped_count % 25 == 0 or scraped_count == total_count:
                log.info(f"  Scraped/Mapped [{scraped_count}/{total_count}] cards...")

    # Rely on upsert transaction to prevent data loss; obsolete card IDs will be cleaned up afterwards.

    # Bulk insert cards
    log.info(f"Executing batch insert for {len(rows)} cards...")
    query = """
        INSERT INTO cards (
            card_id, name, supertype, hp, pokemon_type, 
            weakness_type, retreat_cost, image_url, trainer_subtype, effect_text
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
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
        async with conn.transaction():
            await conn.executemany(query, rows)
            
    # Cleanup obsolete card IDs after successful transaction
    inserted_ids = [row[0] for row in rows]
    log.info(f"Cleaning up obsolete card IDs for {db_prefix}...")
    async with pool.acquire() as conn:
        deleted = await conn.execute(
            "DELETE FROM cards WHERE LOWER(card_id) LIKE $1 AND NOT (card_id = ANY($2::varchar[]))",
            f"{target_lower}-%",
            inserted_ids
        )
        log.info(f"  Cleanup results: {deleted}")
            
    log.info(f"Synchronized {len(rows)} cards for {db_prefix} successfully!")

async def main():
    if len(sys.argv) < 2:
        log.error("Please provide at least one set code. Example: python seed_limitless_cards.py B3 B3a")
        return
        
    targets = sys.argv[1:]
    
    if not os.path.exists(SUBMODULE_JSON_PATH):
        log.error(f"Could not find submodule json at: {SUBMODULE_JSON_PATH}")
        return
        
    log.info(f"Loading submodule data from {SUBMODULE_JSON_PATH}...")
    with open(SUBMODULE_JSON_PATH, "r", encoding="utf-8") as f:
        raw_cards = json.load(f)
        
    pool = await asyncpg.create_pool(DATABASE_URL, statement_cache_size=0)
    try:
        for target in targets:
            await seed_set(pool, target, raw_cards)
    finally:
        await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
