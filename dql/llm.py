"""The only module that talks to the Anthropic API.

Responsibilities, all of them contract clauses:

- read the model from `DQL_MODEL`, never hardcode one at a call site (C5)
- cache every raw response to SQLite keyed by model plus content hash, so a
  re-run without `--fresh` costs nothing and reproduces exactly (C4)
- record tokens and USD for every call, and refuse a call that would push
  cumulative spend past `DQL_COST_CAP_USD` before it is sent (C5)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

from . import store

load_dotenv()

# Prompt template version. Bump when a prompt changes, so stale cache entries
# are not silently reused against a new prompt.
PROMPT_VERSION = "v1"

DEFAULT_MODEL = "claude-haiku-4-5"

# USD per million tokens. The cheapest capable model is the default because the
# whole project runs under a 3.00 USD cap.
PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}
FALLBACK_PRICE = (1.00, 5.00)


class CostCapExceeded(RuntimeError):
    """Raised before a call that would cross DQL_COST_CAP_USD (C5)."""


class MissingAPIKey(RuntimeError):
    """Raised when no API key is configured."""


def get_model() -> str:
    return os.environ.get("DQL_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def get_cost_cap() -> float:
    try:
        return float(os.environ.get("DQL_COST_CAP_USD", "3.00"))
    except ValueError:
        return 3.00


def price_for(model: str) -> tuple[float, float]:
    for known, price in PRICING.items():
        if model.startswith(known):
            return price
    return FALLBACK_PRICE


def usd_for(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = price_for(model)
    return (tokens_in / 1_000_000) * price_in + (tokens_out / 1_000_000) * price_out


def estimate_tokens(text: str) -> int:
    """Conservative character based estimate, used only for the pre call cost
    check. Actual billing always uses the tokens the API reports."""
    return max(1, int(len(text) / 3.2))


@dataclass
class LLMResult:
    data: Any
    cached: bool
    tokens_in: int
    tokens_out: int
    usd: float


def cache_key(model: str, system: str, user: str, schema: dict | None) -> str:
    payload = json.dumps(
        {
            "v": PROMPT_VERSION,
            "model": model,
            "system": system,
            "user": user,
            "schema": schema,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return store.sha256_text(payload)


def _extract_json(text: str) -> Any:
    """Parse JSON from a response body, tolerating a code fence."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Last resort: the outermost balanced object or array.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"could not parse JSON from response: {text[:400]}")


class Client:
    """Cached, cost capped, JSON returning Claude client."""

    def __init__(self, conn: sqlite3.Connection, run_id: str, fresh: bool = False) -> None:
        self.conn = conn
        self.run_id = run_id
        self.fresh = fresh
        self.model = get_model()
        self.cap = get_cost_cap()
        self._client = None
        self._structured_ok = True
        self.calls_made = 0
        self.calls_cached = 0

    # -- lazy client construction, so cache only runs need no key -----------

    def _api(self):
        if self._client is None:
            key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not key:
                raise MissingAPIKey(
                    "ANTHROPIC_API_KEY is not set.\n"
                    "Copy .env.example to .env and add your key, or export it in "
                    "the shell. No partial state has been written."
                )
            import anthropic

            self._client = anthropic.Anthropic(api_key=key, max_retries=3)
        return self._client

    # -- cost guard ---------------------------------------------------------

    def spend(self) -> float:
        return store.total_cost(self.conn)

    def _guard(self, system: str, user: str, max_tokens: int) -> None:
        est_in = estimate_tokens(system) + estimate_tokens(user)
        projected = usd_for(self.model, est_in, max_tokens)
        current = self.spend()
        if current + projected > self.cap:
            raise CostCapExceeded(
                f"Refusing the call: cumulative spend {current:.4f} USD plus a "
                f"worst case {projected:.4f} USD for this call would cross the "
                f"{self.cap:.2f} USD cap.\n"
                "Raise DQL_COST_CAP_USD deliberately, or run without --fresh to "
                "use the cache."
            )

    # -- the one call path --------------------------------------------------

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 4000,
    ) -> LLMResult:
        key = cache_key(self.model, system, user, schema)

        if not self.fresh:
            row = self.conn.execute(
                "SELECT * FROM llm_cache WHERE cache_key = ?", (key,)
            ).fetchone()
            if row is not None:
                self.calls_cached += 1
                return LLMResult(
                    data=json.loads(row["response"]),
                    cached=True,
                    tokens_in=row["tokens_in"],
                    tokens_out=row["tokens_out"],
                    usd=0.0,
                )

        self._guard(system, user, max_tokens)

        import anthropic

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if schema is not None and self._structured_ok:
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": schema}
            }

        try:
            response = self._api().messages.create(**kwargs)
        except anthropic.BadRequestError as exc:
            # Some models decline output_config. Fall back once to prompt led
            # JSON rather than failing the stage, and remember the decision.
            if schema is not None and self._structured_ok and "output_config" in str(exc):
                self._structured_ok = False
                kwargs.pop("output_config", None)
                kwargs["system"] = system + (
                    "\n\nRespond with a single JSON object matching this schema. "
                    "No prose, no code fence.\n" + json.dumps(schema)
                )
                response = self._api().messages.create(**kwargs)
            else:
                raise
        except anthropic.NotFoundError as exc:
            raise RuntimeError(
                f"Model {self.model!r} was not found. Check DQL_MODEL in .env."
            ) from exc
        except anthropic.AuthenticationError as exc:
            raise MissingAPIKey(
                "The API rejected the key in ANTHROPIC_API_KEY. Check .env."
            ) from exc

        text = "".join(b.text for b in response.content if b.type == "text")
        data = _extract_json(text)

        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        usd = usd_for(self.model, tokens_in, tokens_out)

        store.record_cost(
            self.conn,
            call_id=str(uuid.uuid4()),
            run_id=self.run_id,
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            usd=usd,
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?,?,?)",
            (key, self.model, json.dumps(data, ensure_ascii=False),
             tokens_in, tokens_out, store.utcnow()),
        )
        self.conn.commit()
        self.calls_made += 1

        return LLMResult(data=data, cached=False, tokens_in=tokens_in,
                         tokens_out=tokens_out, usd=usd)
