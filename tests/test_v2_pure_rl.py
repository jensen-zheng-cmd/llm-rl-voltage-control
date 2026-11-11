"""Checks for the v2 Pure-RL day-ahead environment."""
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from finetune_ppo_v2 import v2_dataset
from run_llm_planning import VoltageControlEnv, parse_plan, score
from train_pure_rl_v2 import DayAheadEnv, PENALTY, to_spec


def run(env, actions):
    obs, _ = env.reset()
    total = 0.0
    for action in actions:
        obs, reward, done, _, _ = env.step(np.asarray(action))
        total += reward
    assert done and obs.shape == env.observation_space.shape
    return total, env.episodes[-1]


def test_constraints_penalty_and_common_scorer():
    dataset = v2_dataset()
    day = dataset.split_dates("train").astype(str)[10]
    env = DayAheadEnv(dataset, [day], scale=5.0)
    # Hour 0 sets the initial tap freely; then the tap alternates every hour (only four changes allowed).
    # Capacitor 1 is on in hours 2-4 and requested again from hour 8 (illegal), capacitor 3 stays on.
    actions = []
    for h in range(24):
        tap = 5 + (2 if h % 2 else 1)
        sc1 = int(2 <= h < 5 or h >= 8)
        actions.append([tap, sc1, 0, 1])
    total, episode = run(env, actions)
    plan = episode["plan"]
    assert plan["initial_tap"] == 1 and len(plan["tap_changes"]) == 4
    assert plan["sc_intervals"] == {"4": [2, 5], "9": None, "26": [0, 24]}
    # After four changes the tap rests at 1; only the odd-hour requests for 2 (hours 5-23) are illegal.
    illegal_tap, illegal_sc = 10, 24 - 8
    assert episode["illegal_actions"] == illegal_tap + illegal_sc
    metrics = score(VoltageControlEnv(dataset), day, plan)
    assert np.isclose(episode["reward"], metrics["reward"], rtol=0, atol=1e-9)
    assert np.isclose(total, episode["reward"] - PENALTY * episode["illegal_actions"], rtol=0, atol=1e-9)


def test_to_spec_round_trip():
    tap = np.r_[np.full(6, -2), np.full(10, 1), np.full(8, 0)]
    sc = np.zeros((24, 3), dtype=int)
    sc[17:22, 1] = 1
    spec = to_spec(tap, sc)
    assert spec == {"initial_tap": -2, "tap_changes": [[6, 1], [16, 0]],
                    "sc_intervals": {"4": None, "9": [17, 22], "26": None}}
    plan = parse_plan(spec)
    np.testing.assert_array_equal(plan.tap[::4], tap)
