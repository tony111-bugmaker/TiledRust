"""按改动文件挑选相关回归用例，取代每次无差别跑全量 700+ 例。

用法（在项目根目录执行）：

    .venv\\Scripts\\python.exe -m rwmap.tests.run_impacted \\
        rwmap/widgets/transform_icons.py rwmap/widgets/object_toolbar.py

    .venv\\Scripts\\python.exe -m rwmap.tests.run_impacted --list <路径…>
    .venv\\Scripts\\python.exe -m rwmap.tests.run_impacted --all

选测试的规则（只用标准库，不依赖 git / pytest）：

1. 改动的是测试文件 → 直接跑它。
2. 改动的是源码文件 → 取它导出的符号名 + 模块名，在测试源码里做全词匹配。
3. 再找「直接 import 了该文件」的源码模块，把它们导出的符号也纳入匹配，
   这样只改一个叶子模块（如 transform_icons.py）也不会漏掉工具栏测试。
4. ROUTES 是少数跨模块场景的人工补充（主窗口 / 主题 / 文档等）。
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
TEST_DIR = ROOT / "rwmap" / "tests"
PKG = "rwmap.tests"

# 跨模块改动的人工路由：源码路径片段 → 测试模块名（不含 .py）。
ROUTES: dict[str, tuple[str, ...]] = {
    "app/main_window.py": (
        "test_map_menu_tiled", "test_view_menu_tiled", "test_gui_smoke",
        "test_multidoc", "test_ui_round9", "test_ui_round10",
    ),
    "theme.py": ("test_theme_two_color", "test_ui_round10", "test_gui_smoke"),
    "app/panel_layout.py": ("test_panel_settings", "test_ui_round10"),
    "document/": ("test_roundtrip", "test_tmx_writer_team",
                  "test_undo_coverage", "test_multidoc"),
    "io/": ("test_roundtrip", "test_tmx_writer_team", "test_layer_codec"),
    "rw/automapping.py": ("test_automapping", "test_automapping_live",
                          "test_pen_automapping"),
}


def _exports(path: Path) -> set[str]:
    """取模块顶层导出的公开名字（函数 / 类 / 赋值常量）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    names.discard("annotations")
    return {n for n in names if not n.startswith("_")}


def _module_name(path: Path) -> str:
    return path.stem[5:] if path.stem.startswith("test_") else path.stem


def _source_files():
    return [p for p in (ROOT / "rwmap").rglob("*.py")
            if "tests" not in p.parts and "probe" not in p.parts]


def _importers(target: Path) -> set[Path]:
    """找直接 import 了 target 的源码模块（一层反向依赖）。"""
    tail = target.stem
    out: set[Path] = set()
    for src in _source_files():
        if src == target:
            continue
        try:
            text = src.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if re.search(rf"\b{tail}\b", text) and (
                f"import {tail}" in text or f" {tail} import" in text
                or f".{tail}" in text):
            out.add(src)
    return out


def select(changed: list[Path]) -> dict[str, list[str]]:
    """返回 {测试模块名: [命中原因…]}。"""
    tests = sorted(TEST_DIR.glob("test_*.py"))
    texts = {}
    for t in tests:
        try:
            texts[t.stem] = t.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            texts[t.stem] = ""
    chosen: dict[str, list[str]] = {}

    def add(name: str, why: str):
        if name in texts:
            chosen.setdefault(name, []).append(why)

    for path in changed:
        label = path.name
        if path.name.startswith("test_") and path.parent == TEST_DIR:
            add(path.stem, f"测试文件本身变更: {label}")
            continue
        rel = path.resolve().relative_to(ROOT).as_posix() if path.is_absolute() \
            else path.as_posix()
        for frag, names in ROUTES.items():
            if frag in rel:
                for n in names:
                    add(n, f"人工路由: {frag}")
        if not path.exists():
            continue
        tokens = _exports(path) | {path.stem}
        for importer in _importers(path):
            tokens |= _exports(importer)
        # 高频通用名（MainWindow 之类）在大部分测试里都出现，不能用来选测试。
        limit = max(3, int(len(tests) * 0.35))
        useful = {
            tok for tok in tokens
            if len(tok) >= 4 and sum(
                bool(re.search(rf"\b{re.escape(tok)}\b", t))
                for t in texts.values()) <= limit
        }
        for test_name, text in texts.items():
            hit = [tok for tok in useful
                   if re.search(rf"\b{re.escape(tok)}\b", text)]
            if hit:
                add(test_name, f"{label} → {', '.join(sorted(hit)[:4])}")
    return chosen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="按改动文件跑相关回归测试")
    ap.add_argument("paths", nargs="*", help="改动过的源码 / 测试文件路径")
    ap.add_argument("--list", action="store_true", help="只列出将运行的测试")
    ap.add_argument("--all", action="store_true", help="跑全量（覆盖自动选择）")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    if args.all:
        names = sorted(t.stem for t in TEST_DIR.glob("test_*.py"))
        reasons = {n: ["--all"] for n in names}
    else:
        if not args.paths:
            ap.error("请给出改动文件路径，或用 --all 跑全量")
        changed = [(ROOT / p).resolve() if Path(p).is_absolute()
                   else (ROOT / p) for p in args.paths]
        reasons = select(changed)
        names = sorted(reasons)
    if not names:
        print("没有匹配的测试文件（请检查路径或用 ROUTES 补充路由）", file=sys.stderr)
        return 1

    print(f"选中 {len(names)} 个测试模块：")
    for n in names:
        print(f"  - {n}: {'; '.join(reasons[n][:2])}")
    if args.list:
        return 0

    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for n in names:
        suite.addTests(loader.loadTestsFromName(f"{PKG}.{n}"))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
