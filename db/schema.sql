CREATE TABLE IF NOT EXISTS cards (
    card_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    supertype VARCHAR(20) NOT NULL,
    hp INTEGER,
    pokemon_type VARCHAR(20),
    weakness_type VARCHAR(20),
    retreat_cost INTEGER,
    image_url TEXT
);