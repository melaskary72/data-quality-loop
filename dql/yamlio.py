"""A restricted-subset YAML emitter and parser, standard library only.

Why this exists: PyYAML is not on the dependency allowlist (BUILD_CONTRACT C6),
but the taxonomy is the one artifact a human is expected to read and hand edit,
so it should be YAML rather than JSON.

The supported subset is exactly what this repo's artifacts use:

- nested mappings
- sequences of scalars
- sequences of mappings
- scalars: string, int, float, bool, null
- full line comments starting with `#`

Every file this parser reads is either produced by `dumps` below or hand
written in the same subset, so it never has to survive arbitrary YAML. Strings
are always emitted double quoted with JSON escaping, which sidesteps the entire
class of YAML quoting surprises (the Norway problem, colons in values, leading
dashes). `selftest()` round trips a representative document.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

INDENT = "  "


# --------------------------------------------------------------------------
# emit
# --------------------------------------------------------------------------

def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"yamlio cannot emit {type(value).__name__}")


def _emit(value: Any, depth: int, out: list[str]) -> None:
    pad = INDENT * depth
    if isinstance(value, dict):
        for key, val in value.items():
            if isinstance(val, dict) and val:
                out.append(f"{pad}{key}:")
                _emit(val, depth + 1, out)
            elif isinstance(val, list) and val:
                out.append(f"{pad}{key}:")
                _emit(val, depth + 1, out)
            elif isinstance(val, dict):
                out.append(f"{pad}{key}: {{}}")
            elif isinstance(val, list):
                out.append(f"{pad}{key}: []")
            else:
                out.append(f"{pad}{key}: {_scalar(val)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                if not item:
                    out.append(f"{pad}- {{}}")
                    continue
                # First key rides on the dash line, the rest align under it.
                inner: list[str] = []
                _emit(item, depth + 1, inner)
                first = inner[0][len(INDENT * (depth + 1)):]
                out.append(f"{pad}- {first}")
                out.extend(inner[1:])
            elif isinstance(item, list):
                raise TypeError("yamlio does not emit nested bare sequences")
            else:
                out.append(f"{pad}- {_scalar(item)}")
    else:
        out.append(f"{pad}{_scalar(value)}")


def dumps(value: Any, header: str | None = None) -> str:
    out: list[str] = []
    if header:
        out.extend(f"# {line}" if line else "#" for line in header.splitlines())
        out.append("")
    _emit(value, 0, out)
    return "\n".join(out) + "\n"


def dump(value: Any, path: Path, header: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(value, header=header), encoding="utf-8")


# --------------------------------------------------------------------------
# parse
# --------------------------------------------------------------------------

def _parse_scalar(text: str) -> Any:
    text = text.strip()
    if text.startswith('"'):
        return json.loads(text)
    if text in ("null", "~", ""):
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if text == "{}":
        return {}
    if text == "[]":
        return []
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


class _Line:
    __slots__ = ("indent", "text", "no")

    def __init__(self, raw: str, no: int) -> None:
        self.indent = len(raw) - len(raw.lstrip(" "))
        self.text = raw.strip()
        self.no = no


def _tokenize(text: str) -> list[_Line]:
    lines = []
    for i, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(_Line(raw, i))
    return lines


def _split_key(text: str) -> tuple[str, str]:
    """Split `key: value` on the first colon that is not inside a quoted run."""
    in_quote = False
    escaped = False
    for i, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
        elif ch == '"':
            in_quote = not in_quote
        elif ch == ":" and not in_quote:
            return text[:i].strip(), text[i + 1:].strip()
    raise ValueError(f"expected `key: value`, got: {text!r}")


def _parse_block(lines: list[_Line], pos: int, indent: int) -> tuple[Any, int]:
    if pos >= len(lines):
        return None, pos

    if lines[pos].text.startswith("- "):
        items: list[Any] = []
        while pos < len(lines) and lines[pos].indent == indent and lines[pos].text.startswith("- "):
            rest = lines[pos].text[2:].strip()
            child_indent = indent + 2
            try:
                key, value = _split_key(rest)
                is_mapping = True
            except ValueError:
                is_mapping = False
            if is_mapping:
                mapping: dict[str, Any] = {}
                if value:
                    mapping[key] = _parse_scalar(value)
                    pos += 1
                else:
                    pos += 1
                    if pos < len(lines) and lines[pos].indent > child_indent:
                        mapping[key], pos = _parse_block(lines, pos, lines[pos].indent)
                    else:
                        mapping[key] = None
                while pos < len(lines) and lines[pos].indent == child_indent and not lines[pos].text.startswith("- "):
                    k2, v2 = _split_key(lines[pos].text)
                    if v2:
                        mapping[k2] = _parse_scalar(v2)
                        pos += 1
                    else:
                        pos += 1
                        if pos < len(lines) and lines[pos].indent > child_indent:
                            mapping[k2], pos = _parse_block(lines, pos, lines[pos].indent)
                        else:
                            mapping[k2] = None
                items.append(mapping)
            else:
                items.append(_parse_scalar(rest))
                pos += 1
        return items, pos

    mapping = {}
    while pos < len(lines) and lines[pos].indent == indent:
        if lines[pos].text.startswith("- "):
            break
        key, value = _split_key(lines[pos].text)
        if value:
            mapping[key] = _parse_scalar(value)
            pos += 1
        else:
            pos += 1
            if pos < len(lines) and lines[pos].indent > indent:
                mapping[key], pos = _parse_block(lines, pos, lines[pos].indent)
            else:
                mapping[key] = None
    return mapping, pos


def loads(text: str) -> Any:
    lines = _tokenize(text)
    if not lines:
        return {}
    value, _ = _parse_block(lines, 0, lines[0].indent)
    return value


def load(path: Path) -> Any:
    return loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# self test
# --------------------------------------------------------------------------

SELFTEST_DOC: dict[str, Any] = {
    "version": 1,
    "locked": True,
    "note": None,
    "threshold": 0.55,
    "tricky": "a: colon, a # hash, a - dash, quotes \" and unicode مرحبا",
    "domains": [
        {
            "name": "billing",
            "leaves": [
                {
                    "name": "invoice_dispute",
                    "definition": "Customer disputes a charge on an invoice.",
                    "boundary_examples": ["charged twice", "line item wrong"],
                    "weight": 0.25,
                    "deprecated": False,
                },
                {
                    "name": "refund_request",
                    "definition": "Customer asks for money back.",
                    "boundary_examples": ["please refund"],
                    "weight": 1,
                    "deprecated": None,
                },
            ],
        },
        {"name": "empty_domain", "leaves": []},
    ],
    "alignment": {"invoice_dispute": "invoice_dispute", "unmapped": None},
}


def selftest() -> bool:
    """Round trip the representative document. Raises on any mismatch."""
    once = dumps(SELFTEST_DOC, header="selftest")
    back = loads(once)
    if back != SELFTEST_DOC:
        raise AssertionError(
            f"yamlio round trip mismatch.\nexpected: {SELFTEST_DOC}\ngot     : {back}"
        )
    twice = dumps(back, header="selftest")
    if once != twice:
        raise AssertionError("yamlio emit is not idempotent")
    return True


if __name__ == "__main__":
    selftest()
    print("yamlio selftest: ok")
