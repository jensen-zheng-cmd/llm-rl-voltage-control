"""v2 stage E: full rebuild of every method for one training seed (validation days).

Follows docs/v2测试规程.md. Steps run as subprocesses as soon as their inputs exist;
outputs go to <runs>/<name>_s<seed>_<tag>, each with a .log next to it.

  pretrain                        train_ppo.py, improved variant, 1500 episodes
  pool_seed -> pool_formal        Proposed pool: 6 start days, 200 days, 3 Reflexion rounds, threshold 0.90
  nr_seed -> nr_formal            No-Reflexion pool: the same with 0 rounds
  purerl_dayahead -> purerl_plans Pure-RL day-ahead PPO and its fine-tune/validation plans
  llm_validate                    validation-day plans from both pools (no no-experience arm)
  plans, nr_plans                 150 fine-tune plans from each pool
  ft_proposed, ft_nopt, ft_norefl, ft_purerl   fine-tuning (warm-up + 150 episodes; No-PT from scratch)
  val_proposed, val_norefl, val_purerl         joint validation

--phase test (after the validation report was accepted) evaluates the frozen artefacts of the
seed on the 48 test days: LLM plans from both frozen pools (one generation per day and pool),
Pure-RL test plans, and the final (episode 150) policies of every method.

Finished steps are skipped and interrupted LLM steps resume, so the same command can be rerun.
"""
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RUNS = Path(r"D:\Codex\outputs\llm-rl-voltage\runs")


@dataclass
class Step:
    name: str
    output: Path
    args: list
    deps: tuple = ()
    done_file: str = "summary.json"
    resumable: bool = False
    extra: dict = field(default_factory=dict)

    def complete(self):
        if self.name in ("pool_seed", "pool_formal", "nr_seed", "nr_formal"):
            config, days = self.output / "config.json", self.output / "summary.json"
            return (config.exists() and days.exists() and len(json.loads(days.read_text(encoding="utf-8")))
                    == len(json.loads(config.read_text(encoding="utf-8"))["dates"]))
        return (self.output / self.done_file).exists()


def steps_for(seed, tag, key_file, runs, workers):
    out = lambda name: runs / f"{name}_s{seed}_{tag}"
    py = lambda script: [str(SCRIPTS / script)]
    key = ["--key-file", str(key_file)]
    pretrain, purerl = out("ppo_v2A_improved"), out("pure_rl_v2_dayahead")
    pool, nr = out("llm_v2_formal_t090"), out("llm_v2_norefl_formal_t090")
    pool_seed, nr_seed = out("llm_v2_seed"), out("llm_v2_norefl_seed")
    llm_val, plans, nr_plans, purerl_plans = (out("llm_v2_validate"), out("ppo_finetune_v2_plans"),
                                              out("ppo_finetune_v2_norefl_plans"), out("pure_rl_v2_plans"))
    ft = {arm: out(f"ppo_finetune_v2_{arm}") for arm in ("proposed", "nopt", "norefl", "purerl")}
    seed_arg = ["--seed", str(seed)]
    steps = [
        Step("pretrain", pretrain, py("train_ppo.py") + ["--episodes", "1500", "--config", "v2", "--split-scheme",
             "A_half_month", "--variant", "improved", "--output", str(pretrain)] + seed_arg),
        Step("pool_seed", pool_seed, py("run_llm_planning_v2.py") + ["--stage", "seed", "--output", str(pool_seed)] + key,
             resumable=True),
        Step("pool_formal", pool, py("run_llm_planning_v2.py") + ["--stage", "formal", "--pool", str(pool_seed / "pool.json"),
             "--threshold", "0.9", "--output", str(pool)] + key, ("pool_seed",), resumable=True),
        Step("nr_seed", nr_seed, py("run_llm_planning_v2.py") + ["--stage", "seed", "--reflexion-rounds", "0",
             "--output", str(nr_seed)] + key, resumable=True),
        Step("nr_formal", nr, py("run_llm_planning_v2.py") + ["--stage", "formal", "--reflexion-rounds", "0", "--pool",
             str(nr_seed / "pool.json"), "--threshold", "0.9", "--output", str(nr)] + key, ("nr_seed",), resumable=True),
        Step("purerl_dayahead", purerl, py("train_pure_rl_v2.py") + ["train", "--output", str(purerl)] + seed_arg),
        Step("purerl_plans", purerl_plans, py("train_pure_rl_v2.py") + ["plans", "--model", str(purerl), "--output",
             str(purerl_plans)], ("purerl_dayahead",), done_file="validation/config.json"),
        Step("llm_validate", llm_val, py("run_llm_planning_v2.py") + ["--stage", "validate", "--skip-none",
             "--pool-arm", f"t090={pool / 'pool.json'}", "--pool-arm", f"nr={nr / 'pool.json'}",
             "--output", str(llm_val)] + key, ("pool_formal", "nr_formal"), "validation_summary.json", True),
        Step("plans", plans, py("finetune_ppo_v2.py") + ["plans", "--pool", str(pool / "pool.json"),
             "--output", str(plans)] + key, ("pool_formal",), "plans.json"),
        Step("nr_plans", nr_plans, py("finetune_ppo_v2.py") + ["plans", "--pool", str(nr / "pool.json"),
             "--output", str(nr_plans)] + key, ("nr_formal",), "plans.json"),
    ]
    for arm, source, dep, kind in (("proposed", plans, "plans", "proposed"), ("nopt", plans, "plans", "nopt"),
                                   ("norefl", nr_plans, "nr_plans", "proposed"),
                                   ("purerl", purerl_plans / "finetune_plans", "purerl_plans", "proposed")):
        steps.append(Step(f"ft_{arm}", ft[arm], py("finetune_ppo_v2.py") + ["train", "--arm", kind, "--plans",
                          str(source), "--pretrained", str(pretrain), "--output", str(ft[arm])] + seed_arg,
                          ("pretrain", dep)))
    validate = py("finetune_ppo_v2.py") + ["validate", "--pretrained", str(pretrain), "--workers", str(workers)]
    steps += [
        Step("val_proposed", out("ppo_finetune_v2_validate"), validate + ["--c2", str(llm_val), "--c2-arm", "t090",
             "--run", f"proposed={ft['proposed']}", "--run", f"nopt={ft['nopt']}",
             "--output", str(out("ppo_finetune_v2_validate"))], ("llm_validate", "ft_proposed", "ft_nopt")),
        Step("val_norefl", out("ppo_finetune_v2_norefl_validate"), validate + ["--c2", str(llm_val), "--c2-arm", "nr",
             "--run", f"norefl={ft['norefl']}", "--skip-no-llm",
             "--output", str(out("ppo_finetune_v2_norefl_validate"))], ("llm_validate", "ft_norefl")),
        Step("val_purerl", out("pure_rl_v2_validate"), validate + ["--c2", str(purerl_plans / "validation"),
             "--c2-arm", "purerl", "--run", f"purerl={ft['purerl']}", "--skip-no-llm",
             "--output", str(out("pure_rl_v2_validate"))], ("purerl_plans", "ft_purerl")),
    ]
    return steps


# Seed 42 was built before this orchestrator existed (stages D and E1), under these run names.
SEED42 = {"pretrain": "ppo_v2A_improved_seed42_20261109", "pool": "llm_v2_c2_formal_t090_20261110",
          "nr": "llm_v2_norefl_formal_t090_20261110", "purerl": "pure_rl_v2_dayahead_20261110",
          "proposed": "ppo_finetune_v2_proposed_20261110", "nopt": "ppo_finetune_v2_nopt_20261110",
          "norefl": "ppo_finetune_v2_norefl_20261110", "purerl_ft": "pure_rl_v2_finetune_20261110"}


def trained_sources(seed, build_tag, runs):
    """Frozen artefacts of one training seed: pretrained PPO, both pools, Pure-RL agent, fine-tuned policies."""
    if seed == 42:
        return {k: runs / v for k, v in SEED42.items()}
    out = lambda name: runs / f"{name}_s{seed}_{build_tag}"
    return {"pretrain": out("ppo_v2A_improved"), "pool": out("llm_v2_formal_t090"),
            "nr": out("llm_v2_norefl_formal_t090"), "purerl": out("pure_rl_v2_dayahead"),
            **{arm: out(f"ppo_finetune_v2_{arm}") for arm in ("proposed", "nopt", "norefl")},
            "purerl_ft": out("ppo_finetune_v2_purerl")}


def test_steps_for(seed, tag, build_tag, key_file, runs, workers):
    """Frozen test evaluation: plans from the frozen pools / day-ahead agent, final weights only."""
    src = trained_sources(seed, build_tag, runs)
    missing = [str(p) for p in src.values() if not (p / "summary.json").exists()]
    if missing:
        raise FileNotFoundError(f"seed {seed}: incomplete training runs {missing}")
    out = lambda name: runs / f"{name}_s{seed}_{tag}"
    py = lambda script: [str(SCRIPTS / script)]
    llm, purerl = out("llm_v2_test"), out("pure_rl_v2_testplans")
    validate = py("finetune_ppo_v2.py") + ["validate", "--split", "test", "--final-only",
                                           "--pretrained", str(src["pretrain"]), "--workers", str(workers)]
    return [
        Step("llm_test", llm, py("run_llm_planning_v2.py") + ["--stage", "validate", "--split", "test", "--skip-none",
             "--pool-arm", f"t090={src['pool'] / 'pool.json'}", "--pool-arm", f"nr={src['nr'] / 'pool.json'}",
             "--output", str(llm), "--key-file", str(key_file)], (), "validation_summary.json", True),
        Step("purerl_test_plans", purerl, py("train_pure_rl_v2.py") + ["plans", "--split", "test", "--model",
             str(src["purerl"]), "--output", str(purerl)], (), "test/config.json"),
        Step("test_proposed", out("ppo_v2_test"), validate + ["--c2", str(llm), "--c2-arm", "t090",
             "--run", f"proposed={src['proposed']}", "--run", f"nopt={src['nopt']}",
             "--output", str(out("ppo_v2_test"))], ("llm_test",)),
        Step("test_norefl", out("ppo_v2_norefl_test"), validate + ["--c2", str(llm), "--c2-arm", "nr",
             "--run", f"norefl={src['norefl']}", "--skip-no-llm",
             "--output", str(out("ppo_v2_norefl_test"))], ("llm_test",)),
        Step("test_purerl", out("pure_rl_v2_test"), validate + ["--c2", str(purerl / "test"), "--c2-arm", "purerl",
             "--run", f"purerl={src['purerl_ft']}", "--skip-no-llm",
             "--output", str(out("pure_rl_v2_test"))], ("purerl_test_plans",)),
    ]

def execute(step):
    args = list(step.args)
    if step.output.exists():
        if not step.resumable:
            raise RuntimeError(f"{step.name}: {step.output} exists but is incomplete; inspect it before rerunning")
        args.append("--resume")
    log = step.output.with_name(step.output.name + ".log")
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"\n== {time.strftime('%Y-%m-%dT%H:%M:%S')} {' '.join(args)}\n")
        handle.flush()
        code = subprocess.run([sys.executable, *args], cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                              env={**os.environ, "PYTHONUNBUFFERED": "1"}).returncode
    if code or not step.complete():
        raise RuntimeError(f"{step.name} failed (exit {code}); see {log}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--tag", required=True, help="Date tag in the run directory names, e.g. 20261111")
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--runs", type=Path, default=RUNS)
    parser.add_argument("--workers", type=int, default=4, help="Processes per validation step")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--phase", choices=("validation", "test"), default="validation",
                        help="validation: full rebuild; test: frozen evaluation on the 48 test days")
    parser.add_argument("--build-tag", help="test phase: tag of the seed's training runs (ignored for seed 42)")
    args = parser.parse_args()
    if args.phase == "test":
        if args.seed != 42 and not args.build_tag:
            parser.error("--build-tag is required for the test phase")
        steps = test_steps_for(args.seed, args.tag, args.build_tag, args.key_file, args.runs, args.workers)
    else:
        steps = steps_for(args.seed, args.tag, args.key_file, args.runs, args.workers)
    names = {s.name for s in steps}
    assert all(d in names for s in steps for d in s.deps)
    if args.dry_run:
        for s in steps:
            print(f"{s.name:16} {'done' if s.complete() else 'todo':4} after {','.join(s.deps) or '-'}\n    {' '.join(s.args)}")
        return
    done = {s.name for s in steps if s.complete()}
    failed, running = set(), {}
    with ThreadPoolExecutor(len(steps)) as pool:
        while True:
            for s in steps:
                if s.name in done | failed or s.name in running:
                    continue
                if any(d in failed for d in s.deps):
                    failed.add(s.name)
                    print(f"skip {s.name}: an input step failed", flush=True)
                elif all(d in done for d in s.deps):
                    print(f"{time.strftime('%H:%M:%S')} start {s.name}", flush=True)
                    running[s.name] = pool.submit(execute, s)
            if not running:
                break
            finished, _ = wait(running.values(), return_when=FIRST_COMPLETED)
            for name, future in list(running.items()):
                if future in finished:
                    del running[name]
                    try:
                        future.result()
                        done.add(name)
                        print(f"{time.strftime('%H:%M:%S')} done {name}", flush=True)
                    except Exception as error:
                        failed.add(name)
                        print(f"{time.strftime('%H:%M:%S')} FAILED {name}: {error}", flush=True)
    print(f"seed {args.seed}: {len(done)}/{len(steps)} steps complete" + (f"; failed {sorted(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
