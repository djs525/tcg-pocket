import sys
import os
import random
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Any, Tuple, Optional
import numpy as np
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class SimulatedPokemon:
    """
    Explicit tracking model representing instances of an active or benched Pokémon runtime state.
    """
    def __init__(self, card_id: str, hp: int, energy_type: str, weakness: str,
                 base_damage: int, energy_cost: int, is_ex: bool):
        self.card_id = card_id
        self.max_hp = hp
        self.current_hp = hp
        self.energy_type = energy_type
        self.weakness_type = weakness
        self.base_damage = base_damage
        self.energy_cost = energy_cost
        self.is_ex = is_ex
        self.energy_attached = 0

    def reset(self):
        self.current_hp = self.max_hp
        self.energy_attached = 0


class SimulationDeckProfile:
    """
    Consolidated operational wrapper storing structural parameters and capabilities
    for a concrete Archetype list.

    Parameters
    ----------
    passive_energy_per_turn : int
        Extra energy attached each turn from a passive ability (e.g. Gardevoir = 1).
    misty_rate : float
        Probability of a bonus Water energy from Misty each turn.
        Only meaningful for Water decks; leave 0.0 for all others.
    bench_size : int
        Number of Pokémon the deck can field before losing (PTCGP max = 3).
    """
    def __init__(
        self,
        name: str,
        energy_zone_type: str,
        main_attacker: SimulatedPokemon,
        support_basic: Optional[SimulatedPokemon] = None,
        passive_energy_per_turn: int = 0,
        misty_rate: float = 0.0,
        bench_size: int = 3,
    ):
        self.name = name
        self.energy_zone_type = energy_zone_type
        self.main_attacker = main_attacker
        self.support_basic = support_basic
        self.passive_energy_per_turn = passive_energy_per_turn
        self.misty_rate = misty_rate
        self.bench_size = bench_size


class MatchStateTracker:
    """
    Comprehensive runtime data logger maintaining complete tracking layout metrics
    across a single simulated matchup iteration.
    """
    def __init__(self, profile_a: SimulationDeckProfile, profile_b: SimulationDeckProfile):
        self.deck_a = profile_a
        self.deck_b = profile_b

        self.points_a = 0
        self.points_b = 0

        # Remaining bench slots (each KO reduces by 1; hitting 0 = loss)
        self.remaining_a = profile_a.bench_size
        self.remaining_b = profile_b.bench_size

        # Instantiate localized combat instances
        self.active_a = SimulatedPokemon(
            profile_a.main_attacker.card_id,
            profile_a.main_attacker.max_hp,
            profile_a.main_attacker.energy_type,
            profile_a.main_attacker.weakness_type,
            profile_a.main_attacker.base_damage,
            profile_a.main_attacker.energy_cost,
            profile_a.main_attacker.is_ex,
        )
        self.active_b = SimulatedPokemon(
            profile_b.main_attacker.card_id,
            profile_b.main_attacker.max_hp,
            profile_b.main_attacker.energy_type,
            profile_b.main_attacker.weakness_type,
            profile_b.main_attacker.base_damage,
            profile_b.main_attacker.energy_cost,
            profile_b.main_attacker.is_ex,
        )

    def execute_turn_sequence(
        self,
        active_attacker: SimulatedPokemon,
        active_defender: SimulatedPokemon,
        deck_profile: SimulationDeckProfile,
        is_absolute_first_turn: bool,
        going_second_bonus: bool,
    ) -> bool:
        """
        Executes a single player's turn with correct PTCGP rule enforcement.

        Rules applied:
          - Player going first skips Energy Zone on turn 1 entirely.
          - Player going second draws an extra card (modelled as a free
            Supporter-probe coin flip weighted by deck's supporter density).
          - Gardevoir-style passive energy is applied every turn after the
            Energy Zone step.
          - Misty coin-flip bonus is Water-only and deck-specific.
          - Weakness is +20 flat damage (not ×2).

        Returns True if the defender was knocked out this turn.
        """
        # ── Energy attachment phase ──────────────────────────────────────────
        if is_absolute_first_turn:
            # Player 1 skips the Energy Zone generation phase on Turn 1 completely.
            pass
        else:
            # Standard Energy Zone: +1 energy of the deck's type.
            active_attacker.energy_attached += 1

            # Gardevoir-style passive ability (Psychic +1 per turn).
            active_attacker.energy_attached += deck_profile.passive_energy_per_turn

            # Misty coin-flip: Water-only, deck-specific rate.
            # Only applies when the deck actually runs Misty (misty_rate > 0).
            if deck_profile.misty_rate > 0.0 and random.random() < deck_profile.misty_rate:
                active_attacker.energy_attached += 1

        # ── Going-second bonus card (turn 1 only) ────────────────────────────
        # The player going second draws one extra card at the start of their
        # first turn. We model this as a probe against the deck's supporter
        # density. If the probe succeeds the player is treated as having a
        # Supporter available this turn (modelled as one free extra energy to
        # represent the typical tempo swing — e.g. Giovanni / Sabrina access).
        # Decks can override supporter_density on SimulationDeckProfile;
        # the default 0.30 is a reasonable average for most meta lists.
        if going_second_bonus:
            supporter_density = getattr(deck_profile, 'supporter_density', 0.30)
            if random.random() < supporter_density:
                active_attacker.energy_attached += 1

        # ── Combat execution phase ───────────────────────────────────────────
        if active_attacker.energy_attached >= active_attacker.energy_cost:
            computed_damage = active_attacker.base_damage

            # Weakness: flat +20 modifier (PTCGP rule, not ×2).
            if active_attacker.energy_type == active_defender.weakness_type:
                computed_damage += 20

            active_defender.current_hp -= computed_damage

            if active_defender.current_hp <= 0:
                return True

        return False


def run_combat_loop(
    profile_a: SimulationDeckProfile,
    profile_b: SimulationDeckProfile,
    a_goes_first: bool,
) -> Tuple[int, int]:
    """
    Executes a structured turn-by-turn game loop until a 3-point victory
    condition is met or one side runs out of Pokémon.

    Fixes applied vs. original:
      - `is_absolute_first_turn` now uses a consumed flag rather than
        re-evaluating the condition each loop tick, preventing player B
        from receiving an unearned energy on turn 1 when B goes first.
      - KO'd Pokémon decrement a bench counter; the game ends when the
        losing side has no Pokémon left (finite bench model).
      - Going-second extra-card bonus is fired exactly once, on the
        going-second player's first turn action.

    Returns
    -------
    winner : int
        1 if profile_a wins, 0 if profile_b wins.
    turns_elapsed : int
        Total turn count of the match.
    """
    match = MatchStateTracker(profile_a, profile_b)
    is_a_turn = a_goes_first

    # Track whether the player who goes FIRST has taken their first action yet.
    # This flag is consumed the moment that player acts on turn 1.
    first_player_acted = False

    # Track whether the player who goes SECOND has received their bonus card yet.
    second_player_bonus_given = False

    # 30-turn ceiling guards against stalling matchups.
    for current_turn in range(1, 31):

        if is_a_turn:
            # Is this player A's very first action and A goes first?
            is_absolute_first = a_goes_first and not first_player_acted

            # Is this player A's going-second bonus turn?
            going_second_bonus = (not a_goes_first) and (not second_player_bonus_given)

            ko_achieved = match.execute_turn_sequence(
                match.active_a,
                match.active_b,
                match.deck_a,
                is_absolute_first,
                going_second_bonus,
            )

            if is_absolute_first:
                first_player_acted = True
            if going_second_bonus:
                second_player_bonus_given = True

            if ko_achieved:
                reward = 2 if match.active_b.is_ex else 1
                match.points_a += reward
                match.remaining_b -= 1

                if match.remaining_b == 0 or match.points_a >= 3:
                    return 1, current_turn

                match.active_b.reset()

            is_a_turn = False

        else:
            # Is this player B's very first action and B goes first?
            is_absolute_first = (not a_goes_first) and not first_player_acted

            # Is this player B's going-second bonus turn?
            going_second_bonus = a_goes_first and (not second_player_bonus_given)

            ko_achieved = match.execute_turn_sequence(
                match.active_b,
                match.active_a,
                match.deck_b,
                is_absolute_first,
                going_second_bonus,
            )

            if is_absolute_first:
                first_player_acted = True
            if going_second_bonus:
                second_player_bonus_given = True

            if ko_achieved:
                reward = 2 if match.active_a.is_ex else 1
                match.points_b += reward
                match.remaining_a -= 1

                if match.remaining_a == 0 or match.points_b >= 3:
                    return 0, current_turn

                match.active_a.reset()

            is_a_turn = True

    # Draw resolution fallback: whoever has more points wins.
    return (1, 30) if match.points_a >= match.points_b else (0, 30)


def execute_full_matchup_evaluation(
    profile_a: SimulationDeckProfile,
    profile_b: SimulationDeckProfile,
    iterations: int = 10000,
) -> Dict[str, Any]:
    """
    Performs a 10,000 iteration simulation loop per turn-order combination.

    Compiles:
      - Win probabilities (going first / going second).
      - Average Turn-to-Knockout (TTKO) metrics.
      - Per-turn win probability curves for the simulated_states table
        (the real distribution, not an aliased proxy).

    The turn_win_probs_* dicts map turn_number → P(A wins | game ended
    at exactly that turn), which is what the dashboard curve should display.
    """
    a_first_wins = 0
    a_second_wins = 0
    total_turns_a_first = 0
    total_turns_a_second = 0

    # Per-turn accumulators for the real win-probability curve.
    turn_wins_a_first:  Dict[int, int] = defaultdict(int)
    turn_total_first:   Dict[int, int] = defaultdict(int)
    turn_wins_a_second: Dict[int, int] = defaultdict(int)
    turn_total_second:  Dict[int, int] = defaultdict(int)

    # ── Going-first configuration ────────────────────────────────────────────
    for _ in range(iterations):
        result, turns = run_combat_loop(profile_a, profile_b, a_goes_first=True)
        turn_total_first[turns] += 1
        if result == 1:
            a_first_wins += 1
            turn_wins_a_first[turns] += 1
        total_turns_a_first += turns

    # ── Going-second configuration ───────────────────────────────────────────
    for _ in range(iterations):
        result, turns = run_combat_loop(profile_a, profile_b, a_goes_first=False)
        turn_total_second[turns] += 1
        if result == 1:
            a_second_wins += 1
            turn_wins_a_second[turns] += 1
        total_turns_a_second += turns

    # Build real per-turn curves (P(A wins) for games that ended at turn N).
    turn_win_probs_first = {
        t: turn_wins_a_first[t] / turn_total_first[t]
        for t in sorted(turn_total_first)
    }
    turn_win_probs_second = {
        t: turn_wins_a_second[t] / turn_total_second[t]
        for t in sorted(turn_total_second)
    }

    return {
        "archetype_a_name": profile_a.name,
        "archetype_b_name": profile_b.name,
        "win_rate_going_first":  float((a_first_wins  / iterations) * 100),
        "win_rate_going_second": float((a_second_wins / iterations) * 100),
        "avg_ttko_first":        float(total_turns_a_first  / iterations),
        "avg_ttko_second":       float(total_turns_a_second / iterations),
        "turn_win_probs_first":  turn_win_probs_first,
        "turn_win_probs_second": turn_win_probs_second,
    }


if __name__ == "__main__":
    print("=== Executing Task 2.3: Monte Carlo Combat Simulator Verification Workflow ===")

    # ── Pikachu ex (Lightning) ───────────────────────────────────────────────
    # Pikachu ex: 120 HP, Lightning type, weak to Fighting, 90 dmg for 2 energy.
    # No passive acceleration, no Misty (Lightning deck).
    pika_pokemon = SimulatedPokemon(
        card_id="A1-096", hp=120, energy_type="Lightning",
        weakness="Fighting", base_damage=90, energy_cost=2, is_ex=True,
    )
    pikachu_profile = SimulationDeckProfile(
        name="Pikachu ex Core",
        energy_zone_type="Lightning",
        main_attacker=pika_pokemon,
        passive_energy_per_turn=0,
        misty_rate=0.0,
        bench_size=3,
    )

    # ── Mewtwo ex / Gardevoir (Psychic) ─────────────────────────────────────
    # Mewtwo ex: 150 HP, Psychic type, weak to Psychic, 120 dmg for 4 energy.
    # Gardevoir passive: +1 Psychic energy per turn (passive_energy_per_turn=1).
    # This brings Mewtwo's effective energy requirement down to ~2 turns.
    mewtwo_pokemon = SimulatedPokemon(
        card_id="A1-129", hp=150, energy_type="Psychic",
        weakness="Psychic", base_damage=120, energy_cost=4, is_ex=True,
    )
    mewtwo_profile = SimulationDeckProfile(
        name="Mewtwo ex / Gardevoir Core",
        energy_zone_type="Psychic",
        main_attacker=mewtwo_pokemon,
        passive_energy_per_turn=1,   # Gardevoir ability
        misty_rate=0.0,
        bench_size=3,
    )

    logging.info("Initiating 10,000 iteration simulation pairing passes...")
    metrics_summary = execute_full_matchup_evaluation(pikachu_profile, mewtwo_profile, iterations=10000)

    print("\n--- Combat Simulation Matrix Aggregation Metrics ---")
    print(f"Matchup: {metrics_summary['archetype_a_name']} VS {metrics_summary['archetype_b_name']}")
    print(f"Win Rate when Going First:  {metrics_summary['win_rate_going_first']:.2f}%")
    print(f"Win Rate when Going Second: {metrics_summary['win_rate_going_second']:.2f}%")
    print(f"Average Match Length (Going First):  {metrics_summary['avg_ttko_first']:.2f} turns")
    print(f"Average Match Length (Going Second): {metrics_summary['avg_ttko_second']:.2f} turns")

    print("\n--- Turn-by-Turn Win Probability Curve (Going First) ---")
    for turn, prob in metrics_summary["turn_win_probs_first"].items():
        print(f"  Turn {turn:2d}: {prob * 100:.1f}%")