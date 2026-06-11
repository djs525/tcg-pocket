# Pokémon TCG Pocket Meta Analyzer - Implementation Blueprint

This markdown document is optimized for CLI engines, project parses, and developer execution environments. It contains the structural architecture, logic schemas, and execution vectors for building an analytical engine for PTCGP using the `tcgdex-sdk`, Limitless tournament pools, and crowdsourced ladder tracking data.

---

## 1. System Topology & Data Engineering Pipeline

```
[ Limitless API / Scraper ]   [ TCGdex SDK (tcgp) ]   [ Crowdsourced Tracker Ingestion ]
            │                         │                                  │
            ▼                         ▼                                  ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                Data Ingestion Pipeline                                 │
│    - Standardizes Card IDs (e.g., A1-001) across all sources                           │
│    - Normalizes user uploads & match reports                                           │
│    - Computes global deck archetypes via Jaccard Similarity & tech-choice tagging      │
└────────────────────────────────────────────────────────────────────────────────────────┘
                                            │
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              PostgreSQL Database (Vector)                              │
│   - Tables: cards, archetypes, matches, deck_matchups, simulated_states                │
└────────────────────────────────────────────────────────────────────────────────────────┘
                                            │
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               Core Logic & Simulation Engine                           │
│   - Matrix Compiler: Compiles split matchup win rates (first vs. second)               │
│   - Stochastic Turn-0 Hypergeometric Simulator (accounts for guaranteed Basic rule)    │
│   - Monte Carlo Combat Simulator: 10k runs modeling coin-flips & Energy Zone curves   │
└────────────────────────────────────────────────────────────────────────────────────────┘
       │                                     │                                    │
       ▼                                     ▼                                    ▼
[ FastAPI Backend (WS/Redis) ] ──► [ Next.js Frontend ] ──► [ Discord Bot Service (discord.py) ]
```

### Data Pipeline Constraints (PTCGP Specs)
* **Deck Size:** Fixed at 20 cards.
* **Bench Size:** Maximum 3 spots.
* **Victory Condition:** First to 3 points (No Prize Cards layout).
* **Weakness Adjustment:** Static +20 damage adjustment (instead of double damage).
* **Energy Rule Matrix:** Structured Zone distribution (Deterministic per turn based on chosen deck energy profile, no Energy cards in the main deck).

---

## 2. Comprehensive Relational & Analytical Schema

```sql
-- Schema Verification: Engine v1.2.0
-- Target Database: PostgreSQL 15+

CREATE TABLE cards (
    card_id VARCHAR(50) PRIMARY KEY, -- Standardized ID (e.g., 'A1-001')
    name VARCHAR(100) NOT NULL,
    supertype VARCHAR(20) NOT NULL, -- 'Pokémon', 'Trainer', 'Energy'
    hp INTEGER,
    pokemon_type VARCHAR(20),       -- 'Lightning', 'Psychic', 'Water', etc.
    weakness_type VARCHAR(20),
    retreat_cost INTEGER,
    image_url TEXT
);

CREATE TABLE archetypes (
    archetype_id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL, -- e.g., 'Mewtwo ex / Gardevoir'
    tier_rating VARCHAR(5),            -- 'Tier 1', 'Tier 2', 'Rogue'
    win_rate NUMERIC(5,2),
    energy_type_1 VARCHAR(20) NOT NULL, -- Primary energy generation type (e.g., 'Psychic')
    energy_type_2 VARCHAR(20)           -- Optional secondary energy type (e.g., 'Water' for multi-type)
);

CREATE TABLE matches (
    match_id SERIAL PRIMARY KEY,
    reporter_discord_id VARCHAR(50),
    archetype_a_id INTEGER REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    archetype_b_id INTEGER REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    went_first BOOLEAN NOT NULL,
    result VARCHAR(10) NOT NULL, -- 'win' or 'loss' for Archetype A
    reported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE deck_matchups (
    archetype_a_id INTEGER REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    archetype_b_id INTEGER REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    games_played_a_first INTEGER DEFAULT 0,
    win_rate_a_first NUMERIC(5,2) DEFAULT 0.00,  -- Empirical win rate of A when going first
    games_played_a_second INTEGER DEFAULT 0,
    win_rate_a_second NUMERIC(5,2) DEFAULT 0.00, -- Empirical win rate of A when going second
    PRIMARY KEY (archetype_a_id, archetype_b_id)
);

CREATE TABLE simulated_states (
    simulation_id SERIAL PRIMARY KEY,
    archetype_a_id INTEGER REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    archetype_b_id INTEGER REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    turn_number INTEGER NOT NULL,
    state_description TEXT,
    win_probability_a NUMERIC(5,2) NOT NULL,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 3. Implementation Plan by Development Sprints

### Phase 1: Ingestion, Normalization & Syncing (Weeks 1-3)

* **Task 1.1: TCGdex SDK Base Seeding**
Write an asynchronous script targeting the `tcgp` series. Standardize names, remove special character formatting irregularities, and populate the base `cards` reference schema.
* **Task 1.2: Limitless & Crowdsourced Ingestion Engine**
Construct an ingestion engine handling Limitless scraper outputs and crowdsourced log APIs. Map free-text user deck strings (`"Mewtwo-EX"`, `"Mewtwo ex"`) into clean `card_id` sequences via fuzzy string matching and internal alias lookup layouts.
* **Task 1.3: Dynamic Deck Clustering & Tech Tagging**
Implement a script calculating similarity matrices. Categorize decks under meta-archetypes using an 85% Jaccard threshold, but flag any card deviations as "tech options" (e.g. Erika, Sabrina, or Red Card counts) to prevent washing out their impact.

### Phase 2: Mathematical Simulation & Matchup Engine (Weeks 4-7)

* **Task 2.1: Turn-Order Matrix Compiler**
Compute empirical matchup datasets over historic pairings, splitting win/loss records by who went first vs. second.
* **Task 2.2: Stochastic Turn-0 Opening Hand Engine**
Build probability computation models for initial configurations. Since PTCGP uses a hard 20-card layout drawing 5 opening cards, code combinatorial layers evaluating $P(X \ge 1)$ of drawing specific starting assets. *Constraint:* The algorithm must model the game's guaranteed Basic Pokémon rule (redrawing opening hands until at least one Basic Pokémon is present).
* **Task 2.3: Monte Carlo Combat Simulator**
Replace linear combat equations with a stochastic simulator running 10,000 iterations per matchup. Model coin-flip distributions (e.g., Misty, Poké Ball) and turn-by-turn energy accumulation based on the archetype's Energy Zone profile to yield a realistic Turns-to-KO (TTKO) distribution.

### Phase 3: Service Layer & AI Integration (Weeks 8-10)

* **Task 3.1: FastAPI High-Performance Framework**
Build optimized API nodes utilizing FastAPI. Establish high-speed core endpoints:
  * `GET /api/v1/meta/matrix` (returns matchup win rates split by turn order)
  * `POST /api/v1/deck/analyze` (runs Monte Carlo simulations on user decks)
  * `WS /api/v1/simulation/live` (handles real-time simulation step updates)

* **Task 3.2: Cache Strategy Deployment**
Configure a Redis caching container. Cache static card metadata and stable historical matchup matrices. Force sub-50ms query returns by eliminating unnecessary backend database calculations for static records.
* **Task 3.3: Fine-Tuned LLM Strategy Advisor**
Integrate an open-source LLM (e.g., Llama-3-8B-Instruct or Qwen-2.5-Coder) fine-tuned on PTCGP card database and rules. Serve custom strategy recommendations and explain why certain matchups are unfavorable (e.g., explaining weakness calculations and active energy requirements).
* **Task 3.4: Decoupled Discord Bot Service**
Develop a standalone Discord bot process (utilizing `discord.py`) that communicates with the FastAPI Backend. Implement key application slash commands:
  * `/report [win/loss] [my_deck] [opponent_deck] [went_first]` - Validates and inserts a raw match entry into the database.
  * `/meta` - Displays general archetype tier rankings.
  * `/matchup [archetype_a] [archetype_b]` - Outputs matchup splits (going first vs. second) and Monte Carlo TTKO estimations.
  * `/deckcheck [deck_list_string]` - Runs checks to ensure a deck list is legal (maximum 2 duplicates, minimum 1 basic Pokémon).

### Phase 4: Interface Realization & Verification (Weeks 11-12)

* **Task 4.1: Split-Screen Strategy Dashboard**
Expose a UI featuring double deck panels (Active Build vs Meta Archetype Deck) to model matchups side-by-side. Include options to toggle stats based on turn order (Going First vs Going Second).
* **Task 4.2: Asynchronous Media Rendering**
Pipe verified high-resolution graphic links directly out of the TCGdex data payload to dynamically generate modern layout matrices.
* **Task 4.3: Edge System Validation**
Code automated structural guards testing code exceptions: blocking >2 duplicate card IDs per deck profile, throwing rule exceptions if a build violates minimum basic unit configurations (must have at least 1 Basic Pokémon), and validating Energy Zone profile selections.

---

## 4. Engineering Risks & Mitigation Strategies

* **Misty/Coin-Flip Simulation Convergence:** High variance in coin flips can lead to unstable matchup win rates.
  * *Mitigation:* Ensure Monte Carlo runs use a minimum of 10,000 iterations to achieve statistical convergence, caching the output distributions.
* **LLM Hallucinations on Card Rules:** The AI advisor might suggest strategies that violate game rules.
  * *Mitigation:* Feed canonical card texts and rulesets directly in the LLM system prompt (RAG pattern) and enforce JSON response schema validations.
* **Structural String Discrepancies:** Limitless and TCGdex maintain divergent indexing keys.
  * *Mitigation:* Never allow un-sanitized strings to touch downstream queries. Implement a mandatory transformation layer standardizing card names before database entry.
* **Provider Rate Limitations:** Excessive dynamic scraping loops will cause active IP bans from tournament data sites.
  * *Mitigation:* Isolate data harvesting pipelines completely from application routines. Serialize datasets down to an intermediate persistent file layout using long, rate-limited delay loops (`time.sleep(2)`) to avoid hitting live threshold triggers.
* **Discord Match Report Spam (Fraudulent Data Ingestion):** Malicious or inaccurate submissions via `/report` could pollute aggregated stats.
  * *Mitigation:* Track the `reporter_discord_id` on the `matches` table. Apply rate-limiting at the Discord command level, restrict reporting channels to verified guilds, and implement automatic flagging of anomalous win rate trends by specific users for manual pruning.
