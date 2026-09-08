"""リポジトリ全体の状態を読み、派生指標を計算する。

意思決定のゲートはすべてここが返す状態の上に立つ。
LLM に状態を要約させると要約の段階で情報が落ちるので、要約はさせない。
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

import yaml

from . import metrics as metrics_mod
from .paths import decisions_dir, experiments_dir, knowledge_dir, repo_root
from .schema import DEFAULT_AXES


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@dataclass
class Experiment:
    id: str
    record: dict
    metrics: dict | None

    @property
    def tier(self) -> str | None:
        return self.record.get("tier")

    @property
    def axes(self) -> list[str]:
        return list(self.record.get("axes") or [])

    def metric(self, path: str) -> Any:
        if not self.metrics:
            return None
        try:
            return metrics_mod.resolve(self.metrics, f"metrics.{path}")
        except KeyError:
            return None


@dataclass
class State:
    root: Path
    competition: dict = dc_field(default_factory=dict)
    coverage: dict = dc_field(default_factory=dict)
    validation: dict = dc_field(default_factory=dict)
    landscape: dict = dc_field(default_factory=dict)
    domain: dict = dc_field(default_factory=dict)
    proposals: dict = dc_field(default_factory=dict)
    backlog: dict = dc_field(default_factory=dict)
    experiments: list[Experiment] = dc_field(default_factory=list)
    decisions: list[dict] = dc_field(default_factory=list)

    # ---- 索引 -------------------------------------------------------------
    @property
    def experiment_ids(self) -> set[str]:
        return {e.id for e in self.experiments}

    @property
    def hypothesis_ids(self) -> set[str]:
        out = set()
        for e in self.experiments:
            for h in e.record.get("hypothesis") or []:
                if isinstance(h, dict) and h.get("id"):
                    out.add(f"{e.id}#{h['id']}")
        return out

    @property
    def axes(self) -> list[dict]:
        return list(self.coverage.get("axes") or [])

    @property
    def axis_ids(self) -> set[str]:
        return {a.get("id") for a in self.axes if isinstance(a, dict)}

    def axis(self, axis_id: str) -> dict | None:
        for a in self.axes:
            if isinstance(a, dict) and a.get("id") == axis_id:
                return a
        return None

    @property
    def decision_ids(self) -> set[str]:
        return {str(d.get("id")) for d in self.decisions if d.get("id")}

    @property
    def proposal_list(self) -> list[dict]:
        return [p for p in (self.proposals.get("proposals") or []) if isinstance(p, dict)]

    @property
    def facts(self) -> list[dict]:
        return [f for f in (self.domain.get("facts") or []) if isinstance(f, dict)]

    @property
    def sources(self) -> list[dict]:
        return [s for s in (self.landscape.get("sources") or []) if isinstance(s, dict)]

    @property
    def source_ids(self) -> set[str]:
        return {str(s.get("id")) for s in self.sources if s.get("id")}

    @property
    def idea_ids(self) -> set[str]:
        return {str(i.get("id")) for i in self.ideas
                if isinstance(i, dict) and i.get("id")}

    @property
    def ideas(self) -> list[dict]:
        return list(self.backlog.get("ideas") or [])

    def open_ideas(self) -> list[dict]:
        return [i for i in self.ideas if (i.get("status") or "open") == "open"]

    # ---- 派生指標 ---------------------------------------------------------
    @property
    def phase(self) -> str | None:
        return self.competition.get("current_phase")

    @property
    def lb_noise(self) -> float:
        """Public LB の差がこれ未満なら結論を出せない、という幅。"""
        try:
            return float(self.competition.get("lb_noise", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def metric_verified(self) -> bool:
        return bool((self.competition.get("metric") or {}).get("verified"))

    def axis_experiments(self, axis_id: str) -> list[Experiment]:
        return [e for e in self.experiments if axis_id in e.axes]

    def axis_status(self, axis_id: str) -> str:
        a = self.axis(axis_id) or {}
        declared = a.get("status")
        if declared == "saturated":
            return "saturated"
        if declared == "retired":
            return "retired"
        return "open" if self.axis_experiments(axis_id) else "untouched"

    def untouched_axes(self) -> list[str]:
        return [a for a in sorted(self.axis_ids) if self.axis_status(a) == "untouched"]

    def unsaturated_axes(self) -> list[str]:
        return [a for a in sorted(self.axis_ids) if self.axis_status(a) in ("untouched", "open")]

    def tier_counts(self, window: int | None = None) -> dict[str, int]:
        exps = sorted(self.experiments, key=lambda e: e.id)
        if window:
            exps = exps[-window:]
        counts = {"exploit": 0, "explore": 0, "moonshot": 0}
        for e in exps:
            if e.tier in counts:
                counts[e.tier] += 1
        return counts

    def cv_lb_pairs(self) -> list[tuple[str, float, float]]:
        out = []
        for e in self.experiments:
            cv = e.metric("cv.mean")
            lb = e.metric("lb.public")
            if isinstance(cv, (int, float)) and isinstance(lb, (int, float)):
                out.append((e.id, float(cv), float(lb)))
        return out

    def closed_decisions(self) -> list[dict]:
        return [
            d for d in self.decisions
            if isinstance(d.get("outcome"), dict) and d["outcome"].get("result")
        ]


def load_state(root: Path | None = None) -> State:
    r = root or repo_root()
    state = State(root=r)

    state.competition = _load_yaml(r / "competition.yaml")
    state.coverage = _load_yaml(knowledge_dir(r) / "coverage.yaml")
    state.validation = _load_yaml(knowledge_dir(r) / "validation.yaml")
    state.landscape = _load_yaml(knowledge_dir(r) / "landscape.yaml")
    state.domain = _load_yaml(knowledge_dir(r) / "domain.yaml")
    state.proposals = _load_yaml(r / "feedback" / "proposals.yaml")
    state.backlog = _load_yaml(r / "ideas" / "backlog.yaml")

    if not state.coverage.get("axes"):
        state.coverage = {"axes": [{"id": a, "status": "untouched"} for a in DEFAULT_AXES]}

    exp_dir = experiments_dir(r)
    if exp_dir.exists():
        for d in sorted(exp_dir.iterdir()):
            rec = d / "record.yaml"
            if not d.is_dir() or not rec.exists():
                continue
            mdoc: dict | None
            try:
                mdoc = metrics_mod.load(d.name, r)
            except (FileNotFoundError, ValueError):
                mdoc = None
            state.experiments.append(
                Experiment(id=d.name, record=_load_yaml(rec), metrics=mdoc)
            )

    dec_dir = decisions_dir(r)
    if dec_dir.exists():
        for f in sorted(dec_dir.glob("*.yaml")):
            state.decisions.append(_load_yaml(f))

    return state


# ---------------------------------------------------------------------------
# 統計。scipy を持ち込まずに済む範囲で実装する。
# ---------------------------------------------------------------------------

def _rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    return _pearson(_rank(xs), _rank(ys))
