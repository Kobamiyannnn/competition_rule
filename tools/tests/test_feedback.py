"""基盤そのものへのフィードバック。

docs/open-questions.md の数値は1コンペも回さずに置いた当て推量なので、
使ってみて初めて当たり外れが分かる。そのとき感想ではなく回数で判断できるよう、
摩擦を記録して取り出せることを固定する。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import base_record, make_experiment
from expkit import events
from expkit.config import load_lint_config
from expkit.feedback import build
from expkit.lint import lint_proposal
from expkit.state import load_state

PROPOSAL = {
    "id": "p0001",
    "kind": "missing_capability",
    "at": ["exp0001"],
    "incident": "fold ごとに別のモデルを使う構成を試したが、config が1モデル前提で、"
                "change.diff に差分を表現できなかった。",
    "missing": "config に fold 別の設定を持てる形が無い。",
    "workaround": "fold 番号を config のキーに含めて回避したが、diff が読めなくなった。",
    "status": "open",
}


def _errors(repo: Path, **over: object) -> list[str]:
    state = load_state(repo)
    report = lint_proposal(
        {**PROPOSAL, **over}, cfg=load_lint_config(repo), index=0,
        known_experiments=state.experiment_ids, known_decisions=state.decision_ids,
    )
    return [f.rule for f in report.errors]


@pytest.fixture()
def repo_with_experiment(repo: Path) -> Path:
    make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record("exp0001"))
    return repo


class TestProposalGrounding:
    """提案は実際に起きた出来事に紐づいていること。ここが質を決める。"""

    def test_well_formed_passes(self, repo_with_experiment: Path) -> None:
        assert _errors(repo_with_experiment) == []

    def test_without_a_scene_it_is_a_wish(self, repo_with_experiment: Path) -> None:
        assert "proposal.not_grounded" in _errors(repo_with_experiment, at=[])

    def test_nonexistent_experiment_is_rejected(self, repo_with_experiment: Path) -> None:
        assert "link.experiment_missing" in _errors(repo_with_experiment, at=["exp9999"])

    def test_reference_must_be_an_id(self, repo_with_experiment: Path) -> None:
        assert "proposal.bad_reference" in _errors(
            repo_with_experiment, at=["さっきの実験"])

    def test_thin_incident_is_rejected(self, repo_with_experiment: Path) -> None:
        """「使いにくかった」で終わらせない。"""
        assert "proposal.incident_too_thin" in _errors(
            repo_with_experiment, incident="使いにくかった")

    def test_unknown_kind_is_rejected(self, repo_with_experiment: Path) -> None:
        assert "schema.enum" in _errors(repo_with_experiment, kind="要望")

    def test_filed_needs_a_url(self, repo_with_experiment: Path) -> None:
        assert "schema.missing" in _errors(repo_with_experiment, status="filed")

    def test_vocabulary_applies(self, repo_with_experiment: Path) -> None:
        assert "vocab.vague_quantity" in _errors(
            repo_with_experiment,
            incident="この規則が大幅に邪魔をして、実験を進めるのがかなり難しくなった場面があった。")


class TestEvents:
    def test_records_and_loads(self, repo: Path) -> None:
        events.record(repo, "gate_blocked", gate="backlog.thin", command="new")
        loaded = events.load(repo)
        assert len(loaded) == 1
        assert loaded[0]["gate"] == "backlog.thin"
        assert loaded[0]["at"]

    def test_survives_a_broken_line(self, repo: Path) -> None:
        events.record(repo, "gate_blocked", gate="a")
        events.path(repo).write_text(
            events.path(repo).read_text(encoding="utf-8") + "壊れた行\n", encoding="utf-8")
        events.record(repo, "gate_blocked", gate="b")
        assert [e["gate"] for e in events.load(repo)] == ["a", "b"]

    def test_can_be_disabled(self, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXPKIT_NO_EVENTS", "1")
        events.record(repo, "gate_blocked", gate="a")
        assert events.load(repo) == []

    def test_is_not_tracked_by_git(self) -> None:
        """実験の記録ではないので git には入れない。"""
        import subprocess

        root = Path(__file__).resolve().parents[2]
        out = subprocess.run(["git", "check-ignore", "-q", ".expkit/events.jsonl"],
                             cwd=root, capture_output=True)
        assert out.returncode == 0, ".expkit が追跡対象になっている"


class TestReport:
    def test_contains_every_section(self, repo_with_experiment: Path) -> None:
        text = build(load_state(repo_with_experiment))
        for section in ("### 環境", "### 当て推量で置いた設定値",
                        "### 語彙・記述の規則", "### ゲート", "### 改善の提案"):
            assert section in text

    def test_counts_gate_blocks(self, repo_with_experiment: Path) -> None:
        for _ in range(3):
            events.record(repo_with_experiment, "gate_blocked",
                          gate="backlog.thin", command="new")
        text = build(load_state(repo_with_experiment))
        assert "backlog.thin" in text and "| 3 " in text

    def test_deduplicates_repeated_lint_on_the_same_place(
        self, repo_with_experiment: Path
    ) -> None:
        """同じファイルを何度保存しても hook が走る。数えたいのは箇所数。"""
        for _ in range(5):
            events.record(repo_with_experiment, "lint_blocked",
                          rule="vocab.hedge", target="experiments/exp0001/record.yaml")
        text = build(load_state(repo_with_experiment))
        line = next(ln for ln in text.splitlines() if "vocab.hedge" in ln)
        assert "| 1 " in line, f"重複が除かれていない: {line}"

    def test_surfaces_waiver_reasons(self, repo_with_experiment: Path) -> None:
        make_experiment(repo_with_experiment, "exp0002", cv_mean=0.87, record=base_record(
            "exp0002",
            lint_waived=[{"rule": "vocab.metaphor",
                          "reason": "「相性」は公式ドキュメントで定義された用語で比喩ではない。"}]))
        text = build(load_state(repo_with_experiment))
        assert "vocab.metaphor" in text
        assert "公式ドキュメントで定義された用語" in text

    def test_includes_open_proposals(self, repo_with_experiment: Path) -> None:
        (repo_with_experiment / "feedback").mkdir(exist_ok=True)
        (repo_with_experiment / "feedback" / "proposals.yaml").write_text(
            yaml.safe_dump({"proposals": [PROPOSAL]}, allow_unicode=True), encoding="utf-8")
        text = build(load_state(repo_with_experiment))
        assert "p0001" in text
        assert "config が1モデル前提" in text

    def test_does_not_leak_competition_data(self, repo_with_experiment: Path) -> None:
        """規則名・ゲート名・回数だけ。private なコンペでも公開先に貼れること。"""
        text = build(load_state(repo_with_experiment))
        assert "0.87" not in text, "メトリクスの値が漏れている"

    def test_issue_mode_changes_the_heading(self, repo_with_experiment: Path) -> None:
        state = load_state(repo_with_experiment)
        assert build(state, as_issue=True).startswith("## ")
        assert build(state, as_issue=False).startswith("# ")
