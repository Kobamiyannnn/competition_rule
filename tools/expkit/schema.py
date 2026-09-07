"""record / decision / idea のスキーマ定義。

スキーマはこの基盤の本体そのもの。欄を増やすと LLM は必ず埋めようとし、
埋める材料がないときは水増しする。だから欄は増やさない。
"""

from __future__ import annotations

TIERS = ("exploit", "explore", "moonshot")
HYPOTHESIS_STATUS = ("open", "supported", "refuted")
DECISION_TYPES = ("run", "stop", "pivot")
DECISION_RESULTS = ("hit", "miss", "inconclusive")
DIRECTIONS = ("increase", "decrease")

# 探索の網羅マップの既定の軸。init-competition がコンペに合わせて足し引きする。
# 「打ち手がない」と言えるのは、ここが全部埋まったときだけ。
DEFAULT_AXES = (
    "metric_fidelity",   # 公式評価指標と自前実装の一致
    "validation",        # CV 戦略そのもの
    "preprocessing",
    "features",
    "model_family",
    "objective",         # 損失関数・目的関数
    "hyperparameters",
    "postprocessing",
    "ensemble",
    "external_data",
    "pseudo_labeling",
    "data_leak",         # リーク・重複・分布ずれの調査
)

RECORD_FIELDS = {
    "required": ("id", "created_at", "tier", "change", "observation"),
    "optional": (
        "based_on", "decision", "idea", "axes", "hypothesis", "lint_waived", "notes",
    ),
}

DECISION_FIELDS = {
    "required": ("id", "created_at", "type", "question", "options", "chosen", "rationale"),
    "optional": ("outcome", "lint_waived"),
}

IDEA_FIELDS = {
    "required": ("id", "created_at", "action", "axes", "tier", "expected", "evidence", "cost"),
    "optional": ("status", "source", "experiment", "retired_reason"),
}

IDEA_STATUS = ("open", "running", "done", "retired")


def record_template(exp_id: str, created_at: str) -> str:
    return f"""\
# 実験記録。数値は書かない。metrics.json への ${{metrics....}} 参照で書く。
# 字数上限と語彙規則がかかる。`expctl lint {exp_id}` で確認する。
id: {exp_id}
created_at: {created_at}

# exploit=既存の勝ち筋の改良 / explore=未検証の方向 / moonshot=外れる確率が高いが当たれば大きい
tier: explore

# 差分の基準になる実験。最初の実験なら null。
based_on: null
# この実験を生んだ決定とアイデア（あれば）。
decision: null
idea: null

# 触った軸。knowledge/coverage.yaml の axes から選ぶ。
axes: []

change:
  # 何を変えたか。前実験との差分だけ。良し悪しは書かない。200字。
  summary: |
    TODO
  # 機械可読な差分。任意だが、あると report が正確になる。
  diff: []
  # - path: model.num_leaves
  #   from: 31
  #   to: 63

# 何が起きたか。300字。裸の数値は禁止、${{metrics....}} を最低1つ含める。
observation: |
  TODO ${{metrics.cv.mean}}

# なぜだと考えるか。空でよい。書くなら falsification が必須。
# falsification を書けない考えは、この欄に書かない。
hypothesis: []
# - id: h1
#   statement: |
#     TODO
#   falsification: |
#     TODO（「〜なら〜のはず」の形で、観測可能な条件を書く）
#   status: open
#   resolved_by: null

# 実行上の状態だけを書く。セッションを切っても失われないようにするための欄。
# 「fold 3 で OOM。batch を 16 にして再実行中」のような、次に開いた人が
# 同じ場所から再開するのに要る情報。**結果の解釈は書かない**（それは observation）。
# 実験が終わったら消してよい。200字。
notes: null

# lint をどうしても通したいときの抜け道。使ったこと自体が記録に残る。
lint_waived: []
# - rule: vocab.metaphor
#   reason: |
#     TODO
"""


def decision_template(dec_id: str, created_at: str) -> str:
    return f"""\
# 意思決定の台帳。何を根拠に何に賭けたかを残し、後で機械的に答え合わせする。
id: {dec_id}
created_at: {created_at}

# run=次の実験を選ぶ / stop=方向を打ち切る / pivot=方針を変える
type: run

# 何を決めようとしているか。150字。
question: |
  TODO

options:
  - id: o1
    # 何をするか。事実として書く。150字。
    action: |
      TODO
    # 期待する観測。機械が答え合わせに使うので構造化する。
    expected:
      metric: cv.mean
      direction: increase
      magnitude: 0.002
  # - id: o2 ...

chosen: o1

rationale:
  # 根拠にした実験。1つ以上必須。実在チェックがかかる。
  based_on_experiments: []
  # 賭けた仮説。あれば ID で明示する（例: exp0003#h1）。
  based_on_hypotheses: []
  # なぜその選択肢か。200字。
  reason: |
    TODO

# 実験後に expctl decide close で埋める。手で書かない。
outcome: null

lint_waived: []
"""
