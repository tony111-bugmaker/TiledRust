"""单位 raw name 解析（units.tsx + ini + 可选 rwmod）。"""
from __future__ import annotations

from ..rw.version import load_profile
from ..rw.units import collect_units, load_mod_units
from ..rw.mod_reader import list_mods

EXPECTED = {"builder", "tank", "commandCenter", "extractor", "builderShip"}


def run() -> dict:
    errors = []
    details = {"versions": {}}
    for version in ("1.14", "1.15"):
        profile = load_profile(version)
        units = collect_units(profile)
        names = {u.raw_name for u in units}
        missing = sorted(EXPECTED - names)
        if missing:
            errors.append(f"{version}: 缺少核心单位 {missing}")
        if not units:
            errors.append(f"{version}: 单位表为空")
        details["versions"][version] = {
            "count": len(units),
            "from_tsx": sum(1 for u in units if u.source.lower().endswith(".tsx")),
            "from_ini": sum(1 for u in units if u.source.lower().endswith(".ini")),
            "sample": sorted(names)[:8],
            "missing_core": missing,
        }
    mods = list_mods(load_profile("1.15"))
    details["mods_found"] = len(mods)
    if mods:
        try:
            mod_units = load_mod_units(mods[0])
            details["first_mod"] = {"file": mods[0].name, "units": len(mod_units)}
        except Exception as exc:  # noqa: BLE001
            details["first_mod"] = {"error": str(exc)}
    return {"name": "单位 raw name 解析", "ok": not errors, "details": details, "errors": errors}
