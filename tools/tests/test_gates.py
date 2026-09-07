"""探索の質を守るゲートが効いているかを確かめる。

守る対象は三つ。
  - 弱い根拠での打ち切りを許さない
  - ノイズを結論と読み替えさせない
  - 保守化を許さない
"""

from __future__ import annotations

from pathlib import Path

import yaml

from conftest import base_record, make_experiment
from expkit.gates import (
    calibration,
    conservatism_findings,
    cv_trust,
    gate_backlog,
    gate_new_experiment,
    gate_stop_decision,
    judge,
)
from expkit.state import load_state


def _gates(findings: list) -> set[str]:
    return {f.gate for f in findings if f.severity == "error"}


def _set(repo: Path, path: str, doc: dict) -> None:
    (repo / path).write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                             encoding="utf-8")


class TestSaturation:
    """試し尽くした軸には、もう手を出せない。"""

    def test_saturated_axis_blocks_exploit(self, repo: Path) -> None:
        _set(repo, "knowledge/coverage.yaml", {"axes": [
            {"id": "features", "status": "saturated"},
            {"id": "ensemble", "status": "untouched"},
        ]})
        state = load_state(repo)
        assert "saturation.exhausted" in _gates(
            gate_new_experiment(state, tier="exploit", axes=["features"]))

    def test_saturated_axis_allows_explore(self, repo: Path) -> None:
        """飽和判定そのものを覆す実験は explore として立てられる。"""
        _set(repo, "knowledge/coverage.yaml", {"axes": [{"id": "features", "status": "saturated"}]})
        state = load_state(repo)
        assert "saturation.exhausted" not in _gates(
            gate_new_experiment(state, tier="explore", axes=["features"]))

    def test_retired_axis_blocks_everything(self, repo: Path) -> None:
        _set(repo, "knowledge/coverage.yaml", {"axes": [{"id": "features", "status": "retired"}]})
        state = load_state(repo)
        assert "saturation.retired" in _gates(
            gate_new_experiment(state, tier="explore", axes=["features"]))


class TestMetricFidelity:
    """指標実装が検証されるまで、他の軸に進めない。"""

    def test_unverified_metric_blocks_other_axes(self, repo: Path) -> None:
        comp = yaml.safe_load((repo / "competition.yaml").read_text(encoding="utf-8"))
        comp["metric"]["verified"] = False
        _set(repo, "competition.yaml", comp)
        state = load_state(repo)
        assert "phase.metric_unverified" in _gates(
            gate_new_experiment(state, tier="explore", axes=["features"]))

    def test_metric_axis_itself_is_allowed(self, repo: Path) -> None:
        comp = yaml.safe_load((repo / "competition.yaml").read_text(encoding="utf-8"))
        comp["metric"]["verified"] = False
        _set(repo, "competition.yaml", comp)
        state = load_state(repo)
        assert "phase.metric_unverified" not in _gates(
            gate_new_experiment(state, tier="explore", axes=["metric_fidelity"]))


class TestBacklog:
    """在庫が空でない限り「打ち手がない」と言えない。"""

    def test_thin_backlog_blocks(self, repo: Path) -> None:
        _set(repo, "ideas/backlog.yaml", {"ideas": []})
        assert "backlog.thin" in _gates(gate_backlog(load_state(repo)))

    def test_done_ideas_do_not_count(self, repo: Path) -> None:
        _set(repo, "ideas/backlog.yaml", {"ideas": [
            {"id": "i1", "status": "done"}, {"id": "i2", "status": "retired"},
        ]})
        assert "backlog.thin" in _gates(gate_backlog(load_state(repo)))


class TestStopDecision:
    """弱い根拠での打ち切りを潰す本体。"""

    def test_unverified_metric_blocks_stop(self, repo: Path) -> None:
        comp = yaml.safe_load((repo / "competition.yaml").read_text(encoding="utf-8"))
        comp["metric"]["verified"] = False
        _set(repo, "competition.yaml", comp)
        assert "stop.metric_unverified" in _gates(gate_stop_decision(load_state(repo)))

    def test_untrusted_cv_blocks_stop(self, repo: Path) -> None:
        """CV-LB のペアが足りないうちは CV を根拠にできない。"""
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record("exp0001"))
        assert "stop.cv_untrusted" in _gates(gate_stop_decision(load_state(repo)))

    def test_untouched_axes_block_stop(self, repo: Path) -> None:
        assert "stop.untouched_axes" in _gates(gate_stop_decision(load_state(repo)))

    def test_remaining_ideas_block_stop(self, repo: Path) -> None:
        assert "stop.backlog_not_empty" in _gates(gate_stop_decision(load_state(repo)))


class TestCvTrust:
    def test_few_pairs_is_unverified(self, repo: Path) -> None:
        from expkit import metrics as m
        for i, (cv, lb) in enumerate([(0.87, 0.86), (0.88, 0.87)], start=1):
            make_experiment(repo, f"exp{i:04d}", cv_mean=cv, record=base_record(f"exp{i:04d}"))
            m.annotate(f"exp{i:04d}", path="metrics.lb.public", value=lb, by="t", root=repo)
        assert cv_trust(load_state(repo)).status == "unverified"

    def test_anticorrelated_cv_is_broken(self, repo: Path) -> None:
        """CV が上がるほど LB が下がるなら、CV は採点を再現していない。"""
        from expkit import metrics as m
        pairs = [(0.87, 0.89), (0.88, 0.88), (0.89, 0.87), (0.90, 0.86)]
        for i, (cv, lb) in enumerate(pairs, start=1):
            make_experiment(repo, f"exp{i:04d}", cv_mean=cv, record=base_record(f"exp{i:04d}"))
            m.annotate(f"exp{i:04d}", path="metrics.lb.public", value=lb, by="t", root=repo)
        assert cv_trust(load_state(repo)).status == "broken"


class TestJudge:
    """ノイズを結論と読み替えさせない。"""

    def _decision(self, magnitude: float, metric: str = "cv.mean") -> dict:
        return {
            "id": "dec0001", "type": "run", "chosen": "o1",
            "options": [{"id": "o1", "action": "x", "expected": {
                "metric": metric, "direction": "increase", "magnitude": magnitude}}],
        }

    def test_below_noise_is_inconclusive(self, repo: Path) -> None:
        """fold の標準誤差を下回る差は「効かなかった」ではない。"""
        make_experiment(repo, "exp0001", cv_mean=0.8700, cv_std=0.0042,
                        record=base_record("exp0001"))
        make_experiment(repo, "exp0002", cv_mean=0.8708, cv_std=0.0042,
                        record=base_record("exp0002", based_on="exp0001"))
        state = load_state(repo)
        exp = next(e for e in state.experiments if e.id == "exp0002")
        j = judge(state, self._decision(0.003), exp)
        assert j.result == "inconclusive"
        assert j.noise > abs(j.delta)

    def test_meeting_expectation_is_hit(self, repo: Path) -> None:
        make_experiment(repo, "exp0001", cv_mean=0.8700, cv_std=0.0042,
                        record=base_record("exp0001"))
        make_experiment(repo, "exp0002", cv_mean=0.8760, cv_std=0.0042,
                        record=base_record("exp0002", based_on="exp0001"))
        state = load_state(repo)
        exp = next(e for e in state.experiments if e.id == "exp0002")
        assert judge(state, self._decision(0.003), exp).result == "hit"

    def test_wrong_direction_is_miss(self, repo: Path) -> None:
        make_experiment(repo, "exp0001", cv_mean=0.8700, cv_std=0.0042,
                        record=base_record("exp0001"))
        make_experiment(repo, "exp0002", cv_mean=0.8600, cv_std=0.0042,
                        record=base_record("exp0002", based_on="exp0001"))
        state = load_state(repo)
        exp = next(e for e in state.experiments if e.id == "exp0002")
        assert judge(state, self._decision(0.003), exp).result == "miss"

    def test_lb_uses_competition_noise(self, repo: Path) -> None:
        """LB のノイズ幅は competition.yaml の lb_noise（0.001）を使う。"""
        from expkit import metrics as m
        for eid, cv, lb in [("exp0001", 0.87, 0.8700), ("exp0002", 0.88, 0.8705)]:
            based = "exp0001" if eid == "exp0002" else None
            make_experiment(repo, eid, cv_mean=cv, record=base_record(eid, based_on=based))
            m.annotate(eid, path="metrics.lb.public", value=lb, by="t", root=repo)
        state = load_state(repo)
        exp = next(e for e in state.experiments if e.id == "exp0002")
        j = judge(state, self._decision(0.002, metric="lb.public"), exp)
        assert j.result == "inconclusive"
        assert j.noise == 0.001


class TestConservatism:
    """消極性そのものを検出する。"""

    def _closed(self, repo: Path, results: list[str], ratio: float = 1.0) -> None:
        for i, r in enumerate(results, start=1):
            _set(repo, f"decisions/dec{i:04d}.yaml", {
                "id": f"dec{i:04d}", "type": "run", "chosen": "o1",
                "outcome": {"result": r, "ratio": ratio, "experiment": "exp0001"},
            })

    def test_high_hit_rate_warns(self, repo: Path) -> None:
        self._closed(repo, ["hit"] * 9 + ["miss"])
        gates = {f.gate for f in conservatism_findings(load_state(repo))}
        assert "conservatism.hit_rate_too_high" in gates

    def test_underbidding_warns(self, repo: Path) -> None:
        self._closed(repo, ["hit", "miss", "hit", "miss", "hit"], ratio=3.0)
        gates = {f.gate for f in conservatism_findings(load_state(repo))}
        assert "conservatism.underbidding" in gates

    def test_all_exploit_is_error(self, repo: Path) -> None:
        for i in range(1, 11):
            make_experiment(repo, f"exp{i:04d}", cv_mean=0.87,
                            record=base_record(f"exp{i:04d}", tier="exploit"))
        gates = {f.gate for f in conservatism_findings(load_state(repo))}
        assert "conservatism.exploit_only" in gates

    def test_calibration_counts(self, repo: Path) -> None:
        self._closed(repo, ["hit", "hit", "miss", "inconclusive"])
        cal = calibration(load_state(repo))
        assert (cal.hit, cal.miss, cal.inconclusive) == (2, 1, 1)
        assert cal.hit_rate == 2 / 3   # inconclusive は分母に入れない
