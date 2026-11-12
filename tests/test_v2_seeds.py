"""Checks for the v2 stage E entry points (seed handling, record lookup, orchestration)."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from finetune_ppo_v2 import SELECTION_SEED, final_record
from run_seed_v2 import steps_for


def test_final_record_prefers_fallback_then_latest_repair(tmp_path):
    day = tmp_path / "2013-01-11"
    for name in ("t090", "t090_repair_1"):
        (day / name).mkdir(parents=True)
        (day / name / "scored.json").write_text("{}", encoding="utf-8")
    (day / "nr").mkdir()
    (day / "nr" / "scored.json").write_text("{}", encoding="utf-8")
    assert final_record(day, "t090").parent.name == "t090_repair_1"
    assert final_record(day, "nr").parent.name == "nr"
    (day / "t090_fallback").mkdir()
    (day / "t090_fallback" / "scored.json").write_text("{}", encoding="utf-8")
    assert final_record(day, "t090").parent.name == "t090_fallback"


def test_seed_steps_use_seed_and_fixed_selection(tmp_path):
    steps = {s.name: s for s in steps_for(43, "test", tmp_path / "key.txt", tmp_path, 2)}
    assert SELECTION_SEED == 42
    names = set(steps)
    assert all(d in names for s in steps.values() for d in s.deps)
    for name in ("pretrain", "purerl_dayahead", "ft_proposed", "ft_nopt", "ft_norefl", "ft_purerl"):
        args = steps[name].args
        assert args[args.index("--seed") + 1] == "43"
    assert "--skip-none" in steps["llm_validate"].args
    assert steps["ft_norefl"].deps == ("pretrain", "nr_plans")
    assert all(s.output.name.endswith("_s43_test") for s in steps.values())
    assert not any(s.complete() for s in steps.values())


def test_pool_step_completion_counts_days(tmp_path):
    step = {s.name: s for s in steps_for(44, "t", tmp_path / "k", tmp_path, 1)}["pool_seed"]
    step.output.mkdir()
    (step.output / "config.json").write_text(json.dumps({"dates": ["a", "b"]}), encoding="utf-8")
    (step.output / "summary.json").write_text(json.dumps([{"date": "a"}]), encoding="utf-8")
    assert not step.complete()
    (step.output / "summary.json").write_text(json.dumps([{"date": "a"}, {"date": "b"}]), encoding="utf-8")
    assert step.complete()
