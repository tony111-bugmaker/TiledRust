"""从游戏文件生成 ``config/unit_names.json``（完整单位表）。

用法::

    .venv\\Scripts\\python.exe rwmap\\probe\\build_unit_catalog.py
    .venv\\Scripts\\python.exe rwmap\\probe\\build_unit_catalog.py --game "<游戏目录1.15>"

数据源（只读）：
- ``assets/units/**/*.ini`` 的 ``[core]``：``name`` / ``displayLocaleKey`` /
  ``overrideAndReplace`` / ``displayText``
- ``assets/units/units.tsx``：图集里出现的 raw name（补 ini 漏项）
- ``assets/translations/Strings_zh_cn.properties``（简中）/
  ``Strings.properties``（英文）里的 ``units.<key>.name``

规则：
- 有官方中文名的进表并**默认可见**；只有英文的也进表，中文名留空（界面回退英文）。
- 战役变体（``c_tank``）、形态中间态（``*_afterburn`` / ``*_underwater``）、
  模块零件（``modularSpider_*`` 各模块）、开发用桩（``NULL`` / ``missing`` /
  ``example_faction_picker``）标记 ``hidden``，界面上默认折叠。
- 虫族等没有官方翻译的单位用 ``MANUAL_CN`` 兜底。
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys_path = str(ROOT)
import sys  # noqa: E402
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from rwmap.rw.units import collect_units  # noqa: E402
from rwmap.rw.version import active_profile  # noqa: E402
from rwmap.utils.paths import CONFIG_DIR  # noqa: E402

# 文件夹 -> 界面分组
GROUP_BY_FOLDER = {
    "tanks": "陆军",
    "missile_tank": "陆军",
    "laser_tank": "陆军",
    "plasma_tank": "陆军",
    "heavy_battleship": "海军",
    "test_tank": "陆军",
    "mechs_small": "机甲",
    "mechs_large": "机甲",
    "mech_factory": "机甲",
    "mech_engineer": "机甲",
    "modular_spider": "机甲",
    "experimental_spider": "机甲",
    "bomber": "空军",
    "helicopter": "空军",
    "interceptor": "空军",
    "light_gunship": "空军",
    "aa_beam_gunship": "空军",
    "experimental_gunship": "空军",
    "missile_airship": "空军",
    "dropship": "空军",
    "experimental_dropship": "空军",
    "amphibious_jet": "空军",
    "heavy_interceptor": "空军",
    "light_sub": "海军",
    "heavy_sub": "海军",
    "heavy_aa_ship": "海军",
    "heavy_missile_ship": "海军",
    "nautilus": "海军",
    "experimental_carrier": "海军",
    "turrets": "防御",
    "nukes": "核武器",
    "bug_base": "虫族",
    "bug_melee": "虫族",
    "classic_bugs": "虫族",
    "fire_bee": "虫族",
    "mech_factory": "机甲",
    "extractor": "经济",
    "fabricator": "经济",
    "resource_deposits": "经济",
    "outpost": "建筑",
    "laboratory": "建筑",
    "miscellaneous": "其他",
    "missing_unit": "其他",
    "shared": "其他",
    "combat_engineer": "陆军",
}

# 单个单位的分类纠正（官方中文名和目录/关键字会打架时以玩法为准）
GROUP_OVERRIDE = {
    "gunShip": "空军",       # 官方名叫「武装直升机」
    "airShip": "空军",
    "robotCrab": "机甲",
    "robotCrabWater": "机甲",
}

# 官方没给中文名的单位（虫族 / 战役 / 试验品），按社区常用叫法补
MANUAL_CN = {
    "bugMelee": "虫族近战虫",
    "bugMeleeSmall": "小型近战虫",
    "bugMeleeLarge": "大型近战虫",
    "bugMeleeT31": "杀手虫 Alpha",
    "bugRanged": "虫族远程虫",
    "bugRangedT2": "虫族远程虫 T2",
    "bugSpore": "孢子虫",
    "bugFly": "飞行虫",
    "bugWasp": "黄蜂虫",
    "bugBee": "蜜蜂虫",
    "bugNest": "虫巢",
    "bugTurret": "虫族炮塔",
    "bugGenerator": "虫族发电站",
    "bugGeneratorT2": "虫族发电站 T2",
    "bugGeneratorN": "虫族发电站（中型）",
    "bugGeneratorNT2": "虫族发电站（中型）T2",
    "bugExtractor": "虫族采集器",
    "bugExtractorT2": "虫族采集器 T2",
    "bugPickup": "虫族拾取物",
    "bugBase": "虫族基地",
    "robotCrab": "侦察机器人",
    "robotCrabWater": "两栖侦察机器人",
    "laboratory": "实验室",
    "crystal_mid": "中型晶体",
    "custom_tank": "测试坦克",
    "example_faction_picker": "阵营选择器（开发用）",
    "flare_10s": "信号弹",
    "nukeLauncherC": "战役核弹发射井",
    "antiNukeLauncherC": "战役反核发射井",
    "scoutShip": "侦察舰",
    "mechFlying": "飞行机甲",
}

# 明确不参与抽签的内部名字
ALWAYS_HIDDEN = {"NULL", "missing", "modularSpider_emptySlot",
                 "modularSpider_nonEmpty", "nautilusSubmarineLand",
                 "nautilusSubmarineSurface", "mechFlyingLanded",
                 "mechFlyingTakeoff", "experimentalGunshipLanded",
                 "mechBunkerDeployed", "example_faction_picker", "flare_10s"}

# 文件夹缺失时按关键字猜分组（units.tsx 里的老名字没有 ini 目录信息）
GROUP_BY_KEYWORD = (
    ("虫族", ("bug", "ladyBug", "fireBee", "spore", "beetle")),
    ("防御", ("turret", "Turret", "Defence", "samSite", "bunker")),
    ("核武器", ("nuke", "Nuke", "silo")),
    ("经济", ("extractor", "fabricator", "crystal", "credits", "resource")),
    ("空军", ("helicopter", "gunship", "Gunship", "airShip", "Airship", "jet",
              "Jet", "bomber", "interceptor", "Interceptor", "dropship",
              "drone", "Drone")),
    ("海军", ("Ship", "ship", "Sub", "sub", "boat", "Boat", "craft", "Battleship",
              "carrier", "Carrier", "nautilus")),
    ("机甲", ("mech", "Mech", "spider", "Spider")),
    ("陆军", ("tank", "Tank", "artillery", "Artillery", "scout", "Scout")),
    ("建筑", ("factory", "Factory", "commandCenter", "commandPost", "repairbay",
              "laboratory", "outpost", "Outpost", "builder")),
)

_STATE_SUFFIX = re.compile(
    r"_(afterburn|transition|underwater|deployed|landed|takeoff|surface)$")


def read_props(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def core_fields(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    in_core = False
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = line.strip()
        if text.startswith("[") and text.endswith("]"):
            if in_core:
                break
            in_core = text.lower() == "[core]"
            continue
        if not in_core or ":" not in text or text.startswith("#"):
            continue
        key, _, value = text.partition(":")
        fields.setdefault(key.strip(), value.strip())
    return fields


def find_locale_key(raw: str, ini_key: str, keys: dict[str, str],
                    low_keys: dict[str, str]) -> str:
    for cand in (ini_key, raw):
        if cand and cand in keys:
            return cand
    if raw.lower() in low_keys:
        return low_keys[raw.lower()]
    base = re.sub(r"^c_", "", raw)
    if base in keys:
        return base
    if base.lower() in low_keys:
        return low_keys[base.lower()]
    stripped = _STATE_SUFFIX.sub("", raw)
    for cand in (stripped, re.sub(r"^c_", "", stripped)):
        if cand in keys:
            return cand
        if cand.lower() in low_keys:
            return low_keys[cand.lower()]
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default=None, help="游戏根目录，默认取当前配置档")
    ap.add_argument("--out", default=str(CONFIG_DIR / "unit_names.json"))
    args = ap.parse_args()

    profile = active_profile()
    game_root = Path(args.game) if args.game else Path(profile.game_root)
    trans = game_root / "assets" / "translations"
    zh = read_props(trans / "Strings_zh_cn.properties")
    zh_hant = read_props(trans / "Strings_zh.properties")
    en = read_props(trans / "Strings.properties")

    def unit_names(props: dict[str, str]) -> dict[str, str]:
        out = {}
        for key, value in props.items():
            if key.startswith("units.") and key.endswith(".name"):
                out[key[len("units."):-len(".name")]] = value
        return out

    zh_names, hant_names, en_names = unit_names(zh), unit_names(zh_hant), unit_names(en)
    low_keys = {k.lower(): k for k in set(zh_names) | set(en_names)}

    meta: dict[str, dict] = {}
    units_root = game_root / "assets" / "units"
    for ini in sorted(units_root.rglob("*.ini")):
        core = core_fields(ini)
        raw = core.get("name", "")
        if not raw:
            continue
        folder = ini.relative_to(units_root).parts[0]
        old = meta.get(raw)
        if old and old.get("key"):
            continue
        meta[raw] = {
            "raw": raw,
            "ini_key": core.get("displayLocaleKey", ""),
            "replace": core.get("overrideAndReplace", ""),
            "text": core.get("displayText", ""),
            "folder": folder,
            "file": str(ini.relative_to(units_root)).replace("\\", "/"),
        }

    # 图集里出现但 ini 里没有的（老版本残留名）
    for entry in collect_units(profile):
        if entry.raw_name and entry.raw_name not in meta:
            meta[entry.raw_name] = {"raw": entry.raw_name, "ini_key": "",
                                    "replace": "", "text": "", "folder": "",
                                    "file": "", "from_tsx": True}

    rows = []
    for raw, info in sorted(meta.items()):
        key = find_locale_key(raw, info.get("ini_key", ""), zh_names, low_keys)
        cn = (zh_names.get(key) or hant_names.get(key) or "").strip()
        en_name = (en_names.get(key) or info.get("text") or "").strip()
        if not cn:
            cn = MANUAL_CN.get(raw, "")
        folder = info.get("folder", "")
        if not folder:
            # 名字被 c_xxx 覆盖时，目录信息挂在变体那条上
            alt = meta.get(f"c_{raw}") or {}
            folder = alt.get("folder", "")
        group = GROUP_BY_FOLDER.get(folder, "")
        if not group:
            for name, keys in GROUP_BY_KEYWORD:
                if any(k in raw for k in keys):
                    group = name
                    break
        group = GROUP_OVERRIDE.get(raw) or group or "其他"
        hidden = (raw in ALWAYS_HIDDEN
                  or bool(_STATE_SUFFIX.search(raw))
                  or raw.startswith("modularSpider_")
                  or raw.startswith("c_"))
        # 同名的战役变体若已有本体，继续隐藏；没有本体则保留
        if raw.startswith("c_"):
            base = raw[2:]
            if base in meta:
                hidden = True
        rows.append({
            "raw": raw,
            "cn": cn,
            "en": en_name,
            "key": key,
            "group": group,
            "folder": folder,
            "hidden": hidden,
            "replace": info.get("replace", ""),
        })

    payload = {
        "source": f"Rusted Warfare · {game_root}",
        "count": len(rows),
        "units": rows,
    }
    out = Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    visible = [r for r in rows if not r["hidden"]]
    print(f"总计 {len(rows)}，可见 {len(visible)}，隐藏 {len(rows) - len(visible)}")
    print("无中文名:", [r["raw"] for r in visible if not r["cn"]] or "无")
    print("分组:", dict(Counter(r["group"] for r in visible)))
    print("写出", out)


if __name__ == "__main__":
    main()
