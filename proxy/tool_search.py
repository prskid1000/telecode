"""BM25 + regex search over deferred tool definitions (zero dependencies)."""
from __future__ import annotations

import math
import re
from typing import Any

from proxy.config import BM25_K1, BM25_B, MAX_SEARCH_RESULTS


# ── Tokenizer ────────────────────────────────────────────────────────────────

_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t for t in _SPLIT.split(text.lower()) if t]


def _tool_text(tool: dict[str, Any]) -> str:
    """Flatten a tool definition into searchable text."""
    parts = [tool.get("name", ""), tool.get("description", "")]
    schema = tool.get("input_schema", {})
    props = schema.get("properties", {})
    for pname, pdef in props.items():
        parts.append(pname)
        parts.append(pdef.get("description", ""))
    return " ".join(parts)


# ── BM25 Index ───────────────────────────────────────────────────────────────

class BM25Index:
    """Minimal BM25 index over tool definitions."""

    def __init__(self, tools: list[dict[str, Any]]) -> None:
        self.tools = tools
        self.corpus: list[list[str]] = []
        self.df: dict[str, int] = {}
        self.avgdl = 0.0
        self._build()

    def _build(self) -> None:
        self.corpus = [_tokenize(_tool_text(t)) for t in self.tools]
        self.avgdl = (sum(len(d) for d in self.corpus) / len(self.corpus)) if self.corpus else 1.0
        self.df = {}
        for doc in self.corpus:
            seen: set[str] = set()
            for tok in doc:
                if tok not in seen:
                    self.df[tok] = self.df.get(tok, 0) + 1
                    seen.add(tok)

    def search(self, query: str, top_k: int = MAX_SEARCH_RESULTS) -> list[dict[str, Any]]:
        qtoks = _tokenize(query)
        if not qtoks:
            return []
        n = len(self.corpus)
        scores: list[float] = []
        for i, doc in enumerate(self.corpus):
            score = 0.0
            dl = len(doc)
            tf_map: dict[str, int] = {}
            for tok in doc:
                tf_map[tok] = tf_map.get(tok, 0) + 1
            for qt in qtoks:
                if qt not in tf_map:
                    continue
                tf = tf_map[qt]
                df = self.df.get(qt, 0)
                idf = math.log((n - df + 0.5) / (df + 0.5) + 1.0)
                numer = tf * (BM25_K1 + 1)
                denom = tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / self.avgdl)
                score += idf * numer / denom
            scores.append(score)
        ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)
        return [self.tools[i] for i in ranked[:top_k] if scores[i] > 0]


# ── Regex search ─────────────────────────────────────────────────────────────

def search_regex(
    tools: list[dict[str, Any]], pattern: str, max_results: int = MAX_SEARCH_RESULTS
) -> list[dict[str, Any]]:
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return []
    results: list[dict[str, Any]] = []
    for tool in tools:
        if rx.search(_tool_text(tool)):
            results.append(tool)
            if len(results) >= max_results:
                break
    return results
