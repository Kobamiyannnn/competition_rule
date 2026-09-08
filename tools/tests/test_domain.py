"""ドメイン知識の台帳と、そこに依存するゲート。

landscape が「何をやればいいか」なら、こちらは「何が本当か」。
confidence を型で分けるのが肝で、検証していない仮定が前提として
使われるのを見えるようにする。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import base_record, make_experiment, valid_domain
from expkit.config import load_lint_config
from expkit.gates import (
    gate_new_experiment,
    gate_stop_decision,
    inconclusive_streak,
    policy,
    recon_findings,
)
from expkit.lint import assumed_facts, lint_domain
from expkit.state import load_state

FACT = {
    "id": "d0001",
    "statement": "誤差の上位10%のうち72%が、撮影装置Bの画像に集中している。",
    "source": "error_analysis",
    "evidence": "exp0001 の OOF を装置ごとに集計した。",
    "confidence": "confirmed",
    "implication": "装置を特徴量に入れるか、装置ごとに正規化する。"
                   "test の装置比率が train と違うなら分布ずれの原因でもある。",
    "transferred_to": [],
}


def _set(repo: Path, doc: dict) -> None:
    (repo / "knowledge" / "domain.yaml").write_text(
        yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")


def _errors(repo: Path, doc: dict) -> list[str]:
    state = load_state(repo)
    per_fact, whole = lint_domain(
        doc, cfg=load_lint_config(repo), policy=policy(state),
        known_experiments=state.experiment_ids, known_ideas=state.idea_ids,
    )
    return [f.rule for r in [*per_fact, whole] for f in r.errors]


def _fact_errors(repo: Path, **over: object) -> list[str]:
    doc = valid_domain()
    doc["facts"][0] = {**FACT, **over}
    make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record("exp0001"))
    return _errors(repo, doc)


def _gates(findings: list) -> set[str]:
    return {f.gate for f in findings if f.severity == "error"}


class TestDomainLint:
    def test_well_formed_passes(self, repo: Path) -> None:
        assert _errors(repo, valid_domain()) == []

    def test_too_few_facts(self, repo: Path) -> None:
        assert "domain.too_few_facts" in _errors(repo, valid_domain(n_facts=2))

    def test_nothing_confirmed_is_rejected(self, repo: Path) -> None:
        """読んだだけの知識で進むと、検証していない前提の上に実験を積むことになる。"""
        assert "domain.nothing_confirmed" in _errors(repo, valid_domain(n_confirmed=0))

    def test_confirmed_needs_recheckable_evidence(self, repo: Path) -> None:
        assert "domain.unconfirmable_evidence" in _fact_errors(
            repo, evidence="見た感じそうなっていた。")

    @pytest.mark.parametrize("evidence", [
        "exp0001 の OOF を装置ごとに集計した。",
        "https://example.invalid/dataset-doc に記載がある。",
        "地固めの s0002 に同じ記述がある。",
    ])
    def test_recheckable_evidence_passes(self, repo: Path, evidence: str) -> None:
        assert "domain.unconfirmable_evidence" not in _fact_errors(repo, evidence=evidence)

    def test_likely_does_not_need_hard_evidence(self, repo: Path) -> None:
        """読んだだけなら likely。それ自体は責めない。"""
        assert "domain.unconfirmable_evidence" not in _fact_errors(
            repo, confidence="likely", evidence="領域の教科書に記載がある。")

    def test_thin_implication_is_rejected(self, repo: Path) -> None:
        """打ち手にどう効くかが書けない事実は雑学。"""
        assert "domain.implication_too_thin" in _fact_errors(repo, implication="重要。")

    def test_anthropomorphism_is_rejected(self, repo: Path) -> None:
        assert "vocab.metaphor" in _fact_errors(repo, statement="モデルは高齢の患者が苦手。")

    @pytest.mark.parametrize(("field", "value"), [
        ("source", "hearsay"), ("confidence", "probably"),
    ])
    def test_enums(self, repo: Path, field: str, value: str) -> None:
        assert "schema.enum" in _fact_errors(repo, **{field: value})

    def test_nonexistent_experiment_in_evidence(self, repo: Path) -> None:
        assert "link.experiment_missing" in _fact_errors(
            repo, evidence="exp9999 の OOF を集計した。")

    def test_duplicate_id(self, repo: Path) -> None:
        doc = valid_domain()
        doc["facts"][1]["id"] = doc["facts"][0]["id"]
        assert "domain.duplicate_id" in _errors(repo, doc)


class TestAssumedFacts:
    """未検証の仮定を抱えたまま打ち切らせない。"""

    def test_assumed_are_collected(self, repo: Path) -> None:
        doc = valid_domain()
        doc["facts"][4]["confidence"] = "assumed"
        assert [f["id"] for f in assumed_facts(doc)] == ["d0005"]

    def test_assumed_blocks_stop(self, repo: Path) -> None:
        doc = valid_domain()
        doc["facts"][4]["confidence"] = "assumed"
        _set(repo, doc)
        assert "stop.assumed_facts_remain" in _gates(gate_stop_decision(load_state(repo)))

    def test_no_assumed_does_not_block(self, repo: Path) -> None:
        _set(repo, valid_domain())
        assert "stop.assumed_facts_remain" not in _gates(
            gate_stop_decision(load_state(repo)))


class TestReconIncludesDomain:
    def test_missing_domain_blocks_recon(self, repo: Path) -> None:
        _set(repo, {"facts": []})
        assert "recon.domain_incomplete" in _gates(recon_findings(load_state(repo)))

    def test_complete_domain_passes(self, repo: Path) -> None:
        _set(repo, valid_domain())
        assert "recon.domain_incomplete" not in _gates(recon_findings(load_state(repo)))


class TestInconclusiveStreak:
    """判定できない実験が続いたら、闇雲に次を撃たせない。"""

    def _close(self, repo: Path, results: list[str]) -> None:
        for i, r in enumerate(results, start=1):
            (repo / "decisions" / f"dec{i:04d}.yaml").write_text(yaml.safe_dump({
                "id": f"dec{i:04d}", "type": "run", "chosen": "o1",
                "outcome": {"result": r, "experiment": "exp0001"},
            }, allow_unicode=True), encoding="utf-8")

    def test_counts_only_the_trailing_run(self, repo: Path) -> None:
        self._close(repo, ["inconclusive", "hit", "inconclusive", "inconclusive"])
        assert inconclusive_streak(load_state(repo)) == 2

    def test_streak_forces_error_analysis(self, repo: Path) -> None:
        self._close(repo, ["inconclusive"] * 3)
        gates = _gates(gate_new_experiment(load_state(repo), tier="explore",
                                           axes=["features"]))
        assert "stuck.inconclusive_streak" in gates

    def test_error_analysis_axis_satisfies_it(self, repo: Path) -> None:
        self._close(repo, ["inconclusive"] * 3)
        gates = _gates(gate_new_experiment(load_state(repo), tier="explore",
                                           axes=["error_analysis"]))
        assert "stuck.inconclusive_streak" not in gates

    def test_a_hit_resets_the_streak(self, repo: Path) -> None:
        self._close(repo, ["inconclusive", "inconclusive", "inconclusive", "hit"])
        assert inconclusive_streak(load_state(repo)) == 0
        assert "stuck.inconclusive_streak" not in _gates(
            gate_new_experiment(load_state(repo), tier="explore", axes=["features"]))
