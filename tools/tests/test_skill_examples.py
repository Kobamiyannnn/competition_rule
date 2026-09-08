"""スキルに載せた良い例・悪い例が、実際の linter の判定と一致することを固定する。

規範の文書と実装がずれると、エージェントは文書に従って書き、linter に落とされる。
落とされ続けると抜け道を探し始める。だからここは固定しておく。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from expkit.config import load_lint_config
from expkit.lint import Report, lint_prose_field

LOG_EXPERIMENT = ".claude/skills/log-experiment/SKILL.md"
WRITE_REPORT = ".claude/skills/write-report/SKILL.md"
SURVEY = ".claude/skills/survey/SKILL.md"
LEARN_DOMAIN = ".claude/skills/learn-domain/SKILL.md"

# (欄, 本文, 落ちるべきか, 出典)
CASES = [
    (
        "record.change.summary",
        "特徴量を見直して、より効果的な集約を追加した。",
        True, f"{LOG_EXPERIMENT} change 悪い例",
    ),
    (
        "record.change.summary",
        "ユーザ単位の集約特徴（購入額の mean / std / count）を12列追加した。"
        "他の設定は変更していない。",
        False, f"{LOG_EXPERIMENT} change 良い例",
    ),
    (
        "record.observation",
        "CV が 0.8712 まで大幅に改善した。この特徴量がよく効いている印象。",
        True, f"{LOG_EXPERIMENT} observation 悪い例",
    ),
    (
        "record.observation",
        "cv.mean は ${metrics.cv.mean}、fold 間の std は ${metrics.cv.std}。"
        "fold 別では最小 ${metrics.cv.folds[0]}、最大 ${metrics.cv.folds[3]}。",
        False, f"{LOG_EXPERIMENT} observation 良い例",
    ),
    (
        "record.hypothesis.falsification",
        "集約の単位をユーザから乱数グループに置き換えて同じ12列を作り、"
        "cv.mean が ${metrics.cv.std} を超えて下がらないなら、この仮説は誤り。",
        False, f"{LOG_EXPERIMENT} falsification 良い例",
    ),
    (
        "landscape.task.formulation",
        "不均衡二値分類。患者単位のグループ構造あり、ラベルは追跡打ち切りを含む。",
        False, f"{SURVEY} formulation 良い例",
    ),
    (
        "landscape.source.takeaway",
        "患者単位の StratifiedGroupKFold で切り、陽性を含む患者を層化に使う。"
        "画像単位で切ると CV が 0.05 楽観的になると報告している。",
        False, f"{SURVEY} takeaway 良い例",
    ),
    (
        "landscape.source.takeaway",
        "この前処理がかなり効いたと報告されているので有望。",
        True, f"{SURVEY} takeaway 悪い例（skill が禁じている書き方）",
    ),
    (
        "idea.action",
        "患者単位の StratifiedGroupKFold に切り替え、画像単位分割との CV-LB gap を比べる。",
        False, f"{SURVEY} 在庫への転記 良い例",
    ),
    (
        "domain.fact.statement",
        "モデルは高齢の患者が苦手。",
        True, f"{LEARN_DOMAIN} 事実 悪い例",
    ),
    (
        "domain.fact.statement",
        "誤差の上位10%のうち72%が、撮影装置Bの画像に集中している。",
        False, f"{LEARN_DOMAIN} 事実 良い例",
    ),
    (
        "domain.fact.implication",
        "装置を特徴量に入れるか、装置ごとに正規化する。"
        "test の装置比率が train と違うなら分布ずれの原因でもある。",
        False, f"{LEARN_DOMAIN} implication 良い例",
    ),
    (
        "report.conclusion",
        "今回のコンペでは、特徴量エンジニアリングが全体として重要であることが"
        "示唆された。特にユーザ単位の集約が有効に機能し、大幅なスコア向上に"
        "つながった。今後はさらなる検証が求められる。",
        True, f"{WRITE_REPORT} 結論 悪い例",
    ),
    (
        "report.conclusion",
        "提出は LightGBM と CatBoost の等重量平均。cv.mean 0.8901、LB 0.8874。"
        "cv.mean を最も動かしたのはユーザ単位の集約特徴（exp0004、+0.0043）と"
        "閾値最適化（exp0011、+0.0021）。疑似ラベルは3回試していずれも"
        "ノイズ幅未満で判定できていない（dec0008）。"
        "外部データと ensemble の重み最適化は未着手のまま締切を迎えた。",
        False, f"{WRITE_REPORT} 結論 良い例",
    ),
]


@pytest.mark.parametrize(("field", "text", "should_fail", "source"), CASES,
                         ids=[c[3] for c in CASES])
def test_skill_example_matches_linter(
    repo: Path, field: str, text: str, should_fail: bool, source: str
) -> None:
    report = Report(target=field)
    lint_prose_field(field, field, text, load_lint_config(repo), report)
    rules = sorted({f.rule for f in report.errors})
    assert bool(report.errors) is should_fail, f"{source}: 落ちた規則 ={rules}"


def test_examples_are_still_in_the_skill_files() -> None:
    """例文を書き換えたのにテストを直し忘れる事故を防ぐ。"""
    root = Path(__file__).resolve().parents[2]
    for path, marker in [(LOG_EXPERIMENT, "特徴量を見直して"),
                         (WRITE_REPORT, "cv.mean を最も動かしたのは"),
                         (SURVEY, "StratifiedGroupKFold"),
                         (LEARN_DOMAIN, "撮影装置Bの画像に集中")]:
        assert marker in (root / path).read_text(encoding="utf-8"), path
