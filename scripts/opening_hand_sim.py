import sys
import os
import math
import argparse
import asyncio
from typing import Dict, List, Any, Tuple
import numpy as np
from scipy.stats import hypergeom

# Ensure runtime visibility into root directories
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def compute_hypergeometric_prob(deck_size: int, copies: int, hand_size: int) -> float:
    """
    Computes standard hypergeometric probability P(X >= 1) ignoring rule redraw constraints.
    Formula: 1 - (C(deck_size - copies, hand_size) / C(deck_size, hand_size))
    """
    if copies <= 0 or deck_size <= 0:
        return 0.0
    if copies > deck_size or hand_size > deck_size:
        return 0.0
    
    return float(hypergeom.sf(0, deck_size, copies, hand_size))

def simulate_deck_draws(deck_cards: List[Dict[str, Any]], target_card_id: str, iterations: int = 100000) -> Dict[str, float]:
    """
    Executes an explicit Monte Carlo replication of the PTCGP setup draw phase:
    1. A 20-card deck is validated and built.
    2. An opening hand of 5 cards is drawn.
    3. The hand is audited for at least 1 Basic Pokémon.
    4. If no Basic is found, the hand is mixed back, and a full redraw occurs.
    
    Returns:
        target_probability (float): Precise percentage of viable open hands containing target.
        avg_redraw_count (float): Mean number of structural mulligans triggered per match.
        brick_index (float): Probability that a valid hand contains only unwanted fallback basics.
    """

    # 1. Structural Deck Reconstitution
    encoded_deck = []
    total_cards = 0

    for card in deck_cards:
        copies = card.get('copies', 0)
        total_cards += copies
        # Integer categorization mapping for optimized NumPy array operations
        # 2 = Target Card & Basic, 1 = Target Card Only, -1 = Unwanted Basic (Prize liability), -2 = Standard Basic, 0 = Other
        for _ in range(copies):
            if card['card_id'] == target_card_id:
                if card.get('is_basic_pokemon', False):
                    encoded_deck.append(2)
                else:
                    encoded_deck.append(1)
            elif card.get('is_basic_pokemon', False):
                if card.get('is_brick_liability', False):
                    encoded_deck.append(-1)
                else:
                    encoded_deck.append(-2)
            else:
                encoded_deck.append(0)
    
    if total_cards!=20:
        raise ValueError(f"PTCGP rules mandate exactly 20 cards. Evaluated count: {total_cards}")
    
    deck_array = np.array(encoded_deck, dtype=np.int8)

    # 2. Performance-Optimized Vector State Allocation
    target_hits = 0
    total_redraws = 0
    brick_hands = 0
    
    # Random context local assignments decrease interpreter lookup latency
    choice = np.random.choice
    any_op = np.any

    for _ in range(iterations):
        redraws = 0
        while True:
            # Draw exactly 5 cards without replacement
            hand = choice(deck_array, size=5, replace=False)
            
            # Check for at least 1 Basic Pokémon (encoded as 2, -1, or -2)
            has_basic = any_op((hand == 2) | (hand == -1) | (hand == -2))
            
            if has_basic:
                break
            redraws += 1
            
        total_redraws += redraws
        
        # Metric tracking assignments
        has_target = any_op((hand == 2) | (hand == 1))
        if has_target:
            target_hits += 1
            
        # Brick conditions: The hand contains basics, but exclusively unwanted liability options (value -1)
        only_bad_basics = any_op(hand == -1) and not any_op((hand == 2) | (hand == -2))
        if only_bad_basics:
            brick_hands += 1
            
    return {
        "target_probability": float(target_hits / iterations),
        "avg_redraw_count": float(total_redraws / iterations),
        "brick_index": float(brick_hands / iterations)
    }

async def run_standalone_analysis_pipeline():
    """
    Demonstration and verification executor mocking a standard tier 1 archetype composition.
    """
    print("=== Executing Task 2.2: Opening Hand & Brick Analyzer Workflow ===")
    
    # Mocking standard archetype composition: Articuno ex solo variation
    mock_articuno_deck = [
        {"card_id": "A1-046", "is_basic_pokemon": True, "is_brick_liability": False, "copies": 2},  # Articuno ex
        {"card_id": "A1-160", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2}, # Misty
        {"card_id": "A1-167", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2}, # Poké Ball
        {"card_id": "A1-169", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2}, # Professor's Research
        {"card_id": "A1-162", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2}, # Sabrina
        {"card_id": "A1-171", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2}, # X Speed
        {"card_id": "P-A003", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2}, # Potion
        {"card_id": "A1-165", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2}, # Hand Scope
        {"card_id": "A1-164", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2}, # Giovanni
        {"card_id": "A1-042", "is_basic_pokemon": True, "is_brick_liability": True, "copies": 2},   # Ducklett (Unwanted liability basic)
    ]
    
    target_card = "A1-160" # Misty
    
    # 1. Evaluate quick analytical probability
    raw_hyper_prob = compute_hypergeometric_prob(deck_size=20, copies=2, hand_size=5)
    print(f"Closed-form Hypergeometric Probability P(Misty >= 1): {raw_hyper_prob * 100:.2f}%")
    
    # 2. Evaluate explicit structural Monte Carlo
    loop = asyncio.get_running_loop()
    metrics = await loop.run_in_executor(None, simulate_deck_draws, mock_articuno_deck, target_card, 150000)
    
    print("\n--- Canonical Simulation Analytics (150,000 runs) ---")
    print(f"True Opening Attachment Probability (Misty): {metrics['target_probability'] * 100:.2f}%")
    print(f"Expected Redraw Count Per Game: {metrics['avg_redraw_count']:.4f}")
    print(f"Brick Index Rating: {metrics['brick_index'] * 100:.2f}%")

if __name__ == "__main__":
    asyncio.run(run_standalone_analysis_pipeline())
