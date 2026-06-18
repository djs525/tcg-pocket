# Pokémon TCG Pocket Meta Analyzer — Implementation Blueprint

This document is the canonical architecture and sprint reference for the PTCGP Meta Analyzer.
It covers system topology, the full database schema, implementation sprints, and engineering risk mitigations.

**Stack:** Python · PostgreSQL 15+ · FastAPI · Redis · Next.js · LangChain

> **Architecture Pivot (v2):** The platform no longer depends on Discord crowd-sourced match
> reporting as a primary data source. PTCGP's rigid 20-card structure makes purely
> mathematical analysis both feasible and authoritative. `deck_matchups` is now populated
> entirely by the Monte Carlo simulator; the `matches` table is retained for optional
> future use but is not on the critical path.

---

## 1. System Topology & Data Engineering Pipeline

```
[ Limitless Scraper ]        [ TCGdex SDK (tcgp series) ]
         │                              │
         ▼                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Data Ingestion Pipeline                            │
│  - Standardizes Card IDs (e.g., A1-001) across all sources                 │
│  - Normalizes free-text deck strings via fuzzy matching + alias lookup      │
│  - Jaccard clustering on Limitless decklists → Core vs. Tech Variance map  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        PostgreSQL Database (v15+)                           │
│  Tables: cards · archetypes · deck_matchups · simulated_states             │
│  (matches table retained but non-critical)                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                 Predictive Consistency & Simulation Engine                  │
│  - Opening Hand & Brick Analyzer   (hypergeometric + Basic-redraw Monte Carlo) │
│  - Tech Card & Variance Tracker    (Jaccard core/tech split across 100+ lists) │
│  - Monte Carlo Combat Simulator    (10k runs · populates deck_matchups)        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
              [ FastAPI + WebSocket/Redis ] ──► [ Next.js Frontend ]
```

### PTCGP Game Rule Constraints

These rules are hard constraints that every simulation and validation component must respect.

| Rule | Value |
|------|-------|
| Deck size | Fixed at **20 cards** |
| Max duplicates per card | **2 copies** |
| Minimum Basic Pokémon | **1** (deck is illegal otherwise) |
| Bench size | Maximum **3** Pokémon |
| Opening hand | Player draws **5**; guaranteed redraw until ≥1 Basic is in hand |
| Victory condition | First to **3 points** (no Prize Cards) |
| Weakness adjustment | Flat **+20 damage** (not ×2) |
| Energy system | Deterministic **Energy Zone** — 1 energy of the deck's type added per turn; no Energy cards in the main deck |

---

## 2. Database Schema

The live schema file is [`db/schema.sql`](./db/schema.sql). All tables use `IF NOT EXISTS` and are safe to re-run.

```sql
-- Engine v1.2.0 | Target: PostgreSQL 15+

-- ─────────────────────────────────────────────────────────────────────
-- TABLE: cards
-- Base seeded by seed_cards.py (TCGdex SDK).
-- Enriched per-set by seed_limitless_cards.py (Limitless scraper).
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cards (
    card_id         VARCHAR(50)  PRIMARY KEY,   -- Standardized ID e.g. 'A1-001'
    name            VARCHAR(100) NOT NULL,
    supertype       VARCHAR(20)  NOT NULL,       -- 'Pokemon' | 'Trainer'
    hp              INTEGER,                     -- NULL for Trainers
    pokemon_type    VARCHAR(20),                 -- 'Lightning', 'Psychic', etc. NULL for Trainers
    weakness_type   VARCHAR(20),                 -- NULL for Trainers / cards with no weakness
    retreat_cost    INTEGER,                     -- NULL for Trainers
    image_url       TEXT,
    trainer_subtype VARCHAR(30),                 -- 'Item' | 'Supporter' | 'Tool' — NULL for Pokémon
    effect_text     TEXT                         -- Trainer effect or Pokémon flavor text
);

-- ─────────────────────────────────────────────────────────────────────
-- TABLE: archetypes
-- Named deck archetypes with tier ratings, empirical win rates,
-- and the energy type(s) the deck's Energy Zone generates.
-- Populated manually (Phase 2 bootstrap) then auto-derived (Phase 3).
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS archetypes (
    archetype_id    SERIAL       PRIMARY KEY,
    name            VARCHAR(100) UNIQUE NOT NULL, -- e.g. 'Mewtwo ex / Gardevoir'
    tier_rating     VARCHAR(10),                  -- 'Tier 1' | 'Tier 2' | 'Rogue'
    win_rate        NUMERIC(5,2),                 -- Aggregate empirical win rate (0.00–100.00)
    energy_type_1   VARCHAR(20)  NOT NULL,        -- Primary Energy Zone type e.g. 'Psychic'
    energy_type_2   VARCHAR(20)                   -- Optional secondary type for dual-energy decks
);

-- ─────────────────────────────────────────────────────────────────────
-- TABLE: matches
-- Raw match reports submitted via Discord /report.
-- result is always from archetype_a's perspective.
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS matches (
    match_id            SERIAL      PRIMARY KEY,
    reporter_discord_id VARCHAR(50),              -- Discord user ID for fraud tracking
    archetype_a_id      INTEGER     NOT NULL REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    archetype_b_id      INTEGER     NOT NULL REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    went_first          BOOLEAN     NOT NULL,     -- TRUE = archetype_a went first
    result              VARCHAR(10) NOT NULL      -- 'win' | 'loss' for archetype_a
                            CHECK (result IN ('win', 'loss')),
    reported_at         TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────
-- TABLE: deck_matchups
-- Aggregated empirical win rates split by turn order.
-- Recomputed by compile_matchup_matrix.py on a scheduled basis.
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS deck_matchups (
    archetype_a_id        INTEGER      NOT NULL REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    archetype_b_id        INTEGER      NOT NULL REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    games_played_a_first  INTEGER      NOT NULL DEFAULT 0,
    win_rate_a_first      NUMERIC(5,2) NOT NULL DEFAULT 0.00, -- Win rate of A going first
    games_played_a_second INTEGER      NOT NULL DEFAULT 0,
    win_rate_a_second     NUMERIC(5,2) NOT NULL DEFAULT 0.00, -- Win rate of A going second
    PRIMARY KEY (archetype_a_id, archetype_b_id)
);

-- ─────────────────────────────────────────────────────────────────────
-- TABLE: simulated_states
-- Monte Carlo simulation snapshots (10k runs per matchup/turn).
-- Written by monte_carlo_sim.py; one row per (A, B, turn_number).
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS simulated_states (
    simulation_id     SERIAL      PRIMARY KEY,
    archetype_a_id    INTEGER     NOT NULL REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    archetype_b_id    INTEGER     NOT NULL REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    turn_number       INTEGER     NOT NULL,
    state_description TEXT,
    win_probability_a NUMERIC(5,2) NOT NULL,     -- P(A wins) at this turn (0.00–100.00)
    last_updated      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

---

## 3. Implementation Sprints

### Phase 1: Ingestion, Normalization & Syncing — ✅ COMPLETE (Weeks 1–3)

| Task | Status | Script |
|------|--------|--------|
| **1.1** TCGdex SDK base seeding | ✅ Complete | `scripts/seed_cards.py` |
| **1.2** Limitless scraper & enrichment engine | ✅ Complete | `scripts/seed_limitless_cards.py` |
| **1.3** Deck clustering & archetype naming | 🔜 Deferred → Task 3.5 | `scripts/archetype_classifier.py` |

#### Task 1.1 — TCGdex SDK Base Seeding ✅
Async script targeting the `tcgp` series. Fetches all cards, standardizes IDs and names, and upserts into the `cards` table via the `tcgdex-sdk`. Concurrency is bounded by a semaphore (`MAX_CONCURRENT_REQUESTS = 8`) to avoid hammering the API.

#### Task 1.2 — Limitless Scraper & Enrichment Engine ✅
Per-set ingestion script that scrapes [pocket.limitlesstcg.com](https://pocket.limitlesstcg.com) for canonical weakness, retreat cost, trainer subtype, and effect text. Reads the local `pokemon-cards` submodule (`v4.json`) as a base layer and falls back to live scraping for fields the submodule lacks. Includes:
- Rate-limited delay loops (`time.sleep` + jitter) to avoid IP bans
- `to_db_prefix()` normalizer for set code casing (e.g. `B3A` → `B3a`)
- Cleanup pass: removes stale card IDs for a given set after a successful sync
- Invocation: `python seed_limitless_cards.py <SET_CODE> [SET_CODE...]`

> **Note on Seeder Precedence:** `seed_limitless_cards.py` performs a richer upsert than `seed_cards.py` and should always be run after it for any set that needs enrichment. `seed_cards.py` serves as the initial bootstrap only.

#### Task 1.3 — Deck Clustering & Archetype Naming 🔜 (Redesigned as Task 3.5)
Originally dependent on Discord-submitted deck data. Redesigned as a **Jaccard core/tech variance tracker** operating directly on Limitless tournament decklists — see Task 3.5 for the full specification.

---

### Phase 2: Mathematical Simulation & Matchup Engine (Weeks 4–8)

| Task | Status | Script |
|------|--------|--------|
| **2.0** Automated archetype seeding from Limitless | ✅ Complete | `scripts/seed_archetypes.py` |
| **2.1** Monte Carlo matchup matrix populator | 🔧 In Progress | `scripts/compile_matchup_matrix.py` |
| **2.2** Opening Hand & Brick Analyzer | 🔜 Planned | `scripts/opening_hand_sim.py` |
| **2.3** Monte Carlo Combat Simulator | 🔜 Planned | `scripts/monte_carlo_sim.py` |

#### Task 2.0 — Automated Archetype Seeding from Limitless ✅
*Script:* `scripts/seed_archetypes.py`

Scrapes the Limitless TCG Pocket deck matrix for a given set code, filters for archetypes with **100+ recorded plays**, and upserts them into the `archetypes` table. Energy Zone types are resolved by navigating to a sample player decklist and cross-referencing card IDs against the `cards` table. Includes:
- `to_db_prefix()` normalizer for consistent set code casing
- Jittered `time.sleep` delay loop to avoid IP bans on the Limitless scraper
- `assign_tier()` heuristic: ≥500 plays → Tier 1, ≥150 → Tier 2, else Rogue
- Atomic upsert: `ON CONFLICT (name) DO UPDATE` for idempotent re-runs
- Invocation: `python seed_archetypes.py <SET_CODE> [SET_CODE...]` (defaults to `B3a`)

#### Task 2.1 — Monte Carlo Matchup Matrix Populator
*Script:* `scripts/compile_matchup_matrix.py`

**Pivot:** `deck_matchups` is no longer compiled from crowd-sourced `matches` rows. It is now populated entirely by the Monte Carlo Combat Simulator (Task 2.3). This script enumerates every `(archetype_a, archetype_b)` pair from the `archetypes` table, invokes the simulator for each pairing, and upserts the aggregated win-rate results.

```python
# Pseudocode — full implementation in Task 2.3 details
for a, b in itertools.combinations(archetypes, 2):
    result_a_first  = monte_carlo_sim(a, b, went_first=True,  n=10_000)
    result_a_second = monte_carlo_sim(a, b, went_first=False, n=10_000)
    upsert_deck_matchup(a, b,
        games_played_a_first  = 10_000, win_rate_a_first  = result_a_first,
        games_played_a_second = 10_000, win_rate_a_second = result_a_second
    )
```

Designed to run as an **offline scheduled job** (e.g. nightly cron) after `seed_archetypes.py` populates new archetypes. Results are cached in Redis with a 1-hour TTL.

#### Task 2.2 — Opening Hand & Brick Analyzer
*Script:* `scripts/opening_hand_sim.py`

This is the **primary user-facing analytical feature**. Because a PTCGP deck is only 20 cards, opening hand math determines meta viability. Two computation modes:

**Closed-form hypergeometric** (fast, ignores redraw rule):
```
P(X ≥ 1) = 1 − C(deck_size − copies, hand_size) / C(deck_size, hand_size)
```

**Monte Carlo with Basic guarantee** (canonical; accounts for redraw):
Simulates `n` opening hands, redrawing each until ≥1 Basic Pokémon is present, then computes the empirical frequency of the target card appearing. Default `n = 100,000` for statistical stability.

**Metrics exposed to the frontend:**

| Metric | Description |
|--------|-------------|
| **Turn 1 Attachment %** | Exact probability of opening with a key Supporter (e.g. Misty) *and* its required Basic in the same hand |
| **Brick Index** | Probability that the guaranteed-Basic redraw forces an undesirable Basic (e.g. a prize liability) into the opening hand |
| **Avg. Redraw Count** | Expected number of forced redraws this deck composition triggers per game |
| **Setup Curve** | Turn-by-turn probability of having the main attacker powered up and active |

Output: probability tables queryable by archetype, card name, and copy count.

#### Task 2.3 — Monte Carlo Combat Simulator
*Script:* `scripts/monte_carlo_sim.py`

Stochastic turn-by-turn game simulator. Runs **10,000 iterations** per archetype matchup and writes results to `simulated_states`. Built in five sub-steps:

| Sub-task | Description |
|----------|-------------|
| **2.3a** Energy Zone model | Deterministic +1 energy per turn from `archetypes.energy_type_1/2` |
| **2.3b** Damage & weakness | `base_damage + 20` if attacker type matches `weakness_type`; 3-point win condition |
| **2.3c** Coin flip modeling | Independent Bernoulli trials per flip (Misty, Poké Ball, etc.) |
| **2.3d** Full combat loop | Turn sequence: draw energy → attack if cost met → apply damage → check KO → award points; safety cap at 30 turns |
| **2.3e** Output & DB write | Compute win probability + TTKO distribution; upsert one row per `(archetype_a, archetype_b, turn_number)` into `simulated_states` |

---

### Phase 3: Service Layer & Analytics Features (Weeks 8–11)

| Task | Status | Script / Service |
|------|--------|-----------------|
| **3.1** FastAPI backend | 🔜 Planned | `backend/` |
| **3.2** Redis cache layer | 🔜 Planned | `backend/cache.py` |
| **3.3** LLM Strategy Advisor | 🔜 Planned | `backend/advisor.py` |
| **3.4** Tech Card & Variance Tracker | 🔜 Planned | `scripts/tech_variance_tracker.py` |
| **3.5** Simulated Matchup Matrix API | 🔜 Planned | `backend/` |

#### Task 3.1 — FastAPI Backend
Build the core API layer. Required endpoints:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/meta/matrix` | Returns full simulated matchup win-rate matrix split by turn order |
| `POST` | `/api/v1/deck/analyze` | Accepts a 20-card deck list; returns opening hand probabilities + TTKO distribution |
| `GET` | `/api/v1/archetype/{id}/variance` | Returns core card set + tech variance breakdown for an archetype |
| `WS` | `/api/v1/simulation/live` | Real-time WebSocket stream of Monte Carlo simulation step updates |

#### Task 3.2 — Redis Cache Layer
Configure a Redis container alongside FastAPI. Cache:
- Static card metadata (invalidated only on new set ingestion)
- Simulated matchup matrices (TTL: 1 hour, force-refreshed after `compile_matchup_matrix.py` runs)
- Opening hand probability tables per archetype (TTL: 24 hours)

Target: **sub-50ms** response time for all cached reads.

#### Task 3.3 — LLM Strategy Advisor
Integrate an LLM (e.g. `claude-sonnet-4-5`, `gpt-4o`, or a self-hosted `Llama-3-8B-Instruct`) to serve contextual strategy recommendations. The LLM receives a structured RAG context containing:
- Canonical card texts for all cards in both decks
- Full PTCGP ruleset (weakness rule, energy zone, bench limit)
- Simulated matchup win rates and TTKO distributions from `simulated_states`
- Tech variance data for each archetype (from Task 3.4)

Responses are schema-validated (Pydantic) to prevent hallucinated card interactions from reaching the user.

#### Task 3.4 — Tech Card & Variance Tracker
*Script:* `scripts/tech_variance_tracker.py`

Mines the 100+ tournament decklists already scraped per archetype from Limitless to extract structural insights. For each archetype, applies Jaccard similarity clustering to discover which cards are load-bearing and which are flex slots.

**Core/Tech Split Algorithm:**
```
For archetype X with N scraped decklists:
  1. Build card_id frequency map across all N lists
  2. "Immutable Core"  = cards present in ≥ 80% of lists
  3. "Tech Variance"   = remaining slots (present in < 80%)
  4. For each tech card, record: frequency, win_rate_when_included (from archetype win_rate proxy)
```

**Output stored in a new `archetype_tech_cards` table** (see schema addition below):

```sql
CREATE TABLE IF NOT EXISTS archetype_tech_cards (
    archetype_id    INTEGER     NOT NULL REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    card_id         VARCHAR(50) NOT NULL REFERENCES cards(card_id) ON DELETE CASCADE,
    is_core         BOOLEAN     NOT NULL,   -- TRUE = present in ≥80% of lists
    frequency       NUMERIC(5,2) NOT NULL,  -- % of scraped lists including this card
    last_updated    TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (archetype_id, card_id)
);
```

**Live meta-shift tracking:** The tracker runs on a scheduled basis and diffs against the previous snapshot to surface trending tech cards, e.g.:
> *"Over the last 72 hours, successful Pikachu ex variants have dropped Sabrina −1 and added X Speed +1, correlating with the rise of Weezing stall decks."*

#### Task 3.5 — Simulated Matchup Matrix Endpoints
Expose the `simulated_states` and `deck_matchups` tables — fully populated by Task 2.3 — through paginated API endpoints. Users select two archetypes and receive:
- A **turn-by-turn win-probability curve** (`simulated_states` rows for that pairing)
- A **TTKO distribution graph** (how fast each deck achieves its 3-point condition)
- First/second win-rate splits from `deck_matchups`

No human match data is required; the entire matrix is derived from simulation.

**Phase 3 dependencies to add:**
```
langchain
langchain-openai        # swap for langchain-anthropic / langchain-google-genai as needed
langchain-community     # TavilySearchResults
tavily-python           # Tavily web search backend
pydantic                # structured output parsing & validation
```

---

### Phase 4: Interface Realization & Verification (Weeks 11–12)

#### Task 4.1 — Consistency Dashboard (Opening Hand & Brick Analyzer)
Interactive Next.js panel where users select a scraped Limitless archetype (or input a custom 20-card list) and instantly see:
- **Turn 1 Attachment Probability** — exact % chance of opening with a key Supporter and its required Basic
- **Brick Index** — probability the guaranteed-Basic redraw forces an undesirable Basic into hand
- **Avg. Redraw Count** — expected forced redraws per game for this deck composition
- **Setup Curve** — turn-by-turn probability of main attacker being powered and active
- Card image rendering via TCGdex `image_url` fields with lazy-load skeleton placeholders

#### Task 4.2 — Simulated Matchup Matrix Dashboard
Split-screen panel for head-to-head analysis. Users select two archetypes and see:
- First/second win-rate splits from `deck_matchups` (Monte Carlo derived)
- Turn-by-turn win-probability curve from `simulated_states`
- TTKO distribution graph (how fast each deck closes out its 3-point victory condition)
- Turn-order toggle (Going First / Going Second)

#### Task 4.3 — Tech Variance Live Feed
Dashboard panel displaying real-time tech card shifts per archetype sourced from `archetype_tech_cards`. Highlights the **Immutable Core** (≥80% frequency) vs. the **4 flex slots**, and surfaces trending inclusions and drops detected since the last scrape cycle.

#### Task 4.4 — Edge Case Validation Guards
Automated structural guards enforced at the API layer:
- Block deck submissions with >2 copies of any single `card_id`
- Reject decks with no Basic Pokémon (simulation would be illegal)
- Validate Energy Zone profile selections against the `archetypes` table before simulation runs

---

## 4. Project File Structure

```
tcg-pocket/
├── db/
│   └── schema.sql                      ← All 5 tables; safe to re-run
├── scripts/
│   ├── seed_cards.py                   ← Phase 1 · TCGdex bootstrap seeder
│   ├── seed_limitless_cards.py         ← Phase 1 · Per-set Limitless enrichment
│   ├── seed_archetypes.py              ← Phase 2 ✅ · Automated Limitless archetype seeder
│   ├── compile_matchup_matrix.py       ← Phase 2 · Monte Carlo matchup matrix populator
│   ├── opening_hand_sim.py             ← Phase 2 · Opening Hand & Brick Analyzer engine
│   ├── monte_carlo_sim.py              ← Phase 2 · Full combat simulator (10k runs)
│   └── tech_variance_tracker.py        ← Phase 3 · Jaccard core/tech split on Limitless lists
├── backend/                            ← Phase 3 · FastAPI service (TBD)
├── frontend/                           ← Phase 4 · Next.js dashboard (TBD)
├── pokemon-cards/                      ← Git submodule: chase-mew/pokemon-tcg-pocket-cards
├── requirements.txt
└── GEMINI.md                           ← This file
```

---

## 5. Dependencies by Phase

### Phase 1 (current)
```
tcgdex-sdk       # TCGdex API client
asyncpg          # Async PostgreSQL driver
python-dotenv    # .env loading
requests         # Limitless HTTP scraping
beautifulsoup4   # HTML parsing for Limitless scraper
```

### Phase 2 (add before simulation work)
```
scipy            # Hypergeometric distribution calculations
numpy            # Array math for Monte Carlo result aggregation
tqdm             # Progress bars for long-running simulations
```

### Phase 3 (add before service layer work)
```
fastapi
uvicorn[standard]
redis
langchain
langchain-openai          # or langchain-anthropic / langchain-google-genai
pydantic                  # Structured output parsing
```

---

## 6. Engineering Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| **Coin-flip simulation variance** (Misty, Poké Ball) — high variance produces unstable win rates | Enforce minimum **10,000 Monte Carlo iterations**; cache output distributions in `simulated_states`; do not recompute on every API call |
| **LLM hallucinations on card rules** — Strategy Advisor (Task 3.3) may suggest illegal interactions | Feed canonical card texts and the full PTCGP ruleset in every system prompt (RAG pattern); enforce Pydantic JSON schema validation on all LLM outputs |
| **Jaccard clustering noise** — tech variance tracker may misidentify flex slots if sample size is small | Only run Jaccard analysis on archetypes with ≥ 20 scraped decklists; surface sample size alongside frequency percentages in the UI; flag low-confidence core cards |
| **Limitless scraper IP bans** — excessive scraping triggers rate limiting | Rate-limit all scrape loops with `time.sleep(2)` + random jitter; never run the scraper from the application hot path; schedule as an isolated offline job |
| **TCGdex / Limitless ID divergence** — differing set codes and card name formats between sources | All card IDs must pass through `to_db_prefix()` normalization before any DB write; raw strings never touch downstream queries |
| **Monte Carlo realism ceiling** — simplified combat AI (always attack if cost met) may not reflect true meta play patterns | Document simulation assumptions clearly in the UI; label all matchup data as *simulated* not *empirical*; plan a future agent upgrade with priority-queue attack logic |
| **Schema migration for `archetype_tech_cards`** — new table added in pivot | Add `IF NOT EXISTS` guard to the CREATE statement in `schema.sql`; re-run is safe and non-destructive |