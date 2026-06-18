import sys
import os
import random
import logging
from typing import Dict, List, Any, Tuple, Optional
import numpy as np
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class SimulatedPokemon:
    """
    Explicit tracking model representing instances of an active or benched Pokémon runtime state.
    """
    def __init__(self, card_id: str, hp: int, energy_type: str, weakness: str, base_damage: int, energy_cost: int, is_ex: bool):
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
    Consolidated operational wrapper storing structural parameters and capabilities for a concrete Archetype list.
    """
    def __init__(self, name: str, energy_zone_type: str, main_attacker: SimulatedPokemon, support_basic: Optional[SimulatedPokemon] = None):
        self.name = name
        self.energy_zone_type = energy_zone_type
        self.main_attacker = main_attacker
        self.support_basic = support_basic

class MatchStateTracker:
    """
    Comprehensive runtime data logger maintaining complete tracking layout metrics across a single simulated matchup iteration.
    """
    def __init__(self, profile_a: SimulationDeckProfile, profile_b: SimulationDeckProfile):
        self.deck_a = profile_a
        self.deck_b = profile_b
        
        # Interactive state properties
        self.points_a = 0
        self.points_b = 0
        
        # Instantiate localized combat instances
        self.active_a = SimulatedPokemon(
            profile_a.main_attacker.card_id, profile_a.main_attacker.max_hp, profile_a.main_attacker.energy_type,
            profile_a.main_attacker.weakness_type, profile_a.main_attacker.base_damage, profile_a.main_attacker.energy_cost, profile_a.main_attacker.is_ex
        )
        self.active_b = SimulatedPokemon(
            profile_b.main_attacker.card_id, profile_b.main_attacker.max_hp, profile_b.main_attacker.energy_type,
            profile_b.main_attacker.weakness_type, profile_b.main_attacker.base_damage, profile_b.main_attacker.energy_cost, profile_b.main_attacker.is_ex
        )
    
    def execute_turn_sequence(self, active_attacker: SimulatedPokemon, active_defender: SimulatedPokemon, energy_zone_type: str) -> bool:
        """
        Executes granular turn actions:
        1. Increments energy generation from the deterministic Energy Zone.
        2. Evaluates coin-flip modifications dynamically.
        3. Quantifies damage calculation against the flat +20 weakness rule modification matrix.
        
        Returns: True if a knockout point assignment occurs.
        """
        # 2.3a Energy Zone Attachment
        active_attacker.energy_attached += 1

        # 2.3c Coin Flip Modeling Example (E.g., 20% Chance for extra Energy acceleration via Misty/Items)
        if random.random() < 0.2:
            active_attacker.energy_attached += 1
        
        # Combat Execution Sequence
        if active_attacker.energy_attached >= active_attacker.energy_cost:
            computed_damage = active_attacker.base_damage
        
            # 2.3b Weakness Rules: Flat +20 damage modifier applied if type matches defender weakness
            if active_attacker.energy_type == active_defender.weakness_type:
                computed_damage += 20

            active_defender.current_hp -= computed_damage

            #Check for Knockout
            if active_defender.current_hp <= 0:
                return True
        return False
    
def run_combat_loop(profile_a: SimulationDeckProfile, profile_b: SimulationDeckProfile, a_goes_first: bool) -> Tuple[int, int]:
    """
    Executes a structured turn-by-turn game loop until a 3-point victory condition is verified.
    
    Returns:
        winner (int): 1 if profile_a secures victory, 0 if profile_b wins.
        turns_elapsed (int): Total sequence length of the match.
    """
    match = MatchStateTracker(profile_a, profile_b)
    is_a_turn = a_goes_first

    # 30-Turn ceiling guard avoids infinite loops from stalling matchups
    for current_turn in range(1, 31):
        if is_a_turn:
            ko_achieved = match.execute_turn_sequence(match.active_a, match.active_b, match.deck_a.energy_zone_type)
            if ko_achieved:
                reward = 2 if match.active_b.is_ex else 1
                match.points_a += reward
                match.active_b.reset()

            if match.points_a >=3:
                return 1, current_turn
            is_a_turn = False
        else:
            ko_achieved = match.execute_turn_sequence(match.active_b, match.active_a, match.deck_b.energy_zone_type)
            if ko_achieved:
                reward = 2 if match.active_a.is_ex else 1
                match.points_b += reward
                match.active_a.reset()
            
            if match.points_b >= 3:
                return 0, current_turn
            is_a_turn = True

    # Draw Resolution Fallback Guard
    return (1, 30) if match.points_a >= match.points_b else (0, 30)

def execute_full_matchup_evaluation(profile_a: SimulationDeckProfile, profile_b: SimulationDeckProfile, iterations: int = 10000) -> Dict[str, Any]:
    """
    Performs a 10,000 iteration simulation loop per turn order combination.
    Compiles precise win probabilities and average Turn-to-Knockout (TTKO) metrics.
    """
    a_first_wins = 0
    a_second_wins = 0
    total_turns_a_first = 0
    total_turns_a_second = 0

    # Run Going-First Configuration
    for _ in range(iterations):
        result, turns = run_combat_loop(profile_a, profile_b, a_goes_first=True)
        if result == 1:
            a_first_wins += 1
        total_turns_a_first += turns

    # Run Going-Second Configuration
    for _ in range(iterations):
        result, turns = run_combat_loop(profile_a, profile_b, a_goes_first=False)
        if result == 1:
            a_second_wins += 1
        total_turns_a_second += turns
    
    return {
        "archetype_a_name": profile_a.name,
        "archetype_b_name": profile_b.name,
        "win_rate_going_first": float((a_first_wins / iterations) * 100),
        "win_rate_going_second": float((a_second_wins / iterations) * 100),
        "avg_ttko_first": float(total_turns_a_first / iterations),
        "avg_ttko_second": float(total_turns_a_second / iterations)
    }

if __name__ == "__main__":
    print("=== Executing Task 2.3: Monte Carlo Combat Simulator Verification Workflow ===")
    
    # Build structural profile data for Pikachu ex Meta Deck
    pika_pokemon = SimulatedPokemon(card_id="A1-096", hp=120, energy_type="Lightning", weakness="Fighting", base_damage=90, energy_cost=2, is_ex=True)
    pikachu_profile = SimulationDeckProfile(name="Pikachu ex Core", energy_zone_type="Lightning", main_attacker=pika_pokemon)
    
    # Build structural profile data for Mewtwo ex Meta Deck
    mewtwo_pokemon = SimulatedPokemon(card_id="A1-129", hp=150, energy_type="Psychic", weakness="Psychic", base_damage=120, energy_cost=4, is_ex=True)
    mewtwo_profile = SimulationDeckProfile(name="Mewtwo ex / Gardevoir Core", energy_zone_type="Psychic", main_attacker=mewtwo_pokemon)
    
    # Run combat engine matrix evaluation node
    logging.info("Initiating 10,000 iteration simulation pairing passes...")
    metrics_summary = execute_full_matchup_evaluation(pikachu_profile, mewtwo_profile, iterations=10000)
    
    print("\n--- Combat Simulation Matrix Aggregation Metrics ---")
    print(f"Matchup: {metrics_summary['archetype_a_name']} VS {metrics_summary['archetype_b_name']}")
    print(f"Win Rate when Going First: {metrics_summary['win_rate_going_first']:.2f}%")
    print(f"Win Rate when Going Second: {metrics_summary['win_rate_going_second']:.2f}%")
    print(f"Average Match Length (Going First): {metrics_summary['avg_ttko_first']:.2f} turns")
    print(f"Average Match Length (Going Second): {metrics_summary['avg_ttko_second']:.2f} turns")
                        