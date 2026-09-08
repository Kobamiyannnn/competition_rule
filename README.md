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
| 禁止語彙 | ヘッジ・比喩・擬人化・評価語・空句・曖昧な量的表現を機械検出（146パターン） |
| **反証条件の必須化** | `hypothesis` には `falsification` が必須。書けない考えは記録に入れない |
| レポートの機械生成 | 本文は records から組み立てる。人が書けるのは600字の結論だけ |

反証条件の必須化が核。理由は二つある。第一に、**反証条件を書こうとすると
比喩では書けない**。「モデルが特徴を捉えきれていない」は反証条件が書けないので
そこで自壊する。第二に、**反証条件がそのまま次の実験になる**。次の一手が
エージェントの作文ではなく、記録から導かれる。

## 調べる前に手を動かさせない仕組み

同梱の `knowledge/priors/` は私が汎用の記憶から書いた打ち手で、
**どのコンペでも同じ内容になる。汎用の在庫からは汎用のアイデアしか出ない。**
それを順に試すだけの進行は、誰がやっても同じ結果にしかならない。

だから最初の phase は `p0_recon`（地固め）にしてある。

| 仕組み | 中身 |
|---|---|
| `knowledge/landscape.yaml` | このコンペ固有の外部知識。問題の定式化と、調べた出典 |
| `/survey` スキル | 問題を既知の定式化に落とし、過去の上位解法・論文・討論を調べて記録する |
| **recon ゲート** | 地固めが検証を通るまで `metric_fidelity` / `validation` / `data_leak` 以外の軸に進めない |
| 出典の種類の下限 | 論文だけ・解法だけにさせない（既定2種類） |
| 在庫への転記の下限 | 調べただけで打ち手にしていない状態を通さない（既定3件） |
| `expctl landscape check` | 出典の URL が実在するか確かめる。捏造された出典を残さない |

`stop` の決定も地固めが済むまで通らない。
**調べていないなら、打ち手が無いのではなく知らないだけ。**

phase はこの順に進む。

```
p0_recon     指標実装の一致・テストの作られ方・文献と過去解法の調査
p1_survey    モデル族を意図的に雑に走査して掘る先を選ぶ
p2_saturate  可動域を出し切る
p3_ideas     飽和した軸の外側へ
```

## 探索が縮むのを止める仕組み

| 仕組み | 中身 |
|---|---|
| 指標実装の検証 | `metric.verified` が false の間、他の軸に進めず、打ち切りもできない |
| CV 信頼性の台帳 | CV-LB の順位相関を機械集計。`calibrated` になるまで CV を根拠に打ち切れない |
| ノイズ幅の強制 | 差が `lb_noise`（CV なら fold の標準誤差）未満なら `miss` ではなく `inconclusive` |
| 探索の網羅マップ | 12 軸。未着手の軸が残っている限り「打ち手がない」と書けない |
| アイデア在庫 | 未実行が下限（既定8件）を割ると次の実験に進めない。**数えるのは検証を通った項目だけ** |
| tier クォータ | `exploit`/`explore`/`moonshot` の比率を phase ごとに強制 |
| **飽和判定** | 試し尽くした軸への `exploit` を**禁止**する。チューニングの無限沼を塞ぐ |
| 較正の追跡 | 期待と実際を突き合わせる。**的中率が高すぎると警告が出る**（＝賭けていない） |

飽和判定が核。「まずハイパラなど可動域を試し尽くしてから、アイデアを試せる
立場に立つ」は正しい — チューニングされていないベースラインとの比較は交絡する。
ただし「試し尽くす」に終点がないと、それ自体が安全で生産的に見える無限の作業に
なる。だから飽和は両刃にしてある。**飽和前は打ち切れず、飽和後は触れない。**

## セッションが太るのを止める仕組み

三つ目の問題は、実験ごとにセッションを切ろうと思っても切れないこと。
切ると運用状態（回し方、所要時間、どこまで進んだか）が失われるので、
そのまま次の実験も回す。コンテキストが埋まり、`/compact` を打って台無しになる。

**切れないのは規律の問題ではない。切ると本当に何かが失われるから切れない。**
だから対策は「切る規律を持つ」ではなく、**切っても何も失われない状態にする**こと。

| 仕組み | 中身 |
|---|---|
| `expctl brief` | 進行中の実験・いま取れる手・未決着の決定・直近の実験・運用状態を記録から生成 |
| SessionStart hook | 引き継ぎを自動で注入する。`startup` / `resume` / `clear` / `compact` すべてで走る |
| `record.notes` | ファイルから復元できない唯一の情報（「fold 4 で OOM。batch 16 で再実行中」） |
| `knowledge/operations.md` | 回し方・環境の癖・踏んだ失敗。引き継ぎに含まれる |
| PreCompact hook | compact を止めずに、書き出されていない状態を指摘して引き継ぎを保存する |

SessionStart は `source: compact` でも走るので、**compact の直後にファイルから
実データが読み直される**。要約ではなく元の数値が戻るので、compact が致命傷でなくなる。

### 「一実験一セッション」は古いのか

古くない。ただし、普通に挙げられる理由（コンテキスト長）は弱い理由。
本当の理由は、このリポジトリが防ごうとしている失敗そのものにある。

長いセッションでは、エージェントが1時間前に自分で書いた散文がコンテキストに残り、
それを一次資料として読み直す。「さっき集約特徴が効いたので」と、自分の要約を根拠に
次を決める。元の数値はもう遥か上にある。**解釈の再解釈が、1つのセッションの中で
起きている状態。** セッションの境界は、一次資料に戻ることを強制する装置。

より新しいのは**薄いコーディネータ＋委譲**。コンテキストを食うのは実験そのもの
ではなく周辺（データ探索・失敗の原因追跡・公開解法の読み込み）で、これらは出力が
大量で結論は数行。サブエージェントに投げて結論だけ受け取り、本体は薄く保つ。
記録を書く作業と次の一手を決める作業は委譲しない。規範と判断の責任は本体にある。

## 使い方

### GitHub で始める（推奨）

このリポジトリを Settings → Template repository にしておくと、
**Use this template** から履歴なし・`origin` が自分のリポジトリの状態で始められる。
clone した場合と違って git の付け替えが要らない。

作ったら hook だけ有効にする。

```bash
git config core.hooksPath tools/githooks
```

### clone で始める

```bash
git clone <このリポジトリ> my-competition && cd my-competition
bash tools/bootstrap.sh
```

`git clone` すると **`origin` はテンプレートを指したまま**になる。
気づかずにプッシュすると、コンペの作業が公開テンプレート側に飛ぶ。
`bootstrap.sh` がこれを直す。

- `origin` を `template` に改名する（`git pull template main` で更新を取り込める）
- テンプレート宛のプッシュを止める pre-push hook を有効にする
- `data/` を作る

履歴ごと捨てるなら `bash tools/bootstrap.sh --fresh`。
テンプレートの更新は取り込めなくなる。

そのあと `git remote add origin <自分のリポジトリ>`（**private を推奨**）。

### コンペを立ち上げる

```bash
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
| `/survey` | 地固め。問題を定式化し、上位解法・論文・討論を調べて記録する |
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
  landscape.yaml           地固め。このコンペ固有の外部知識（/survey が埋める）
  coverage.yaml            探索の網羅マップと飽和判定
  validation.yaml          CV 戦略と、分かっていないことの台帳
  operations.md            回し方・環境の癖・踏んだ失敗。引き継ぎに載る
  cv_lb.md                 CV-LB 対応（expctl cvlb が生成）
  priors/                  種別別の実績ある打ち手（汎用）。在庫の補充源
reports/
  conclusion.md            人が書く唯一の欄。600字、lint あり
  report.md                expctl report が生成。手で編集しない

tools/
  bootstrap.sh             clone 直後に一度だけ実行して git を付け替える
  githooks/pre-push        テンプレートへの誤プッシュを止める
  expctl                   CLI
  expkit/                  lint・ゲート・状態集計・レポート生成
  hooks/guard.py           記録の lint 強制と metrics.json の保護
  hooks/session.py         引き継ぎの注入と compact 前の点検
  tests/                   137件
templates/
  experiment_runner.py     実験スクリプトの雛形
  CLAUDE.md                作業規範の雛形
```

## コマンド

| コマンド | 用途 |
|---|---|
| `expctl brief` | 引き継ぎ。セッション開始時に hook が自動で走らせる |
| `expctl status` | phase・網羅・CV信頼性・tier配分・較正・消極性 |
| `expctl new` | 実験を立てる（ゲート判定を通してから） |
| `expctl lint [target]` | 記録と決定を検査。省略で全件 |
| `expctl decide new/close` | 決定の作成と機械的な答え合わせ |
| `expctl saturate <axis>` | 軸を飽和（または打ち切り）として宣言 |
| `expctl landscape check` | 出典の URL が実在するか確かめる |
| `expctl idea list` | 在庫を gain/cost 順に並べる |
| `expctl idea add` | 在庫に足す。検証を通らないと入らない |
| `expctl idea retire` | 在庫から捨てる。20字以上の理由が要る |
| `expctl lb <exp>` | LB スコアを差す（手入力として provenance に残る） |
| `expctl cvlb` | CV-LB 対応表と信頼性判定を更新 |
| `expctl table` | 実験一覧を metrics から生成 |
| `expctl render <exp>` | `${...}` を実値に置いて記録を読む |
| `expctl report` | レポートを組み立てる |

## 在庫を数だけで満たせないようにする仕組み

在庫の下限は「打ち手がない」と言わせないための仕組みなので、
中身の薄い項目を8件並べて満たされると形骸化する。だから3点を機械で見る。

| 規則 | 中身 |
|---|---|
| `idea.action_too_vague` | `action` が20字未満は1実験の粒度になっていない |
| `idea.evidence_without_source` | `evidence` が出典を指すこと（`priors/common.md#c01` / `exp0003` / `dec0002` / URL） |
| `backlog.too_narrow` | 在庫が3軸以上に散っていること。同じ発想の変奏で埋めさせない |

そして**ゲートが数えるのは検証を通った項目だけ**。落ちた項目は在庫として数えない。
逆向きの穴も塞いである。中身の無い在庫を口実に打ち切りを止め続けることもできない。

捨てるときは `expctl idea retire <id> --reason ...` で20字以上の理由が要る。

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

## データを絶対にコミットしない

コンペのデータは規約でほぼ確実に再配布禁止になっている。
`.gitignore` で `data/` `input/` `output/` `submissions/` と
`*.csv` `*.parquet` `*.pkl` `*.pt` などを除外してある。

取得元と展開手順は `knowledge/operations.md` に書き、データそのものは置かない。
記録（`record.yaml` / `metrics.json` / `decisions/` / `knowledge/`）は
除外対象に入っていないので、そのままコミットされる。

## 未決の設定

この基盤は1コンペも回さずに作った。語彙規則の誤検知率、在庫の下限、
tier クォータ、的中率の上限、飽和の判定基準は**すべて当て推量**で、
実データで検証していない。

`docs/open-questions.md` に、それぞれ何を観察して決め直すかを書いてある。
1コンペ回したら、まずそこを見る。

## テスト

```bash
python3 -m pytest -q
```

CI（`.github/workflows/ci.yml`）は pytest に加えて、pyflakes、
**スキルの例文と linter の判定の一致**、同梱設定の読み込み、
シェルスクリプトの構文、hook の終了コードを確認する。
コンペで使い始めたリポジトリでは `expctl lint` も走り、
hook を迂回して書かれた記録をここで捕まえる。

規範を触ったときに壊れやすいのは「スキルの例文と linter の一致」。
ここがずれると、エージェントは文書に従って書いて linter に落とされ続け、
やがて抜け道を探し始める。
