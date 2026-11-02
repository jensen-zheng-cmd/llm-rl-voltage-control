"""Extract only MAPDN case33 members using HTTP ranges; verify ZIP CRC and SHA256."""
from __future__ import annotations

import collections
import csv
import datetime as dt
import hashlib
import io
import json
import struct
import time
import urllib.request
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METADATA = json.loads((ROOT / "support/evidence/mapdn_dataset_inspection.json").read_text(encoding="utf-8"))
REVISION = METADATA["dataset_commit"]
URL = "https://huggingface.co/datasets/hsvgbkhgbv/Multi-Agent-Power-Distribution-Networks/resolve/" + REVISION + "/voltage_control_data.zip"
ARCHIVE_SIZE = next(f["size"] for f in METADATA["files"] if f["path"] == "voltage_control_data.zip")
RAW = ROOT / "data/raw/mapdn_case33"
WORK = Path("D:/Codex/work/llm-rl-case33-download")


def get_range(start, length):
    if length == 0:
        return b""
    for attempt in range(4):
        try:
            request = urllib.request.Request(
                URL + "?part=" + str(start),
                headers={"Range": f"bytes={start}-{start+length-1}", "User-Agent": "case33-research-download/1.0"},
            )
            with urllib.request.urlopen(request, timeout=45) as response:
                expected = f"bytes {start}-{start+length-1}/"
                if response.status != 206 or not response.headers.get("Content-Range", "").startswith(expected):
                    raise RuntimeError("Server did not honor the exact byte range")
                data = response.read(length + 1)
            if len(data) != length:
                raise RuntimeError("Incomplete byte range")
            return data
        except Exception as exc:
            if attempt == 3:
                raise RuntimeError(f"Range download failed at {start} ({type(exc).__name__})") from None
            print(f"Retry range at {start}, attempt {attempt+2}", flush=True)
            time.sleep(attempt + 1)


class DirectoryReader(io.RawIOBase):
    def __init__(self):
        self.pos = 0

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else (self.pos+offset if whence == 1 else ARCHIVE_SIZE+offset)
        return self.pos

    def read(self, n=-1):
        n = min(n if n >= 0 else ARCHIVE_SIZE-self.pos, ARCHIVE_SIZE-self.pos)
        if n > 1024*1024:
            raise RuntimeError("Unexpectedly large ZIP directory read")
        content = get_range(self.pos, n)
        self.pos += len(content)
        return content


def digest_file(path):
    sha, crc, size = hashlib.sha256(), 0, 0
    with path.open("rb") as stream:
        while chunk := stream.read(4*1024*1024):
            sha.update(chunk)
            crc = zlib.crc32(chunk, crc)
            size += len(chunk)
    return {"bytes": size, "sha256": sha.hexdigest(), "crc32": crc & 0xffffffff}


def extract_member(info):
    name = Path(info.filename).name
    destination = RAW / name
    if destination.exists():
        digest = digest_file(destination)
        if digest["bytes"] != info.file_size or digest["crc32"] != info.CRC:
            raise RuntimeError(f"Existing file has different contents: {name}")
        print(f"Verified existing {name}", flush=True)
        return {"path": str(destination.relative_to(ROOT)), "archive_member": info.filename, **digest, "zip_crc_verified": True}
    header = get_range(info.header_offset, 30)
    if header[:4] != b"PK\x03\x04":
        raise RuntimeError("Invalid ZIP local header")
    filename_length, extra_length = struct.unpack_from("<HH", header, 26)
    offset = info.header_offset + 30 + filename_length + extra_length
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise RuntimeError("Unsupported ZIP compression")
    inflater = zlib.decompressobj(-15) if info.compress_type == zipfile.ZIP_DEFLATED else None
    temporary = WORK / (name + ".partial")
    sha, crc, size, received = hashlib.sha256(), 0, 0, 0
    print(f"Downloading {name}: {info.compress_size/1e6:.2f} MB compressed", flush=True)
    with temporary.open("wb") as stream:
        while received < info.compress_size:
            block_size = min(8*1024*1024, info.compress_size-received)
            compressed = get_range(offset+received, block_size)
            content = inflater.decompress(compressed) if inflater else compressed
            stream.write(content)
            sha.update(content)
            crc = zlib.crc32(content, crc)
            size += len(content)
            received += block_size
            print(f"{name}: {received/info.compress_size:.0%}", flush=True)
        if inflater:
            content = inflater.flush()
            stream.write(content)
            sha.update(content)
            crc = zlib.crc32(content, crc)
            size += len(content)
            if not inflater.eof:
                raise RuntimeError("Truncated compressed member")
    if size != info.file_size or (crc & 0xffffffff) != info.CRC:
        raise RuntimeError(f"Integrity verification failed: {name}")
    if destination.exists():
        raise RuntimeError("Destination appeared during download")
    temporary.replace(destination)
    print(f"CRC verified: {name}", flush=True)
    return {"path": str(destination.relative_to(ROOT)), "archive_member": info.filename,
            "bytes": size, "sha256": sha.hexdigest(), "crc32": crc & 0xffffffff, "zip_crc_verified": True}


def inspect_csv(path):
    row_count, previous, first, last = 0, None, None, None
    intervals, day_counts = collections.Counter(), collections.Counter()
    bad_columns, nonfinite_or_empty = 0, 0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        for row in reader:
            if len(row) != len(header):
                bad_columns += 1
            instant = dt.datetime.fromisoformat(row[0])
            if first is None:
                first = instant
            if previous is not None:
                intervals[int((instant-previous).total_seconds())] += 1
            previous, last = instant, instant
            day_counts[instant.date().isoformat()] += 1
            nonfinite_or_empty += sum(value.strip().lower() in {"", "nan", "inf", "-inf", "+inf", "none"} for value in row[1:])
            row_count += 1
    return {"path": str(path.relative_to(ROOT)), "signals": len(header)-1, "rows": row_count,
            "first_timestamp": first.isoformat(sep=" "), "last_timestamp": last.isoformat(sep=" "),
            "interval_seconds_histogram": dict(intervals), "date_count": len(day_counts),
            "rows_per_calendar_date_histogram": dict(collections.Counter(day_counts.values())),
            "first_calendar_date_rows": day_counts[first.date().isoformat()],
            "last_calendar_date_rows": day_counts[last.date().isoformat()],
            "column_count_errors": bad_columns, "empty_or_nonfinite_tokens": nonfinite_or_empty,
            "scope": "full timestamp and table-shape scan; not a full physical-value or power-flow audit"}


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    manifest = {"dataset_revision": REVISION, "archive_url": URL, "files": [], "csv_checks": []}
    report_path = ROOT / "support/evidence/case33_download_manifest.json"
    with zipfile.ZipFile(DirectoryReader()) as archive:
        members = [info for info in archive.infolist() if "/case33_3min_final/" in info.filename and not info.is_dir()]
    if {Path(info.filename).name for info in members} != {"load_active.csv", "load_reactive.csv", "pv_active.csv", "model.p"}:
        raise RuntimeError("Unexpected case33 member list")
    for info in members:
        manifest["files"].append(extract_member(info))
        report_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for info in members:
        path = RAW / Path(info.filename).name
        if path.suffix == ".csv":
            print(f"Scanning timestamps and table shape: {path.name}", flush=True)
            manifest["csv_checks"].append(inspect_csv(path))
            report_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["complete"] = True
    manifest["power_flow_executed"] = False
    report_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"complete": True, "csv_checks": manifest["csv_checks"]}, ensure_ascii=True, indent=2), flush=True)


if __name__ == "__main__":
    main()
