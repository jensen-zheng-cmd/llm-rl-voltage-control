"""Checks for the v2 stage D fine-tuning entry point."""
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from finetune_ppo_v2 import mechanical, retrieve_excluding, select_days, v2_dataset
from run_llm_planning import NEUTRAL


def test_selection_is_stratified_training_days():
    dataset = v2_dataset()
    selection = select_days(dataset)
    train = set(dataset.split_dates("train").astype(str))
    dates = [d for d, _ in selection]
    assert len(selection) == 150 and len(set(dates)) == 150 and set(dates) <= train
    groups = [g for _, g in selection]
    assert groups.count("ordinary") == 90 and groups.count("high_load") == 30 and groups.count("high_pv") == 30
    assert select_days(dataset) == selection


def test_mechanical_counts():
    assert mechanical(NEUTRAL) == {"oltc_changes": 0, "sc_switching": 0}
    spec = {"initial_tap": 2, "tap_changes": [[6, 3], [18, 1]],
            "sc_intervals": {"4": [17, 22], "9": [0, 24], "26": None}}
    assert mechanical(spec) == {"oltc_changes": 2, "sc_switching": 3}


def test_retrieval_excludes_same_date():
    curve = list(np.linspace(1, 2, 24))
    pool = [{"forecast": {"date": "2013-01-02", "net_load_mw": curve}},
            {"forecast": {"date": "2013-01-03", "net_load_mw": [2 * x for x in curve]}}]
    reference, sim, excluded = retrieve_excluding(pool, {"date": "2013-01-02", "net_load_mw": curve})
    assert reference["forecast"]["date"] == "2013-01-03" and excluded and sim < 1
    reference, _, excluded = retrieve_excluding(pool, {"date": "2013-01-09", "net_load_mw": curve})
    assert reference["forecast"]["date"] == "2013-01-02" and not excluded
