# competition_rule

コンペに挑戦するための基盤テンプレート。
実験の記録を正確に取り、その記録から次の一手を正確に決めることに絞ってある。

## 何を解こうとしているか

エージェントにコンペを回させると、二種類の壊れ方をする。

**記録が太る。** エージェントは自由記述欄を埋めようとする。埋める材料が
観測値しかないとき、水増しの手段は比喩とヘッジしかない。`AUC +0.003` が
「わずかながら改善の兆しが見られる」になる。次のセッションはこれを事実として
受け取る。`+0.003` はもう消えている。三段目では「改善傾向が確認されている」に
なる。**情報は単調に減り、文字数は単調に増える。** 最終レポートが意味不明に
なるのは必然。

**探索が縮む。** 弱い根拠で打ち切る（ローカルが採点基準を再現できているか
確かめないまま、ローカルを当てにする）。Public が上がらないと消極的になる
（ノイズを結論と読み替える）。既存の勝ち筋の微修正ばかり続く（探索の配分を
誰も管理していない）。

この二つは独立ではない。**記録の規範だけを強くすると、探索は縮む。**
根拠を義務化すれば、エージェントは根拠を書けることしかやらなくなり、
根拠が一番書きやすいのは既存の勝ち筋の微修正だからだ。
だからこの基盤は、両方に同時に手を打つ。

## 記録が太るのを止める仕組み

文体指導（「簡潔に書け」）はしない。守られないので。
**解釈を書ける場所を物理的に減らし、書いた解釈に検証義務を負わせる。**

| 仕組み | 中身 |
|---|---|
| 数値と記述の分離 | `metrics.json` は機械しか書かない。sha256 で守り、手で編集すると lint が落ちる |
| 数値への接地 | 記述欄に裸の数値を書けない。`${metrics.cv.mean}` で参照する。転記ミスと誇張が同時に消える |
| 字数上限 | `observation` は300字、`change` は200字。比喩を書く余白を物理的に奪う |
| 禁止語彙 | ヘッジ・比喩・擬人化・評価語・空句・曖昧な量的表現を機械検出（155パターン） |
| **反証条件の必須化** | `hypothesis` には `falsification` が必須。書けない考えは記録に入れない |
| レポートの機械生成 | 本文は records から組み立てる。人が書けるのは600字の結論だけ |

反証条件の必須化が核。理由は二つある。第一に、**反証条件を書こうとすると
比喩では書けない**。「モデルが特徴を捉えきれていない」は反証条件が書けないので
そこで自壊する。第二に、**反証条件がそのまま次の実験になる**。次の一手が
エージェントの作文ではなく、記録から導かれる。

## 探索が縮むのを止める仕組み

| 仕組み | 中身 |
|---|---|
| 指標実装の検証 | `metric.verified` が false の間、他の軸に進めず、打ち切りもできない |
| CV 信頼性の台帳 | CV-LB の順位相関を機械集計。`calibrated` になるまで CV を根拠に打ち切れない |
| ノイズ幅の強制 | 差が `lb_noise`（CV なら fold の標準誤差）未満なら `miss` ではなく `inconclusive` |
| 探索の網羅マップ | 12 軸。未着手の軸が残っている限り「打ち手がない」と書けない |
| アイデア在庫 | 未実行が下限（既定8件）を割ると次の実験に進めない |
| tier クォータ | `exploit`/`explore`/`moonshot` の比率を phase ごとに強制 |
| **飽和判定** | 試し尽くした軸への `exploit` を**禁止**する。チューニングの無限沼を塞ぐ |
| 較正の追跡 | 期待と実際を突き合わせる。**的中率が高すぎると警告が出る**（＝賭けていない） |

飽和判定が核。「まずハイパラなど可動域を試し尽くしてから、アイデアを試せる
立場に立つ」は正しい — チューニングされていないベースラインとの比較は交絡する。
ただし「試し尽くす」に終点がないと、それ自体が安全で生産的に見える無限の作業に
なる。だから飽和は両刃にしてある。**飽和前は打ち切れず、飽和後は触れない。**

## 使い方

```bash
git clone <このリポジトリ> my-competition && cd my-competition
claude
> /init-competition
```

`/init-competition` が対話でコンペ情報を聞き、`competition.yaml`・
`coverage.yaml`・`validation.yaml`・アイデア在庫・`CLAUDE.md` を埋める。
最初の実験は必ず「評価指標の自前実装が公式定義と一致することを示す」になる。

以降の1周。

```bash
tools/expctl status                            # 状態を見る（要約させない）
tools/expctl idea list                         # 在庫を gain/cost 順に見る
tools/expctl decide new --type run             # 決定を台帳に書く
tools/expctl new --tier explore --axes features \
    --based-on exp0003 --decision dec0002 --idea i0007
# 実験を回す（スクリプトから expkit.metrics.write で metrics.json を書く）
# record.yaml の記述欄を埋める
tools/expctl lint exp0004                      # 規範を通す
tools/expctl decide close dec0002 --experiment exp0004   # 機械で答え合わせ
```

`.claude/settings.json` の hook が、`record.yaml` の保存時に lint を強制し、
`metrics.json` への直接編集をブロックする。

## スキル

| スキル | 用途 |
|---|---|
| `/init-competition` | コンペ用に一度で立ち上げる |
| `/log-experiment` | 実験記録の書き方。良い例と悪い例つき |
| `/decide-next` | 次の一手の決め方。「打ち手がない」と感じたときの確認手順 |
| `/write-report` | レポートの作り方。結論600字の書き方 |

## 構成

```
competition.yaml           コンペ定義。指標・ノイズ幅・phase・policy
lint.yaml                  語彙規則と字数上限の上書き（任意）
CLAUDE.md                  /init-competition が生成する作業規範

experiments/exp0001/
  record.yaml              記述欄。lint がかかる
  metrics.json             機械しか書かない。sha256 で守る
decisions/dec0001.yaml     意思決定の台帳。期待と実際を突き合わせる
ideas/backlog.yaml         アイデア在庫。空にできない
knowledge/
  coverage.yaml            探索の網羅マップと飽和判定
  validation.yaml          CV 戦略と、分かっていないことの台帳
  cv_lb.md                 CV-LB 対応（expctl cvlb が生成）
  priors/                  種別別の実績ある打ち手。在庫の補充源
reports/
  conclusion.md            人が書く唯一の欄。600字、lint あり
  report.md                expctl report が生成。手で編集しない

tools/
  expctl                   CLI
  expkit/                  lint・ゲート・状態集計・レポート生成
  hooks/guard.py           Claude Code の hook
  tests/                   42件
templates/
  experiment_runner.py     実験スクリプトの雛形
  CLAUDE.md                作業規範の雛形
```

## コマンド

| コマンド | 用途 |
|---|---|
| `expctl status` | phase・網羅・CV信頼性・tier配分・較正・消極性 |
| `expctl new` | 実験を立てる（ゲート判定を通してから） |
| `expctl lint [target]` | 記録と決定を検査。省略で全件 |
| `expctl decide new/close` | 決定の作成と機械的な答え合わせ |
| `expctl saturate <axis>` | 軸を飽和（または打ち切り）として宣言 |
| `expctl idea list` | 在庫を gain/cost 順に並べる |
| `expctl lb <exp>` | LB スコアを差す（手入力として provenance に残る） |
| `expctl cvlb` | CV-LB 対応表と信頼性判定を更新 |
| `expctl table` | 実験一覧を metrics から生成 |
| `expctl render <exp>` | `${...}` を実値に置いて記録を読む |
| `expctl report` | レポートを組み立てる |

## 抜け道について

語彙規則だけは `lint_waived` で抜けられる。規則名と20字以上の理由が要り、
**抜けたこと自体が記録に残る**。ゲートを `--force` で押し切った場合も、
押し切った内容が `record.yaml` の `gates_forced` に残る。

抜けられないものは4つ。反証条件、改竄検知、スキーマ、参照解決。
ここを緩めると基盤の意味がなくなる。

## 調整

`lint.yaml` をリポジトリ直下に置くと、既定にマージされる。
語彙の誤検知が続くなら、その規則の `patterns` を上書きするか、
`vocab.<rule>.extra` に追加する。

`competition.yaml` の `policy` で在庫の下限、tier の窓と許容幅、
CV 信頼性の閾値、的中率の上限を変えられる。

## テスト

```bash
python3 -m pytest tools/tests/ -q
```
