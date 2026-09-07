"""アイデア在庫の検証。

在庫の下限は「打ち手がない」と言わせないための仕組みなので、
中身の薄い項目で数だけ満たされると形骸化する。
ここが通ると、ゲート全体が意味を失う。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import valid_ideas
from expkit.config import load_lint_config
from expkit.gates import gate_backlog, gate_stop_decision, policy, valid_open_ideas
from expkit.lint import lint_backlog, lint_idea
from expkit.state import load_state

GOOD = {
    "id": "i0001",
    "created_at": "2026-09-07T12:00:00+09:00",
    "action": "ユーザ単位の集約特徴（購入額の mean / std / count）を12列作り、"
              "既存の特徴集合に足して比較する。",
    "axes": ["features"],
    "tier": "explore",
    "expected": {"metric": "cv.mean", "direction": "increase", "magnitude": 0.003},
    "evidence": "priors/tabular.md#t03",
    "cost": 1.0,
    "status": "open",
}


def _errors(repo: Path, **over: object) -> list[str]:
    idea = {**GOOD, **over}
    report = lint_idea(idea, cfg=load_lint_config(repo), index=0,
                       known_axes=load_state(repo).axis_ids)
    return [f.rule for f in report.errors]


def _set_backlog(repo: Path, ideas: list[dict]) -> None:
    (repo / "ideas" / "backlog.yaml").write_text(
        yaml.safe_dump({"ideas": ideas}, allow_unicode=True), encoding="utf-8")


class TestIdea:
    def test_well_formed_passes(self, repo: Path) -> None:
        assert _errors(repo) == []

    def test_vague_action_is_rejected(self, repo: Path) -> None:
        """「特徴量を追加」は1実験の粒度になっていない。"""
        assert "idea.action_too_vague" in _errors(repo, action="特徴量を追加")

    @pytest.mark.parametrize("evidence", ["効きそうだから", "経験上よく効く", "一般に有効"])
    def test_evidence_without_source_is_rejected(self, repo: Path, evidence: str) -> None:
        """出典を要求するのが、思いつきで在庫を埋めさせない主な歯止め。"""
        assert "idea.evidence_without_source" in _errors(repo, evidence=evidence)

    @pytest.mark.parametrize("evidence", [
        "priors/common.md#c01", "exp0003 で観測された", "dec0002 の棄却理由",
        "https://example.invalid/solution",
    ])
    def test_recognised_sources_pass(self, repo: Path, evidence: str) -> None:
        assert "idea.evidence_without_source" not in _errors(repo, evidence=evidence)

    def test_nonexistent_experiment_as_source_is_rejected(self, repo: Path) -> None:
        assert "link.experiment_missing" in [
            f.rule for f in lint_idea(
                {**GOOD, "evidence": "exp9999 で観測された"},
                cfg=load_lint_config(repo), index=0,
                known_axes=load_state(repo).axis_ids, known_experiments=set(),
            ).errors
        ]

    def test_magnitude_must_be_numeric(self, repo: Path) -> None:
        assert "schema.magnitude" in _errors(
            repo, expected={"metric": "cv.mean", "direction": "increase",
                            "magnitude": "大きい"})

    def test_cost_must_be_positive(self, repo: Path) -> None:
        assert "schema.cost" in _errors(repo, cost=0)

    def test_unknown_axis_is_rejected(self, repo: Path) -> None:
        assert "schema.unknown_axis" in _errors(repo, axes=["nonexistent"])

    def test_retired_needs_a_reason(self, repo: Path) -> None:
        assert "idea.retired_without_reason" in _errors(repo, status="retired")

    def test_vocabulary_applies(self, repo: Path) -> None:
        assert "vocab.metaphor" in _errors(
            repo, action="集約特徴がよく効いたので、同じ発想で店舗単位にも広げてみる。")


class TestBacklogAsAWhole:
    def test_duplicate_ids_are_rejected(self, repo: Path) -> None:
        _set_backlog(repo, [GOOD, {**GOOD, "action": GOOD["action"] + "（別案）"}])
        state = load_state(repo)
        _per, whole = lint_backlog(state.backlog, cfg=load_lint_config(repo),
                                   policy=policy(state), known_axes=state.axis_ids)
        assert "idea.duplicate_id" in [f.rule for f in whole.errors]

    def test_narrow_backlog_is_rejected(self, repo: Path) -> None:
        """同じ発想の変奏で下限を満たすのを止める。"""
        _set_backlog(repo, [{**GOOD, "id": f"i{n:04d}"} for n in range(1, 9)])
        state = load_state(repo)
        _per, whole = lint_backlog(state.backlog, cfg=load_lint_config(repo),
                                   policy=policy(state), known_axes=state.axis_ids)
        assert "backlog.too_narrow" in [f.rule for f in whole.errors]


class TestGateCountsOnlyValidIdeas:
    """ここが本体。ゲートが数えるのは検証を通ったものだけ。"""

    def test_invalid_ideas_do_not_count(self, repo: Path) -> None:
        _set_backlog(repo, [{**GOOD, "id": f"i{n:04d}", "evidence": "効きそう"}
                            for n in range(1, 9)])
        good, bad = valid_open_ideas(load_state(repo))
        assert (len(good), len(bad)) == (0, 8)
        gates = {f.gate for f in gate_backlog(load_state(repo))}
        assert "backlog.invalid_entries" in gates
        assert "backlog.thin" in gates

    def test_valid_ideas_satisfy_the_gate(self, repo: Path) -> None:
        _set_backlog(repo, valid_ideas(8))
        assert gate_backlog(load_state(repo)) == []

    def test_stop_is_not_blocked_by_junk_ideas(self, repo: Path) -> None:
        """逆向きの穴。中身の無い在庫を口実に打ち切りを止め続けない。"""
        _set_backlog(repo, [{**GOOD, "id": f"i{n:04d}", "evidence": "効きそう"}
                            for n in range(1, 9)])
        gates = {f.gate for f in gate_stop_decision(load_state(repo))}
        assert "stop.backlog_not_empty" not in gates
