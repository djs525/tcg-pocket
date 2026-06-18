-- Schema: Pokémon TCG Pocket Meta Analyzer
-- Engine v1.2.0 | Target: PostgreSQL 15+
-- Safe to re-run: all statements use IF NOT EXISTS / DO NOTHING patterns.

-- ─────────────────────────────────────────────────────────────
-- TABLE: cards
-- Base card reference seeded by seed_cards.py (TCGdex SDK) and
-- enriched by seed_limitless_cards.py (Limitless scraper).
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cards (
    card_id         VARCHAR(50)  PRIMARY KEY,            -- Standardized ID, e.g. 'A1-001'
    name            VARCHAR(100) NOT NULL,
    supertype       VARCHAR(20)  NOT NULL,               -- 'Pokemon' | 'Trainer'
    hp              INTEGER,                             -- NULL for Trainer cards
    pokemon_type    VARCHAR(20),                         -- 'Lightning', 'Psychic', etc. NULL for Trainers
    weakness_type   VARCHAR(20),                         -- NULL for Trainers / cards with no weakness
    retreat_cost    INTEGER,                             -- NULL for Trainers
    image_url       TEXT,
    trainer_subtype VARCHAR(30),                         -- 'Item' | 'Supporter' | 'Tool' — NULL for Pokémon
    effect_text     TEXT                                 -- Trainer effect or Pokémon flavor text
);

-- ─────────────────────────────────────────────────────────────
-- TABLE: archetypes
-- Named deck archetypes with tier ratings, empirical win rates,
-- and the energy type(s) the deck generates.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS archetypes (
    archetype_id    SERIAL       PRIMARY KEY,
    name            VARCHAR(100) UNIQUE NOT NULL,        -- e.g. 'Mewtwo ex / Gardevoir'
    tier_rating     VARCHAR(10),                         -- 'Tier 1' | 'Tier 2' | 'Rogue'
    win_rate        NUMERIC(5,2),                        -- Aggregate empirical win rate (0.00–100.00)
    energy_type_1   VARCHAR(20)  NOT NULL,               -- Primary energy generation type, e.g. 'Psychic'
    energy_type_2   VARCHAR(20)                          -- Optional secondary type for multi-energy decks
);

-- ─────────────────────────────────────────────────────────────
-- TABLE: matches
-- Raw match reports submitted via Discord /report command.
-- result is always from archetype_a's perspective.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS matches (
    match_id            SERIAL       PRIMARY KEY,
    reporter_discord_id VARCHAR(50),                     -- Discord user ID for fraud tracking
    archetype_a_id      INTEGER      NOT NULL REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    archetype_b_id      INTEGER      NOT NULL REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    went_first          BOOLEAN      NOT NULL,            -- TRUE = archetype_a went first
    result              VARCHAR(10)  NOT NULL             -- 'win' | 'loss' for archetype_a
                            CHECK (result IN ('win', 'loss')),
    reported_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────
-- TABLE: deck_matchups
-- Aggregated empirical matchup data split by turn order.
-- win_rate_a_first = win rate of archetype_a when IT goes first.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS deck_matchups (
    archetype_a_id      INTEGER      NOT NULL REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    archetype_b_id      INTEGER      NOT NULL REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    games_played_a_first  INTEGER    NOT NULL DEFAULT 0,
    win_rate_a_first      NUMERIC(5,2) NOT NULL DEFAULT 0.00,  -- Win rate of A going first
    games_played_a_second INTEGER    NOT NULL DEFAULT 0,
    win_rate_a_second     NUMERIC(5,2) NOT NULL DEFAULT 0.00,  -- Win rate of A going second
    PRIMARY KEY (archetype_a_id, archetype_b_id)
);

-- ─────────────────────────────────────────────────────────────
-- TABLE: simulated_states
-- Monte Carlo simulation snapshots (10k runs per matchup/turn).
-- win_probability_a is the P(A wins) at the given turn number.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS simulated_states (
    simulation_id       SERIAL       PRIMARY KEY,
    archetype_a_id      INTEGER      NOT NULL REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    archetype_b_id      INTEGER      NOT NULL REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    turn_number         INTEGER      NOT NULL,
    state_description   TEXT,
    win_probability_a   NUMERIC(5,2) NOT NULL,           -- P(A wins) at this turn (0.00–100.00)
    last_updated        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────
-- TABLE: archetype_tech_cards
-- Added in v1.2.0 Pivot (Task 3.4). Tracks Jaccard flex slots.
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS archetype_tech_cards (
    archetype_id    INTEGER     NOT NULL REFERENCES archetypes(archetype_id) ON DELETE CASCADE,
    card_id         VARCHAR(50) NOT NULL REFERENCES cards(card_id) ON DELETE CASCADE,
    is_core         BOOLEAN     NOT NULL,   -- TRUE = present in ≥80% of lists
    frequency       NUMERIC(5,2) NOT NULL,  -- % of scraped lists including this card
    last_updated    TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (archetype_id, card_id)
);