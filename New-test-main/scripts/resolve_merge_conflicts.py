#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CONFLICT_START = "<<<<<<<"
CONFLICT_MID = "======="
CONFLICT_END = ">>>>>>>"


def resolve_conflicts(content: str, keep: str) -> tuple[str, bool]:
    lines = content.splitlines(keepends=True)
    output: list[str] = []
    index = 0
    changed = False

    while index < len(lines):
        line = lines[index]
        if line.startswith(CONFLICT_START):
            changed = True
            index += 1
            ours: list[str] = []
            theirs: list[str] = []
            while index < len(lines) and not lines[index].startswith(CONFLICT_MID):
                ours.append(lines[index])
                index += 1
            if index < len(lines) and lines[index].startswith(CONFLICT_MID):
                index += 1
            while index < len(lines) and not lines[index].startswith(CONFLICT_END):
                theirs.append(lines[index])
                index += 1
            while index < len(lines) and not lines[index].startswith(CONFLICT_END):
                index += 1
            if index < len(lines) and lines[index].startswith(CONFLICT_END):
                index += 1
            output.extend(ours if keep == "ours" else theirs)
            continue

        output.append(line)
        index += 1

    return "".join(output), changed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove merge conflict markers from files.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="File or directory to scan (default: current directory).",
    )
    parser.add_argument(
        "--keep",
        choices=("ours", "theirs"),
        default="ours",
        help="Which side of conflicts to keep when applying changes.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to files instead of just reporting.",
    )
    args = parser.parse_args()

    root = Path(args.path)
    paths = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
    conflicts_found = False

    for path in paths:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        updated, changed = resolve_conflicts(content, args.keep)
        if changed:
            conflicts_found = True
            if args.apply:
                path.write_text(updated, encoding="utf-8")
                print(f"Resolved conflicts in {path}")
            else:
                print(f"Conflict markers found in {path}")

    if conflicts_found and not args.apply:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
