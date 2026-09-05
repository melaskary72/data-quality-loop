"""Canonical paths. Every module resolves files through here, so a path is
never spelled twice and never drifts between stages."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA = ROOT / "data"
EVAL = ROOT / "eval"
EXPORT = ROOT / "export"
ASSETS = ROOT / "assets"
SCRIPTS = ROOT / "scripts"

DB = DATA / "dql.db"
SEED_MANIFEST = DATA / "seed_manifest.json"
ADJUDICATIONS = DATA / "adjudications.jsonl"
V1_TO_V2_DIFF = DATA / "v1_to_v2_diff.jsonl"
VENDOR_ALIGNMENT = DATA / "vendor_alignment.yaml"

TAXONOMY = ROOT / "taxonomy.yaml"
TAXONOMY_RATIONALE = ROOT / "taxonomy_rationale.md"
REPORT = ROOT / "REPORT.md"

INDUCTION_REPORT = DATA / "induction_report.md"

TRAIN_JSONL = EXPORT / "train.jsonl"
EVAL_JSONL = EXPORT / "eval.jsonl"
DATASHEET = EXPORT / "DATASHEET.md"


def ensure_dirs() -> None:
    for d in (DATA, EVAL, EXPORT, ASSETS, ASSETS / "screenshots"):
        d.mkdir(parents=True, exist_ok=True)
