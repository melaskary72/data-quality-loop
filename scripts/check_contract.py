#!/usr/bin/env python3
"""Enforce BUILD_CONTRACT.md by static check. Exits non zero on any violation.

Checks:
  C3  sealed ground truth is referenced only by eval/, plus the narrow
      generator carve out (dql/generate.py writes it)
  C5  no model id is hardcoded at a call site, the model comes from DQL_MODEL
  C6  the dependency allowlist is not exceeded
  C8  .env is gitignored and untracked
  C9  no em dash appears in any prose file
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GROUND_TRUTH = "ground_truth.jsonl"
GT_ALLOWED = {"eval", "scripts/check_contract.py", "specs", "docs", "README.md",
              "BUILD_CONTRACT.md", "REPORT.md"}
GT_CODE_CARVE_OUT = {"dql/generate.py"}  # writes the file, never reads it back

ALLOWLIST = {
    "anthropic", "pydantic", "rapidfuzz", "scikit-learn", "sklearn",
    "rich", "python-dotenv", "dotenv", "resend",
}

MODEL_ID_PATTERN = re.compile(r"claude-[a-z0-9.\-]+")
MODEL_ID_ALLOWED = {"dql/llm.py", "scripts/check_contract.py", ".env", ".env.example"}

PROSE_SUFFIXES = {".md", ".yaml", ".yml", ".txt"}
SKIP_DIRS = {".git", ".venv", "__pycache__", "assets", "node_modules"}

failures: list[str] = []
notes: list[str] = []


def walk(suffixes: set[str] | None = None):
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if suffixes and path.suffix not in suffixes:
            continue
        yield path


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def check_ground_truth() -> None:
    """C3: only eval/ may reference the sealed file, plus the generator."""
    for path in walk():
        r = rel(path)
        if r == "data-quality-loop-BUILD-BRIEF.md":
            continue
        if any(r == a or r.startswith(a + "/") for a in GT_ALLOWED):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if GROUND_TRUTH not in text:
            continue
        if r in GT_CODE_CARVE_OUT:
            # The carve out is write only. Reading it back would be a violation.
            for lineno, line in enumerate(text.splitlines(), 1):
                if GROUND_TRUTH in line or "GROUND_TRUTH" in line:
                    if re.search(r"\b(open|read_text|loads|load|readlines)\s*\(", line):
                        if "write" not in line:
                            failures.append(
                                f"C3 {r}:{lineno} generator carve out is write only, "
                                f"this line reads the sealed file: {line.strip()}"
                            )
            notes.append(f"C3 carve out exercised (write only): {r}")
            continue
        lineno = next(
            i for i, line in enumerate(text.splitlines(), 1) if GROUND_TRUTH in line
        )
        failures.append(
            f"C3 {r}:{lineno} references the sealed ground truth outside eval/"
        )


def check_model_ids() -> None:
    """C5: no hardcoded model id outside the single place that resolves it."""
    for path in walk({".py"}):
        r = rel(path)
        if r in MODEL_ID_ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if MODEL_ID_PATTERN.search(line):
                failures.append(
                    f"C5 {r}:{lineno} hardcoded model id, read DQL_MODEL instead: "
                    f"{line.strip()}"
                )


def check_dependencies() -> None:
    """C6: the declared dependency set stays inside the allowlist."""
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.exists():
        failures.append("C6 pyproject.toml is missing")
        return
    text = pyproject.read_text(encoding="utf-8")
    block = re.search(r"dependencies = \[(.*?)\]", text, re.DOTALL)
    if not block:
        failures.append("C6 pyproject.toml declares no dependencies block")
        return
    declared = set(re.findall(r'"([A-Za-z0-9_.\-]+)', block.group(1)))
    extra = {d for d in declared if d.lower() not in ALLOWLIST}
    if extra:
        failures.append(
            f"C6 dependencies outside the allowlist: {sorted(extra)}. "
            "Add a written justification to docs/06-CHANGE-LOG.md first."
        )


def check_secrets() -> None:
    """C8: .env gitignored and never tracked."""
    gitignore = ROOT / ".gitignore"
    if not gitignore.exists() or ".env" not in gitignore.read_text(encoding="utf-8"):
        failures.append("C8 .env is not in .gitignore")
    try:
        tracked = subprocess.run(
            ["git", "ls-files", ".env"], cwd=ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()
        if tracked:
            failures.append("C8 .env is tracked by git")
    except OSError:
        notes.append("C8 git unavailable, skipped the tracked-file check")


def check_prose() -> None:
    """C9: no em dash in prose."""
    for path in walk(PROSE_SUFFIXES):
        r = rel(path)
        if r == "data-quality-loop-BUILD-BRIEF.md":
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "—" in line:
                failures.append(f"C9 {r}:{lineno} em dash in prose: {line.strip()[:80]}")


def main() -> int:
    check_ground_truth()
    check_model_ids()
    check_dependencies()
    check_secrets()
    check_prose()

    for note in notes:
        print(f"  note: {note}")

    if failures:
        print(f"\ncheck_contract: {len(failures)} violation(s)\n")
        for f in failures:
            print(f"  FAIL {f}")
        return 1
    print("\ncheck_contract: green, all clauses hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
