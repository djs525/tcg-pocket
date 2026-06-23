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

# Maximum redraws per opening hand attempt.
# PTCGP has no formal cap, but 10 consecutive all-trainer draws is
# astronomically unlikely in any legal 20-card deck. Without this guard
# a pathological deck config (e.g. 0 basics) would spin forever.
MAX_MULLIGAN_ATTEMPTS = 10


def compute_hypergeometric_prob(deck_size: int, copies: int, hand_size: int) -> float:
    """
    Computes standard hypergeometric probability P(X >= 1).

    Note: this is a closed-form approximation. It does not account for
    the PTCGP redraw rule (guaranteed Basic in opening hand), so the
    returned value will be slightly lower than the true opening-hand
    probability produced by simulate_deck_draws.

    Formula: 1 - (C(deck_size - copies, hand_size) / C(deck_size, hand_size))
    """
    if copies <= 0 or deck_size <= 0:
        return 0.0
    if copies > deck_size or hand_size > deck_size:
        return 0.0

    return float(hypergeom.sf(0, deck_size, copies, hand_size))


def simulate_deck_draws(
    deck_cards: List[Dict[str, Any]],
    target_card_id: str,
    iterations: int = 100000,
) -> Dict[str, float]:
    """
    Executes an explicit Monte Carlo replication of the PTCGP setup draw phase:

    1. A 20-card deck is validated and built.
    2. An opening hand of 5 cards is drawn without replacement.
    3. The hand is audited for at least 1 Basic Pokémon.
    4. If no Basic is found the hand is shuffled back and a full redraw
       occurs (up to MAX_MULLIGAN_ATTEMPTS times).
    5. After MAX_MULLIGAN_ATTEMPTS the iteration is skipped with a warning;
       this should never occur in a legal deck and signals a configuration bug.

    Card encoding (NumPy int8):
        2  = Target card AND a Basic Pokémon
        1  = Target card only (non-Basic, e.g. Misty)
       -1  = Unwanted Basic (brick liability, e.g. Ducklett in a non-Water deck)
       -2  = Wanted non-target Basic (filler Basic that satisfies the redraw rule)
        0  = Non-Basic, non-target (Trainers, etc.)

    Returns
    -------
    target_probability : float
        Fraction of valid opening hands containing the target card.
    avg_redraw_count : float
        Mean number of structural mulligans triggered per game.
    brick_index : float
        Probability that a valid hand contains only unwanted liability basics
        (encoded -1) with no desirable Basics or the target card.
    """

    # ── Deck reconstitution ──────────────────────────────────────────────────
    encoded_deck = []
    total_cards = 0

    for card in deck_cards:
        copies = card.get('copies', 0)
        total_cards += copies
        for _ in range(copies):
            if card['card_id'] == target_card_id:
                encoded_deck.append(2 if card.get('is_basic_pokemon', False) else 1)
            elif card.get('is_basic_pokemon', False):
                encoded_deck.append(-1 if card.get('is_brick_liability', False) else -2)
            else:
                encoded_deck.append(0)

    if total_cards != 20:
        raise ValueError(
            f"PTCGP rules mandate exactly 20 cards. Evaluated count: {total_cards}"
        )

    basic_count = sum(1 for v in encoded_deck if v in (2, -1, -2))
    if basic_count == 0:
        raise ValueError(
            "Deck contains no Basic Pokémon. This deck is illegal under PTCGP rules "
            "and the opening-hand redraw loop would never terminate."
        )

    deck_array = np.array(encoded_deck, dtype=np.int8)

    # ── Simulation loop ──────────────────────────────────────────────────────
    target_hits  = 0
    total_redraws = 0
    brick_hands  = 0
    skipped      = 0

    choice  = np.random.choice
    any_op  = np.any

    for _ in range(iterations):
        redraws = 0
        hand = None

        while redraws <= MAX_MULLIGAN_ATTEMPTS:
            candidate = choice(deck_array, size=5, replace=False)
            has_basic = any_op((candidate == 2) | (candidate == -1) | (candidate == -2))
            if has_basic:
                hand = candidate
                break
            redraws += 1

        if hand is None:
            # Should be unreachable for any legal deck; log and skip.
            skipped += 1
            continue

        total_redraws += redraws

        has_target = any_op((hand == 2) | (hand == 1))
        if has_target:
            target_hits += 1

        # Brick: the hand has basics, but exclusively unwanted liability options.
        only_bad_basics = any_op(hand == -1) and not any_op((hand == 2) | (hand == -2))
        if only_bad_basics:
            brick_hands += 1

    if skipped > 0:
        import warnings
        warnings.warn(
            f"{skipped} iterations were skipped because no valid Basic was drawn within "
            f"{MAX_MULLIGAN_ATTEMPTS} attempts. Check deck composition.",
            RuntimeWarning,
        )

    valid_iterations = iterations - skipped
    if valid_iterations == 0:
        raise RuntimeError("All iterations were skipped. Deck configuration is invalid.")

    return {
        "target_probability": float(target_hits  / valid_iterations),
        "avg_redraw_count":   float(total_redraws / valid_iterations),
        "brick_index":        float(brick_hands   / valid_iterations),
    }


async def run_standalone_analysis_pipeline():
    """
    Demonstration and verification executor mocking a standard tier 1 archetype composition.
    """
    print("=== Executing Task 2.2: Opening Hand & Brick Analyzer Workflow ===")

    # Mocking standard archetype composition: Articuno ex solo variation
    mock_articuno_deck = [
        {"card_id": "A1-046", "is_basic_pokemon": True,  "is_brick_liability": False, "copies": 2},  # Articuno ex
        {"card_id": "A1-160", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2},  # Misty
        {"card_id": "A1-167", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2},  # Poké Ball
        {"card_id": "A1-169", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2},  # Professor's Research
        {"card_id": "A1-162", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2},  # Sabrina
        {"card_id": "A1-171", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2},  # X Speed
        {"card_id": "P-A003", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2},  # Potion
        {"card_id": "A1-165", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2},  # Hand Scope
        {"card_id": "A1-164", "is_basic_pokemon": False, "is_brick_liability": False, "copies": 2},  # Giovanni
        {"card_id": "A1-042", "is_basic_pokemon": True,  "is_brick_liability": True,  "copies": 2},  # Ducklett (unwanted basic)
    ]

    target_card = "A1-160"  # Misty

    # 1. Closed-form hypergeometric baseline (ignores redraw rule — will read slightly lower).
    raw_hyper_prob = compute_hypergeometric_prob(deck_size=20, copies=2, hand_size=5)
    print(f"Closed-form Hypergeometric Probability P(Misty >= 1): {raw_hyper_prob * 100:.2f}%")

    # 2. Full structural Monte Carlo (respects PTCGP redraw and brick rules).
    loop = asyncio.get_running_loop()
    metrics = await loop.run_in_executor(
        None, simulate_deck_draws, mock_articuno_deck, target_card, 150000
    )

    print("\n--- Canonical Simulation Analytics (150,000 runs) ---")
    print(f"True Opening Attachment Probability (Misty): {metrics['target_probability'] * 100:.2f}%")
    print(f"Expected Redraw Count Per Game:              {metrics['avg_redraw_count']:.4f}")
    print(f"Brick Index Rating:                          {metrics['brick_index'] * 100:.2f}%")


if __name__ == "__main__":
    asyncio.run(run_standalone_analysis_pipeline())