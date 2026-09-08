# このリポジトリでの作業規範

<!-- /init-competition がコンペに合わせて具体化する。 -->

コンペ: TODO
データ形式: TODO

## Python は uv 越しに動かす

このリポジトリは uv を前提にしている。`python` や `pip` を直接呼ばない。

```bash
uv run expctl status          # コマンド
uv run pytest -q              # テスト
uv run python src/run.py ...  # 任意のスクリプト
uv add lightgbm               # ライブラリを足す
```

`uv add` で足したものは `uv.lock` に固定され、`metrics.json` がその指紋を記録する。
`pip install` で入れたものは記録に残らず、その実験は再現できなくなる。

uv が無いと記録の検査（hook）が走らない。無いなら先に入れる。

## 最初に読むもの

作業を始める前に `uv run expctl status` を実行し、出力をそのまま読む。
要約しない。要約すると数値が落ちて評価語に置き換わる。

## 守ること

### 数値に触らない

`experiments/*/metrics.json` は機械しか書かない。手で編集すると
integrity ハッシュがずれて lint が落ちる。

- 実験の数値: 実験スクリプトから `expkit.metrics.write(...)`
- LB スコア: `uv run expctl lb <exp_id> --public <score>`

### 記録に解釈を混ぜない

`record.yaml` の3つの欄は別の問いに答える。混ぜない。

- `change` — 何を変えたか（結果を知る前に書ける内容）
- `observation` — 何が起きたか（`${metrics....}` 参照。裸の数値は書かない）
- `hypothesis` — なぜだと考えるか（反証条件が書けないなら書かない）

詳しくは `/log-experiment`。

### 反証条件が書けない考えは記録に入れない

「〜が効いている」「モデルが〜を捉えられていない」は、反証条件が書けない。
消すか、観測に降ろすか、反証できる粒度まで割る。消すのは負けではない。

### 調べる前に手を動かさない

最初の phase は `p0_recon`（地固め）。`knowledge/landscape.yaml` が検証を
通るまで、`metric_fidelity` / `validation` / `data_leak` 以外の軸には
ゲートで進めない。埋めるのは `/survey`。

地固めは2本立て。`landscape` が「何をやればいいか」、`domain` が「何が本当か」。

`knowledge/priors/` は汎用の打ち手で、どのコンペでも同じ内容になる。
**汎用の在庫からは汎用のアイデアしか出ない。** 上位との差はこの2つで作る。

### 確かめていないことを confirmed と書かない

`knowledge/domain.yaml` の `confidence` は型で分ける。
自分で確かめたものだけ `confirmed`、読んだだけは `likely`、思っただけは `assumed`。

**未検証の仮定が前提として使われるのが「弱い根拠で打ち切る」の正体。**
`assumed` と書いておけば、残っている限り打ち切りゲートが止めてくれる。

### 判定できない状態が続いたら誤り分析

決定が3回連続で `inconclusive` になると、ゲートが次の実験に
`error_analysis` 軸を要求する。数値が動かないのに闇雲に次を撃つのは、
探索ではなく手詰まりの症状。OOF 予測を切り口を変えて集計する（`/learn-domain`）。

### 「打ち手がない」と書く前に

順に確認する。ほとんどの場合、打ち手はある。

1. `expctl status` の未着手の軸
2. `expctl idea list` の在庫
3. `knowledge/landscape.yaml` の未消化の `takeaway`
4. `knowledge/domain.yaml` の未消化の `implication`
5. `knowledge/priors/` の未消化項目
6. 未決着の仮説の反証条件（そのまま実験になる）
7. `/survey` か `/learn-domain` をもう一度回す

在庫を補充するときは `expctl idea add`。入れる前に検証がかかるので、
中身の薄い項目で数だけ満たすことはできない（出典と粒度と軸の広がりを見る）。

打ち切るなら `expctl decide new --type stop`。ゲートが通らないなら
まだ打ち切る段階ではない。

### ノイズと結論を区別する

LB や CV の差が `competition.yaml` の `lb_noise`（CV なら fold の標準誤差）
を下回るなら、それは「効かなかった」ではなく「判定できなかった」。
`expctl decide close` が機械判定するので、自分で読み替えない。

### 飽和の基準を先に決める

ハイパーパラメータ探索は安全で生産的に見えるので無限に続けられる。
探索を始める前に飽和の判定基準を決め、達したら
`expctl saturate <axis> --criterion "..."` で宣言する。
宣言した軸には以降 `tier: exploit` で触れなくなる。

## セッションの切り方

**実験1本ごとにセッションを切る。** コンテキストの節約が目的ではない。

長いセッションでは、エージェントが自分で1時間前に書いた散文がコンテキストに残り、
それを一次資料として読み直す。「さっき集約特徴が効いたので」と、自分の要約を根拠に
次を決める。元の数値はもう遥か上にある。これはこのリポジトリが防ごうとしている
「解釈の再解釈」が、1つのセッションの中で起きている状態。
**セッションの境界は、一次資料に戻ることを強制する装置。**

切るコストはほぼ無い。SessionStart hook が `uv run expctl brief` の出力を
自動で注入するので、新しいセッションは前のセッションが持っていた状態から始まる。

### 切る前にやること

進行中の実験があるなら、`record.yaml` の `notes` に**どこまで進んだか**を書く。
これが唯一、ファイルから復元できない情報。

```yaml
notes: |
  fold 3 まで完了。fold 4 で OOM。batch を 16 に落として再実行中。
```

結果の解釈は書かない。それは `observation` と `hypothesis` の役目。

### compact について

`/compact` は要約する。要約こそ、このリポジトリ全体が防ごうとしている操作。

**compact を打ちたくなること自体が、何かが書き出されていないという信号。**
全部ファイルにあるなら、セッションを捨てて新しく始める方が速い。

PreCompact hook が、書き出されていない状態を指摘して引き継ぎを保存する。
compact を止めはしないが、指摘が出たら先に書き出す。

## コンテキストを食う作業は委譲する

実験そのものより、その周辺がコンテキストを食う。出力が大量で、
有用な結論は数行しかない作業は、サブエージェントに投げて結果だけ受け取る。

| 作業 | 委譲する | 受け取るもの |
|---|---|---|
| データの探索・分布の確認 | する | 数値と、気づいた点の箇条書き |
| 失敗の原因追跡・デバッグ | する | 原因1行と、適用した修正 |
| 公開解法・ディスカッションの読み込み | する | `backlog.yaml` に足すべき項目 |
| 実験の記録を書く | **しない** | 規範がかかる作業なので本体でやる |
| 次の一手を決める | **しない** | 判断は本体の責任 |

委譲するときは、**結論だけを短く返させる**。生の出力を返させると委譲の意味が消える。

## 基盤が邪魔をしたら記録する

この基盤の設定値は1コンペも回さずに置いた当て推量なので、使ってみないと
当たり外れが分からない。邪魔をされた場面は、次に直すための材料になる。

**書くきっかけ**

- ゲートを `--force` で押し切ったとき
- `lint_waived` を使ったとき
- 記録したいことに置き場所が無かったとき
- 規範に従うと不自然な作業になったとき

```
uv run expctl propose --kind missing_capability --at exp0012 \
  --incident "fold ごとに別のモデルを使う構成を試したが、config が1モデル前提で、change.diff に差分を表現できなかった。" \
  --missing "config に fold 別の設定を持てる形が無い。" \
  --workaround "fold 番号を config のキーに含めて回避したが、diff が読めなくなった。"
```

**`--at` は必須。** 実際に起きた場面に紐づかない提案は願望であって、
再設計の材料にならない。思いついただけなら書かない。

規則そのものへの不満は `lint_waived` の `reason` に書けばよい。
それが「なぜこの規則がここでは間違っていたか」の記録になり、集計される。

コンペが終わったら `uv run expctl feedback --issue` で、
測定値と提案をまとめた報告が出る。コンペのデータは含まないので、
そのままテンプレートリポジトリの Issue に貼れる。

## 使うスキル

| 場面 | スキル |
|---|---|
| コンペを調べる（手法） | `/survey` |
| ドメイン知識を貯める | `/learn-domain` |
| 実験を回し終えて記録を書く | `/log-experiment` |
| 次に何を試すか決める | `/decide-next` |
| レポートを作る | `/write-report` |

## 実験の1周

```
uv run expctl status                          # 状態を見る
uv run expctl idea list                       # 在庫から選ぶ
uv run expctl decide new --type run           # 決定を書く
uv run expctl new --tier ... --axes ... \     # 実験を立てる
    --based-on ... --decision ... --idea ...
# 実験を回す（スクリプトから expkit.metrics.write で metrics.json を書く）
# record.yaml の記述欄を埋める
uv run expctl lint <exp_id>                   # 規範を通す
uv run expctl decide close <dec_id> --experiment <exp_id>   # 答え合わせ
```
