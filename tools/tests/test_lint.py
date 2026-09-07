"""記述欄の規範が実際に効いているかを確かめる。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import base_record, make_experiment
from expkit.config import load_lint_config
from expkit.lint import lint_record
from expkit.state import load_state


def _lint(repo: Path, exp_id: str) -> list[str]:
    state = load_state(repo)
    e = next(x for x in state.experiments if x.id == exp_id)
    r = lint_record(
        e.record, cfg=load_lint_config(repo), exp_id=exp_id, root=repo,
        known_experiments=state.experiment_ids, known_axes=state.axis_ids,
    )
    return [f.rule for f in r.errors]


def test_clean_record_passes(repo: Path) -> None:
    make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record("exp0001"))
    assert _lint(repo, "exp0001") == []


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("cv.mean は ${metrics.cv.mean} まで改善したと思われる。", "vocab.hedge"),
        ("cv.mean は ${metrics.cv.mean}。この特徴量が効いている。", "vocab.metaphor"),
        ("cv.mean は ${metrics.cv.mean}。大幅に動いた。", "vocab.vague_quantity"),
        ("cv.mean は ${metrics.cv.mean}。良い結果だった。", "vocab.evaluative"),
        ("cv.mean は ${metrics.cv.mean}。全体として妥当。", "vocab.filler"),
        ("cv.mean は 0.8712 だった。${metrics.cv.std} も見た。", "numeric.bare"),
        ("モデルがユーザ単位の傾向を理解できるようになった。${metrics.cv.mean}", "vocab.metaphor"),
    ],
)
def test_observation_vocabulary(repo: Path, text: str, rule: str) -> None:
    make_experiment(repo, "exp0001", cv_mean=0.87,
                    record=base_record("exp0001", observation=text))
    assert rule in _lint(repo, "exp0001")


def test_observation_needs_measured_reference(repo: Path) -> None:
    """設定値への参照は測定値ではないので、接地の数に入らない。"""
    make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record(
        "exp0001", observation="設定は ${config.model.name} のまま変えていない。"))
    assert "observation.no_ref" in _lint(repo, "exp0001")


def test_unresolved_reference_is_error(repo: Path) -> None:
    make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record(
        "exp0001", observation="cv.mean は ${metrics.cv.stdev}。"))
    assert "ref.unresolved" in _lint(repo, "exp0001")


def test_length_limit(repo: Path) -> None:
    make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record(
        "exp0001", observation="cv.mean は ${metrics.cv.mean}。" + "あ" * 400))
    assert "length.exceeded" in _lint(repo, "exp0001")


class TestFalsification:
    """この基盤の核。反証条件のない仮説を通さない。"""

    def test_missing_is_error(self, repo: Path) -> None:
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record(
            "exp0001", hypothesis=[{"id": "h1", "statement": "集約の単位が寄与している。",
                                    "status": "open"}]))
        assert "falsification.missing" in _lint(repo, "exp0001")

    def test_must_be_conditional(self, repo: Path) -> None:
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record(
            "exp0001", hypothesis=[{
                "id": "h1", "statement": "集約の単位が寄与している。",
                "falsification": "cv.mean が 0.87 を下回る。", "status": "open"}]))
        assert "falsification.not_conditional" in _lint(repo, "exp0001")

    def test_must_be_observable(self, repo: Path) -> None:
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record(
            "exp0001", hypothesis=[{
                "id": "h1", "statement": "集約の単位が寄与している。",
                "falsification": "効かない場合もあるならば誤り。", "status": "open"}]))
        assert "falsification.not_observable" in _lint(repo, "exp0001")

    def test_well_formed_passes(self, repo: Path) -> None:
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record(
            "exp0001", hypothesis=[{
                "id": "h1", "statement": "集約の単位がユーザであることが寄与している。",
                "falsification": "集約の単位を乱数グループに置き換えて "
                                 "cv.mean が ${metrics.cv.std} を超えて下がらないなら誤り。",
                "status": "open", "resolved_by": None}]))
        assert _lint(repo, "exp0001") == []

    def test_settled_needs_resolver(self, repo: Path) -> None:
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record(
            "exp0001", hypothesis=[{
                "id": "h1", "statement": "集約の単位が寄与している。",
                "falsification": "乱数グループに置き換えて cv.mean が下回らないなら誤り。",
                "status": "refuted", "resolved_by": None}]))
        assert "schema.missing" in _lint(repo, "exp0001")


class TestIntegrity:
    def test_hand_edit_detected(self, repo: Path) -> None:
        import json
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record("exp0001"))
        p = repo / "experiments" / "exp0001" / "metrics.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["metrics"]["cv"]["mean"] = 0.99
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        assert "integrity.metrics_tampered" in _lint(repo, "exp0001")

    def test_expctl_lb_keeps_integrity(self, repo: Path) -> None:
        from expkit import metrics as m
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record("exp0001"))
        m.annotate("exp0001", path="metrics.lb.public", value=0.86, by="test", root=repo)
        m.verify("exp0001", repo)   # 例外が飛ばなければよい
        assert _lint(repo, "exp0001") == []


class TestWaiver:
    def test_vocabulary_can_be_waived(self, repo: Path) -> None:
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record(
            "exp0001",
            observation="cv.mean は ${metrics.cv.mean}。特徴量の相性を見た。",
            lint_waived=[{"rule": "vocab.metaphor",
                          "reason": "「相性」は公式ドキュメントで定義された用語で比喩ではない。"}]))
        assert _lint(repo, "exp0001") == []

    def test_falsification_cannot_be_waived(self, repo: Path) -> None:
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record(
            "exp0001",
            hypothesis=[{"id": "h1", "statement": "集約が寄与している。", "status": "open"}],
            lint_waived=[{"rule": "falsification.missing",
                          "reason": "反証条件が思いつかないのでここは飛ばしたい。"}]))
        rules = _lint(repo, "exp0001")
        assert "waiver.forbidden" in rules
        assert "falsification.missing" in rules

    def test_thin_reason_rejected(self, repo: Path) -> None:
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record(
            "exp0001",
            observation="cv.mean は ${metrics.cv.mean}。よく効いた。",
            lint_waived=[{"rule": "vocab.metaphor", "reason": "都合"}]))
        assert "waiver.thin_reason" in _lint(repo, "exp0001")
