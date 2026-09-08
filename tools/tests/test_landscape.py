"""地固めの検証と、それを前提にするゲート。

knowledge/priors/ は汎用の打ち手で、どのコンペでも同じ内容になる。
汎用の在庫からは汎用のアイデアしか出ないので、
このコンペ固有の外部知識が入るまで modeling 系の軸に進ませない。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import valid_landscape
from expkit.config import load_lint_config
from expkit.gates import (
    RECON_ALLOWED_AXES,
    gate_new_experiment,
    gate_stop_decision,
    policy,
    recon_done,
)
from expkit.lint import lint_landscape
from expkit.state import load_state


def _set(repo: Path, doc: dict) -> None:
    (repo / "knowledge" / "landscape.yaml").write_text(
        yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")


def _errors(repo: Path, doc: dict) -> list[str]:
    state = load_state(repo)
    per_source, whole = lint_landscape(
        doc, cfg=load_lint_config(repo), policy=policy(state),
        known_ideas=state.idea_ids,
    )
    return [f.rule for r in [*per_source, whole] for f in r.errors]


def _gates(findings: list) -> set[str]:
    return {f.gate for f in findings if f.severity == "error"}


class TestLandscapeLint:
    def test_well_formed_passes(self, repo: Path) -> None:
        assert _errors(repo, valid_landscape()) == []

    def test_empty_task_is_rejected(self, repo: Path) -> None:
        doc = valid_landscape()
        doc["task"]["formulation"] = ""
        assert "schema.missing" in _errors(repo, doc)

    def test_too_few_sources(self, repo: Path) -> None:
        assert "landscape.too_few_sources" in _errors(repo, valid_landscape(n_sources=2))

    def test_one_kind_only_is_rejected(self, repo: Path) -> None:
        """論文だけでは実装の勘所が分からず、解法だけでは原理が分からない。"""
        doc = valid_landscape()
        for src in doc["sources"]:
            src["kind"] = "paper"
        assert "landscape.one_kind_only" in _errors(repo, doc)

    def test_not_transferred_is_rejected(self, repo: Path) -> None:
        """調べただけで打ち手にしていない状態を通さない。"""
        doc = valid_landscape(n_transferred=0)
        assert "landscape.not_transferred" in _errors(repo, doc)

    def test_thin_takeaway_is_rejected(self, repo: Path) -> None:
        doc = valid_landscape()
        doc["sources"][0]["takeaway"] = "良い結果を報告している。"
        assert "landscape.takeaway_too_thin" in _errors(repo, doc)

    @pytest.mark.parametrize("url", ["example.com/paper", "arXiv:2401.00001", "TODO"])
    def test_bad_url_is_rejected(self, repo: Path, url: str) -> None:
        """開いた URL をそのまま貼らせる。捏造の余地を減らす。"""
        doc = valid_landscape()
        doc["sources"][0]["url"] = url
        assert "landscape.bad_url" in _errors(repo, doc)

    def test_unknown_kind_is_rejected(self, repo: Path) -> None:
        doc = valid_landscape()
        doc["sources"][0]["kind"] = "tweet"
        assert "schema.enum" in _errors(repo, doc)

    def test_duplicate_source_id(self, repo: Path) -> None:
        doc = valid_landscape()
        doc["sources"][1]["id"] = doc["sources"][0]["id"]
        assert "landscape.duplicate_id" in _errors(repo, doc)

    def test_transferred_to_must_exist_in_backlog(self, repo: Path) -> None:
        doc = valid_landscape()
        doc["sources"][0]["transferred_to"] = ["i9999"]
        assert "link.idea_missing" in _errors(repo, doc)

    def test_vocabulary_applies_to_takeaway(self, repo: Path) -> None:
        doc = valid_landscape()
        doc["sources"][0]["takeaway"] = "この前処理がかなり効いたと報告されているので有望。"
        rules = _errors(repo, doc)
        assert "vocab.metaphor" in rules or "vocab.vague_quantity" in rules


class TestReconGate:
    """調べる前に手を動かさせない。"""

    def test_modeling_axes_are_blocked_before_recon(self, repo: Path) -> None:
        _set(repo, valid_landscape(n_sources=1))
        gates = _gates(gate_new_experiment(load_state(repo), tier="explore",
                                           axes=["features"]))
        assert "recon.landscape_incomplete" in gates
        assert "recon.axis_blocked" in gates

    @pytest.mark.parametrize("axis", sorted(RECON_ALLOWED_AXES))
    def test_groundwork_axes_are_allowed_before_recon(self, repo: Path, axis: str) -> None:
        """指標の一致・CV戦略・リーク調査は、調べる前でも進めてよい。"""
        _set(repo, valid_landscape(n_sources=1))
        gates = _gates(gate_new_experiment(load_state(repo), tier="explore", axes=[axis]))
        assert "recon.axis_blocked" not in gates

    def test_modeling_axes_open_up_after_recon(self, repo: Path) -> None:
        _set(repo, valid_landscape())
        state = load_state(repo)
        assert recon_done(state)
        assert "recon.axis_blocked" not in _gates(
            gate_new_experiment(state, tier="explore", axes=["features"]))

    def test_stop_is_blocked_before_recon(self, repo: Path) -> None:
        """調べていないなら、打ち手が無いのではなく知らないだけ。"""
        _set(repo, valid_landscape(n_sources=1))
        assert "stop.recon_incomplete" in _gates(gate_stop_decision(load_state(repo)))


class TestEvidenceFromLandscape:
    def test_source_id_counts_as_evidence(self, repo: Path) -> None:
        """調べた出典を根拠にしたアイデアが在庫に入る。"""
        from expkit.lint import lint_idea

        idea = {
            "id": "i0100",
            "action": "患者単位の StratifiedGroupKFold に切り替え、"
                      "画像単位分割との CV-LB gap を比べる。",
            "axes": ["validation"], "tier": "explore",
            "expected": {"metric": "cv.mean", "direction": "increase", "magnitude": 0.01},
            "evidence": "s0003", "cost": 0.8, "status": "open",
        }
        report = lint_idea(idea, cfg=load_lint_config(repo), index=0,
                           known_axes=load_state(repo).axis_ids)
        assert "idea.evidence_without_source" not in [f.rule for f in report.errors]
