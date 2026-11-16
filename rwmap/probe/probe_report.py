"""探针汇总。命令行：python -m rwmap.probe.probe_report"""
from __future__ import annotations

import json
import sys
import time

from ..utils.log import report_path
from .probe_batch import run as run_batch
from .probe_compression import run as run_compression
from .probe_resources import run as run_resources
from .probe_roundtrip import run as run_roundtrip
from .probe_thumbnail import run as run_thumbnail
from .probe_units import run as run_units

PROBES = [
    ("batch", run_batch),
    ("compression", run_compression),
    ("roundtrip", run_roundtrip),
    ("resources", run_resources),
    ("units", run_units),
    ("thumbnail", run_thumbnail),
]


def run_all(selected: list[str] | None = None) -> dict:
    results = []
    started = time.time()
    for key, func in PROBES:
        if selected and key not in selected:
            continue
        try:
            result = func()
        except Exception as exc:  # noqa: BLE001
            result = {"name": key, "ok": False, "details": {}, "errors": [repr(exc)]}
        result["key"] = key
        results.append(result)
    return {
        "ok": all(r["ok"] for r in results),
        "elapsed": round(time.time() - started, 2),
        "results": results,
    }


def format_report(report: dict) -> str:
    lines = [f"探针汇总  {'全部通过' if report['ok'] else '存在失败'}  用时 {report['elapsed']}s", ""]
    for item in report["results"]:
        mark = "PASS" if item["ok"] else "FAIL"
        lines.append(f"[{mark}] {item['name']}")
        for err in item.get("errors", [])[:5]:
            lines.append(f"       - {err}")
    return "\n".join(lines)


def main() -> int:
    selected = sys.argv[1:] or None
    report = run_all(selected)
    text = format_report(report)
    print(text)
    path = report_path("probe_report.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: {path}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
