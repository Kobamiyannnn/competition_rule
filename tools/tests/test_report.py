"""レポートが記録から機械的に組み上がること、結論欄に規範がかかることを確かめる。"""

from __future__ import annotations

from pathlib import Path

from conftest import base_record, make_experiment
from expkit.config import load_lint_config
from expkit.lint import Report
from expkit.report import build, lint_conclusion
from expkit.state import load_state


def _conclusion_errors(repo: Path, text: str) -> list[str]:
    r = Report(target="reports/conclusion.md")
    lint_conclusion(text, load_lint_config(repo), r)
    return [f.rule for f in r.errors]


class TestConclusion:
    def test_fat_conclusion_is_rejected(self, repo: Path) -> None:
        text = ("今回のコンペでは、特徴量エンジニアリングが全体として重要である"
                "ことが示唆された。特にユーザ単位の集約が有効に機能し、大幅な"
                "スコア向上につながったと思われる。")
        rules = _conclusion_errors(repo, text)
        assert "vocab.hedge" in rules
        assert "vocab.filler" in rules
        assert "vocab.vague_quantity" in rules

    def test_factual_conclusion_passes(self, repo: Path) -> None:
        text = ("提出は LightGBM と CatBoost の等重量平均。"
                "cv.mean を最も動かしたのはユーザ単位の集約特徴と閾値最適化。"
                "疑似ラベルは3回試していずれもノイズ幅未満で判定できていない。"
                "外部データとアンサンブルの重み最適化は未着手のまま締切を迎えた。")
        assert _conclusion_errors(repo, text) == []

    def test_numbers_are_allowed(self, repo: Path) -> None:
        """結論は特定の実験に紐づかないので ${...} が使えない。数値は許す。"""
        assert _conclusion_errors(repo, "提出の cv.mean は 0.8901、LB は 0.8874。") == []

    def test_vocabulary_is_still_strict(self, repo: Path) -> None:
        """数値を許しても語彙は緩めない。曖昧さこそが防ぎたい失敗。"""
        assert "vocab.metaphor" in _conclusion_errors(repo, "集約特徴が効いた。")

    def test_length_limit(self, repo: Path) -> None:
        assert "length.exceeded" in _conclusion_errors(repo, "あ" * 700)


class TestBuild:
    def test_report_contains_every_section(self, repo: Path) -> None:
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record("exp0001"))
        text, creport = build(load_state(repo), repo)
        for section in ("## 結論", "## コンペ", "## 検証の信頼性", "## 実験",
                        "## 探索の網羅", "## 意思決定の台帳", "## 仮説",
                        "## 未実行のアイデア在庫"):
            assert section in text
        assert creport is None      # conclusion.md がまだ無い

    def test_untouched_axes_are_stated_as_remaining_moves(self, repo: Path) -> None:
        """未着手の軸は「打ち手が尽きていない」証拠としてレポートに出る。"""
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record("exp0001"))
        text, _ = build(load_state(repo), repo)
        assert "打ち手が尽きたのではなく、まだ触っていない" in text

    def test_conclusion_is_linted_when_present(self, repo: Path) -> None:
        (repo / "reports").mkdir()
        (repo / "reports" / "conclusion.md").write_text(
            "全体として大幅に改善したと思われる。", encoding="utf-8")
        make_experiment(repo, "exp0001", cv_mean=0.87, record=base_record("exp0001"))
        _text, creport = build(load_state(repo), repo)
        assert creport is not None and creport.errors
