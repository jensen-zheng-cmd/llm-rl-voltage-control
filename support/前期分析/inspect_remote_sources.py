"""Read public dataset metadata and tiny ZIP samples without downloading archives."""
from __future__ import annotations

import io
import json
import struct
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "source_checks"
OUT.mkdir(exist_ok=True)
DATASET = "https://huggingface.co/datasets/hsvgbkhgbv/Multi-Agent-Power-Distribution-Networks"


def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


class RemoteZip(io.RawIOBase):
    def __init__(self, url, size):
        self.url, self.size, self.pos = url, size, 0
        self.bytes_received = 0
        self.request_count = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else (self.pos + offset if whence == 1 else self.size + offset)
        if not 0 <= self.pos <= self.size:
            raise ValueError("seek outside archive")
        return self.pos

    def read(self, n=-1):
        n = min(self.size - self.pos, n if n >= 0 else self.size - self.pos)
        if n == 0:
            return b""
        if n > 1024 * 1024 or self.bytes_received + n > 5 * 1024 * 1024:
            raise ValueError("metadata-only download budget exceeded")
        request = urllib.request.Request(
            self.url + "?range_check=" + str(time.time_ns()),
            headers={"Range": f"bytes={self.pos}-{self.pos+n-1}", "User-Agent": "research-source-check/1.0"},
        )
        with urllib.request.urlopen(request, timeout=35) as response:
            if response.status != 206:
                raise RuntimeError(f"Server ignored Range; stopped before reading large body: {response.status}")
            content_range = response.headers.get("Content-Range", "")
            if not content_range.startswith(f"bytes {self.pos}-"):
                raise RuntimeError(f"Unexpected Content-Range: {content_range}")
            data = response.read(n + 1)
        if len(data) != n:
            raise RuntimeError(f"Short or oversized range: expected {n}, got {len(data)}")
        self.bytes_received += len(data)
        self.request_count += 1
        self.pos += len(data)
        return data


def main():
    repo = get_json("https://huggingface.co/api/datasets/hsvgbkhgbv/Multi-Agent-Power-Distribution-Networks")
    files = get_json("https://huggingface.co/api/datasets/hsvgbkhgbv/Multi-Agent-Power-Distribution-Networks/tree/main?recursive=true")
    result = {"dataset_url": DATASET, "dataset_commit": repo.get("sha"), "files": files}
    archive = next(item for item in files if item["path"] == "voltage_control_data.zip")
    remote = RemoteZip(DATASET + "/resolve/" + repo["sha"] + "/voltage_control_data.zip", archive["size"])
    try:
        with zipfile.ZipFile(remote) as zf:
            result["archive_members"] = [
                {"path": item.filename, "compressed_bytes": item.compress_size, "bytes": item.file_size}
                for item in zf.infolist()
            ]
            samples = {}
            for item in zf.infolist():
                if "case33" in item.filename and item.filename.endswith(".csv"):
                    with zf.open(item) as entry:
                        sample = entry.read(8192).decode("utf-8-sig")
                    lines = sample.splitlines()[:6]
                    samples[item.filename] = lines
                    (OUT / (Path(item.filename).stem + "_sample.txt")).write_text("\n".join(lines), encoding="utf-8")
            result["case33_samples"] = samples
    except Exception as exc:
        result["range_inspection_error"] = repr(exc)
    result["range_download_bytes"] = remote.bytes_received
    result["range_requests"] = remote.request_count
    (OUT / "mapdn_dataset_inspection.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
