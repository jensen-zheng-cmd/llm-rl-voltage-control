from __future__ import annotations

import hashlib
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_configs():
    runtime = read_json(PROJECT_ROOT / "configs/environment_v1.json")
    system = read_json(PROJECT_ROOT / runtime["system_config"])
    return runtime, system


def processed_directory(runtime) -> Path:
    return Path(runtime["output_root"]) / runtime["processed_subdirectory"]
