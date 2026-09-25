#!/usr/bin/env python3
"""
jevroute.py — Jev as the cheap front door for Claude, Codex and Grok.

Jev never writes text. It SELECTS: which agent or tool a step needs, which
files or messages are worth sending along, and — by picking "ask_big_model" —
when a step really needs a frontier model. Everything it drops is logged, so
we can check it never threw away something the big model later needed.
When it's unsure, it keeps the chunk and escalates the step.

    from jevroute import Router
    r = Router()                                   # uses call_jev(); wire that first
    r.pick_tool("rerun the week-2 replay", ["bash", "read_file", "codex", "grok"])
    kept, stats = r.prune("why did week 2 lose?", chunks, budget_tokens=6000)

Where it goes: in Agent Bridge, before a task is dispatched — pick_tool
chooses the agent, prune chooses what context rides along.

Stdlib only. The one thing to wire is call_jev(), same contract style as jevloop.py.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

BIG_MODEL = "ask_big_model"
PREVIEW_CHARS = 1200      # Jev judges each chunk from its opening, not the whole file
BATCH = 40                # chunk questions per Jev call; all answered in one pass


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def call_jev(request: dict) -> dict:
    """
    Wire to TypeSafe (SDK or the OpenRouter route already in use). Pin jev-1.13.0.

    request = {"state": {...},
               "questions": {key: {"type": "choice", "question": str, "options": [...]}
                             | {"type": "noul", "question": str}}}
    returns   {key: {"choice": str, "confidence": float}}   for choice questions
              {key: {"p": float}}                           for noul questions
    """
    raise NotImplementedError("call_jev() is not wired yet — see docstring")


@dataclass
class Router:
    transport: object = call_jev
    min_conf: float = 0.70      # below this, a tool pick escalates to the big model
    keep_p: float = 0.35        # a chunk stays unless Jev is fairly sure it isn't needed
    log_path: str | None = None
    log: list = field(default_factory=list)

    def _record(self, kind: str, **data) -> None:
        rec = {"ts": time.time(), "kind": kind, **data}
        self.log.append(rec)
        if self.log_path:
            with open(self.log_path, "a") as fh:
                fh.write(json.dumps(rec) + "\n")

    def pick_tool(self, task: str, tools: list[str]) -> str:
        """One choice question. The big model is always an option and always the fallback."""
        options = list(dict.fromkeys(list(tools) + [BIG_MODEL]))
        ans = self.transport({"state": {"task": task}, "questions": {"tool": {
            "type": "choice", "options": options,
            "question": "Which one of these should handle this step?"}}})["tool"]
        choice, conf = ans.get("choice"), ans.get("confidence", 0.0)
        if choice not in options:
            raise ValueError(f"Jev picked an option that wasn't offered: {choice!r}")
        final = choice if conf >= self.min_conf else BIG_MODEL
        self._record("pick_tool", task=task, choice=choice, confidence=conf, final=final)
        return final

    def prune(self, task: str, chunks: list[tuple[str, str]], budget_tokens: int):
        """
        Compacting by selection: Jev scores every chunk "needed or not" in one
        parallel pass per batch; code keeps the likely-needed ones up to the
        budget, in their original order. Nothing is rewritten or summarized.
        """
        scores: dict[str, float] = {}
        for start in range(0, len(chunks), BATCH):
            batch = chunks[start:start + BATCH]
            state = {"task": task, "chunks": {cid: text[:PREVIEW_CHARS] for cid, text in batch}}
            questions = {f"need_{i}": {"type": "noul",
                                       "question": f"Chunk {cid} is needed to do the task."}
                         for i, (cid, _) in enumerate(batch)}
            ans = self.transport({"state": state, "questions": questions})
            for i, (cid, _) in enumerate(batch):
                scores[cid] = float(ans[f"need_{i}"]["p"])

        kept, used, dropped = [], 0, []
        for cid, text in sorted(chunks, key=lambda c: scores[c[0]], reverse=True):
            t = approx_tokens(text)
            if scores[cid] < self.keep_p:
                dropped.append((cid, round(scores[cid], 3), "not_needed"))
            elif used + t > budget_tokens:
                dropped.append((cid, round(scores[cid], 3), "over_budget"))
            else:
                kept.append((cid, text))
                used += t
        total = sum(approx_tokens(t) for _, t in chunks)
        stats = {"chunks_in": len(chunks), "chunks_kept": len(kept), "tokens_in": total,
                 "tokens_sent": used, "saved": round(1 - used / total, 3) if total else 0.0}
        self._record("prune", task=task, dropped=dropped, **stats)
        order = {cid: i for i, (cid, _) in enumerate(chunks)}
        return sorted(kept, key=lambda c: order[c[0]]), stats


def drop_misses(log: list[dict], later_requested: set[str]) -> float | None:
    """
    The quality check. Share of dropped chunks the big model later asked for
    anyway. If it climbs above a few percent, lower keep_p or raise the budget.
    """
    dropped = [d[0] for rec in log if rec["kind"] == "prune" for d in rec["dropped"]]
    if not dropped:
        return None
    return sum(cid in later_requested for cid in dropped) / len(dropped)
