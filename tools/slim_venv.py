"""Shrink .venv by removing what this project never imports.

The virtual environment is ~31 MB right after `setup.bat`, but only ~7 MB of
that is actually reachable from this code.  Two things dominate the waste:

  pip                          ~13 MB  needed only to install, once
  Pillow AVIF/WebP/LittleCMS   ~8 MB   we only draw a 32x32 bitmap

Everything removed here is recoverable:

  pip      -> .venv\\Scripts\\python.exe -m ensurepip
  Pillow   -> .venv\\Scripts\\python.exe -m ensurepip && \\
              .venv\\Scripts\\python.exe -m pip install --force-reinstall pillow

Safe to re-run; it only reports when nothing is left to do.

Usage:
    python tools/slim_venv.py           # trim
    python tools/slim_venv.py --check   # report only, change nothing
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
SITE = VENV / "Lib" / "site-packages"

# pip itself: the package tree, its metadata, and the launcher shims.
PIP_TARGETS = [
    SITE / "pip",
    VENV / "Scripts" / "pip.exe",
    VENV / "Scripts" / "pip3.exe",
]

# Pillow binaries + plugins this project never touches:
#   AVIF  - 7.5 MB decoder, no AVIF anywhere in the code
#   WebP  - 0.4 MB decoder
#   CMS   - LittleCMS colour management, unused for a solid-colour icon
PIL_GLOBS = [
    "_avif.*.pyd",
    "AvifImagePlugin.py",
    "_webp.*.pyd",
    "WebPImagePlugin.py",
    "_imagingcms.*.pyd",
    "ImageCms*.py",
]


def tree_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            pass
    return total


def human(num: int) -> str:
    return f"{num / 1048576:.1f} MB"


def collect_targets() -> list[Path]:
    """Return every path that can be removed, existing ones only."""
    targets: list[Path] = [p for p in PIP_TARGETS if p.exists()]
    targets += list(SITE.glob("pip-*.dist-info"))
    # pip ships wheel/vendor copies that glob above does not catch.
    targets += list(SITE.glob("pip-*.data"))

    if (SITE / "PIL").is_dir():
        for pattern in PIL_GLOBS:
            targets += list((SITE / "PIL").glob(pattern))

    # Bytecode caches: harmless, but they are pure duplication.
    targets += [p for p in SITE.rglob("__pycache__") if p.is_dir()]
    return dedupe(targets)


def dedupe(paths: list[Path]) -> list[Path]:
    """Drop paths already covered by a selected parent directory.

    Without this, `pip/` and its own `pip/**/__pycache__` are both counted,
    which inflates the reported savings and the predicted end size.
    """
    resolved = {p.resolve() for p in paths}
    kept: list[Path] = []
    for path in sorted(resolved, key=lambda p: len(p.parts)):
        if any(parent in resolved for parent in path.parents):
            continue
        kept.append(path)
    return kept


def main() -> int:
    parser = argparse.ArgumentParser(description="Trim the .venv size.")
    parser.add_argument("--check", action="store_true", help="report only")
    args = parser.parse_args()

    if not SITE.is_dir():
        print("No .venv found. Run setup.bat first.", file=sys.stderr)
        return 1

    before = tree_size(VENV)
    targets = collect_targets()

    if not targets:
        print(f"Already slim: {human(before)}. Nothing to remove.")
        return 0

    plan = 0
    for target in targets:
        plan += tree_size(target) if target.is_dir() else target.stat().st_size

    print(f"venv now      : {human(before)}")
    print(f"removable     : {human(plan)} in {len(targets)} path(s)")

    if args.check:
        for target in sorted(targets):
            print(f"  - {target.relative_to(VENV)}")
        print(f"would become  : {human(before - plan)}")
        return 0

    failed = 0
    for target in targets:
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as exc:
            failed += 1
            print(f"  ! {target.name}: {exc}", file=sys.stderr)

    after = tree_size(VENV)
    print(f"venv after    : {human(after)}  (freed {human(before - after)})")
    if failed:
        print(f"{failed} path(s) could not be removed (in use?).", file=sys.stderr)
        return 1
    print("Restore pip any time with: .venv\\Scripts\\python.exe -m ensurepip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
