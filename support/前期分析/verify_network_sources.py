"""Static checks on downloaded MATPOWER cases; does not execute MATLAB or power flow."""
import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "source_checks"


def matrix(text, name):
    block = re.search(r"mpc\." + name + r"\s*=\s*\[(.*?)\];", text, re.S).group(1)
    rows = []
    for line in block.splitlines():
        line = line.split("%", 1)[0].strip().rstrip(";")
        if line:
            rows.append([float(token) for token in line.split()])
    return rows


def inspect_case(name):
    path = OUT / (name + ".m")
    text = path.read_text(encoding="utf-8")
    buses, branches = matrix(text, "bus"), matrix(text, "branch")
    nodes = {int(row[0]) for row in buses}
    active = [(int(row[0]), int(row[1])) for row in branches if int(row[10]) == 1]
    reached = {min(nodes)}
    while True:
        expanded = reached | {v for u, v in active if u in reached} | {u for u, v in active if v in reached}
        if expanded == reached:
            break
        reached = expanded
    summary = {
        "source_url": f"https://github.com/MATPOWER/matpower/blob/master/data/{name}.m",
        "snapshot_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "buses": len(buses), "branches_total": len(branches), "branches_closed": len(active),
        "connected": reached == nodes, "radial": reached == nodes and len(active) == len(nodes)-1,
        "base_mva": float(re.search(r"mpc.baseMVA\s*=\s*([\d.]+)", text).group(1)),
        "base_kv": sorted({row[9] for row in buses}),
        "reference_buses": [int(row[0]) for row in buses if int(row[1]) == 3],
        "power_flow_executed": False,
    }
    if name == "case33bw":
        summary["base_load_mw"] = sum(row[2] for row in buses)/1000
        summary["base_load_mvar"] = sum(row[3] for row in buses)/1000
    return summary


def main():
    result = {name: inspect_case(name) for name in ("case33bw", "case141")}
    gen_text = (OUT / "mapdn_bus33bw_generator.py").read_text(encoding="utf-8")
    zero_based = ast.literal_eval(re.search(r"^PV_bus_index\s*=\s*(\[[^\n]+\])", gen_text, re.M).group(1))
    pv_buses = [node+1 for node in zero_based]
    result["case33_pv_mapping"] = {"source_zero_based": zero_based, "paper_one_based": pv_buses,
                                    "matches_paper_figure": pv_buses == [13,18,22,25,29,33]}
    result["candidate_regions_one_based"] = {
        "region1": [2,3,4,5,19,20,21,22,23,24,25],
        "region2": list(range(8,19)),
        "region3": [6,7]+list(range(26,34)),
    }
    region_nodes = [node for group in result["candidate_regions_one_based"].values() for node in group]
    result["regions_cover_each_nonreference_bus_once"] = sorted(region_nodes) == list(range(2,34))
    result["region_map_status"] = "transcribed from target-paper supplementary topology; not from MAPDN zone labels"
    (OUT / "network_static_checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
