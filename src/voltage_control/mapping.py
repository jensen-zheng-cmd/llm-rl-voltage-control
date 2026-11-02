"""Read only inert tables from the old MAPDN pickle, without calling pickle factories."""
from __future__ import annotations

import pickletools
from pathlib import Path


def inert_pickle_tables(path: Path):
    stack, memo = [], {}
    marker = object()

    def marked_values():
        position = len(stack) - 1
        while stack[position] is not marker:
            position -= 1
        result = stack[position + 1:]
        del stack[position:]
        return result

    for opcode, arg, _ in pickletools.genops(path.read_bytes()):
        name = opcode.name
        if name == "PROTO":
            continue
        if name == "EMPTY_DICT": stack.append({})
        elif name == "EMPTY_LIST": stack.append([])
        elif name == "MARK": stack.append(marker)
        elif name in ("BINPUT", "LONG_BINPUT"): memo[arg] = stack[-1]
        elif name in ("BINGET", "LONG_BINGET"): stack.append(memo[arg])
        elif name in ("BINUNICODE", "BININT1", "BININT2", "BININT", "BINFLOAT"): stack.append(arg)
        elif name == "NEWTRUE": stack.append(True)
        elif name == "NEWFALSE": stack.append(False)
        elif name == "NONE": stack.append(None)
        elif name == "APPENDS":
            values = marked_values(); stack[-1].extend(values)
        elif name == "APPEND":
            value = stack.pop(); stack[-1].append(value)
        elif name == "SETITEMS":
            values = marked_values(); stack[-1].update(zip(values[::2], values[1::2]))
        elif name == "SETITEM":
            value, key = stack.pop(), stack.pop(); stack[-1][key] = value
        elif name == "GLOBAL": stack.append({"inert_global": arg})
        elif name == "TUPLE3":
            values = stack[-3:]; del stack[-3:]; stack.append(tuple(values))
        elif name == "TUPLE": stack.append(tuple(marked_values()))
        elif name == "REDUCE":
            args, factory = stack.pop(), stack.pop(); stack.append({"factory": factory, "args": args})
        elif name == "BUILD":
            state = stack.pop(); stack[-1]["inert_state"] = state
        elif name == "STOP": break
        else: raise ValueError(f"Unsupported old-pickle opcode {name}")
    result = {}
    for name in ("load", "sgen"):
        table = stack[-1][name]["DF"]
        result[name] = {"index": table["index"], "rows": [dict(zip(table["columns"], row)) for row in table["data"]]}
    return result


def verify_mapping(raw_directory: Path, system):
    tables = inert_pickle_tables(raw_directory / "model.p")
    load_buses = [int(row["bus"]) + 1 for row in tables["load"]["rows"]]
    pv_buses = [int(row["bus"]) + 1 for row in tables["sgen"]["rows"]]
    if tables["load"]["index"] != list(range(32)) or load_buses != list(range(2, 34)):
        raise ValueError("MAPDN load table no longer matches CSV-column-to-bus mapping")
    if tables["sgen"]["index"] != list(range(6)) or pv_buses != system["paper_devices"]["pv_buses"]:
        raise ValueError("MAPDN PV mapping disagrees with paper configuration")
    return {"load_csv_columns": list(map(str, range(32))), "load_buses_one_based": load_buses,
            "pv_csv_columns": list(map(str, range(6))), "pv_buses_one_based": pv_buses,
            "method": "inert pickle opcode interpretation; no serialized callable executed"}
