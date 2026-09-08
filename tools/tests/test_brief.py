"""引き継ぎが、セッションを捨てても失われない状態を全部拾うことを確かめる。

セッションを切れないのは規律の問題ではなく、切ると失われるものがあるから。
ここが漏れていると、切るのが高くつき、長いセッションに戻る。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from conftest import base_record, make_experiment
from expkit.brief import build, in_flight
from expkit.state import load_state


def _brief(repo: Path) -> str:
    return build(load_state(repo), repo)


class TestInFlight:
    """「いま何をしていたか」はファイルから復元できなければならない。"""

    def test_record_without_metrics_is_in_flight(self, repo: Path) -> None:
        d = repo / "experiments" / "exp0001"
        d.mkdir(parents=True)
        (d / "record.yaml").write_text(
            yaml.safe_dump(base_record("exp0001"), allow_unicode=True), encoding="utf-8")
        pending = in_flight(load_state(repo), repo)
        assert [e.id for e, _ in pending] == ["exp0001"]

    def test_failing_lint_is_in_flight(self, repo: Path) -> None:
        """記録が規範を通っていない実験も、終わっていない扱いにする。"""
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record(
            "exp0001", observation="cv が大幅に改善したと思われる。${metrics.cv.mean}"))
        assert [e.id for e, _ in in_flight(load_state(repo), repo)] == ["exp0001"]

    def test_finished_experiment_is_not_in_flight(self, repo: Path) -> None:
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record("exp0001"))
        assert in_flight(load_state(repo), repo) == []

    def test_notes_survive_into_the_brief(self, repo: Path) -> None:
        """ファイルから復元できない唯一の情報。ここが落ちると切るコストが戻る。"""
        d = repo / "experiments" / "exp0001"
        d.mkdir(parents=True)
        (d / "record.yaml").write_text(yaml.safe_dump(base_record(
            "exp0001", notes="fold 3 まで完了。fold 4 で OOM。batch を 16 にして再実行中。"),
            allow_unicode=True), encoding="utf-8")
        assert "fold 4 で OOM" in _brief(repo)


class TestBriefContent:
    def test_states_what_is_allowed_now(self, repo: Path) -> None:
        text = _brief(repo)
        assert "## いま取れる手" in text
        assert "未着手の軸（打ち手はまだある）" in text

    def test_open_decisions_are_listed(self, repo: Path) -> None:
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record("exp0001"))
        (repo / "decisions" / "dec0001.yaml").write_text(yaml.safe_dump({
            "id": "dec0001", "type": "run", "question": "木を深くするか。",
            "options": [{"id": "o1", "action": "深くする。", "expected": {
                "metric": "cv.mean", "direction": "increase", "magnitude": 0.002}}],
            "chosen": "o1", "outcome": None,
        }, allow_unicode=True), encoding="utf-8")
        text = _brief(repo)
        assert "答え合わせ待ちの決定" in text and "dec0001" in text

    def test_closed_decisions_are_not_listed(self, repo: Path) -> None:
        (repo / "decisions" / "dec0001.yaml").write_text(yaml.safe_dump({
            "id": "dec0001", "type": "run", "question": "x", "chosen": "o1",
            "outcome": {"result": "hit", "experiment": "exp0001"},
        }, allow_unicode=True), encoding="utf-8")
        assert "答え合わせ待ちの決定" not in _brief(repo)

    def test_operations_instructions_are_not_injected(self, repo: Path) -> None:
        """operations.md の「書き方の説明」は毎回注入しても情報が増えない。"""
        (repo / "knowledge" / "operations.md").write_text(
            "# 運用上の状態\n\n書き方の説明。これは引き継ぎに載せない。\n\n---\n\n"
            "## コマンド\n\npython3 src/run.py --exp <id>  （40分）\n",
            encoding="utf-8")
        text = _brief(repo)
        assert "python3 src/run.py" in text
        assert "書き方の説明" not in text

    def test_stays_small_enough_to_inject_every_session(self, repo: Path) -> None:
        """毎回のセッション開始に注入されるので、長さが効く。"""
        for i in range(1, 21):
            make_experiment(repo, f"exp{i:04d}", cv_mean=0.87,
                            record=base_record(f"exp{i:04d}"))
        assert len(_brief(repo)) < 6000
