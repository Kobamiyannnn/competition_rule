---
name: init-competition
description: コンペ用のリポジトリを一度で立ち上げる。competition.yaml・coverage.yaml・validation.yaml・アイデア在庫・CLAUDE.md を埋め、評価指標の実装検証を最初の作業として立てる。新しいコンペを始めるとき、このテンプレートを clone した直後に使う。
---

# コンペの初期設定

このリポジトリを、特定のコンペで実際に回せる状態にする。
終わったときに揃っているのは次の5つ。

1. `competition.yaml` — 指標・ノイズ幅・phase・policy
2. `knowledge/coverage.yaml` — このコンペに合わせた探索の軸
3. `knowledge/validation.yaml` — テストの作られ方について分かっていること
4. `ideas/backlog.yaml` — `policy.backlog_min_open` 以上の未実行アイデア
5. `CLAUDE.md` — 以降のセッションが規範に従うための指示

## 手順

### 1. 聞く

利用者に次を聞く。分かる範囲でよい。**推測で埋めない**。

- コンペ名と URL
- 評価指標の名前と、公式ページに書かれている定義
- データの形式（テーブル / テキスト / 画像 / 音声 / 時系列 / その他）
- train と test の分かれ方について分かっていること
- 締切、提出回数の上限、外部データの可否
- 実行環境（GPU の有無、1実験にかけられる時間）

URL があるなら公式ページと規約を読みに行く。読めたなら、読んだ内容を
`competition.yaml` に写す。読めなかったなら、その旨を TODO として残す。

### 2. competition.yaml を埋める

`metric.verified` は **必ず false のままにする**。ここを true にできるのは、
指標の自前実装が公式定義と一致することをテストで示したときだけ。

`lb_noise` は分からなければ 0 のままでよいが、その場合は
「同一予測を2回提出して差を測る」を backlog の最初のアイデアとして入れる。
ここが 0 のままだと、LB の差がノイズかどうか判定できず、
「上がらなかった＝効かなかった」という誤読を止められない。

### 3. coverage.yaml をコンペに合わせる

`knowledge/coverage.yaml` の既定の軸から、このコンペに存在しない軸を消し、
足りない軸を足す。判断がつかない軸は**消さずに残す**。
残っている軸は「まだ触っていない」という事実として、打ち切りを止める材料になる。

データ形式に応じて `knowledge/priors/` の該当ファイルを読み、
そこで挙がっている軸が coverage に無ければ足す。

### 4. validation.yaml を埋める

`test_construction.unknown` を**空にしない**。
分かっていないことを分かっていないと書くのが、この欄の役目。
ここが空だと、暗黙の前提のまま実験が進み、あとで弱い根拠の打ち切りになる。

### 5. アイデア在庫を仕込む

`knowledge/priors/README.md` と `common.md`、データ形式に対応するファイルを読み、
`policy.backlog_min_open`（既定8件）以上を `ideas/backlog.yaml` に写す。

`ideas/backlog.yaml` を手で書くより `expctl idea add` を使う。
入れる前に検証がかかるので、通らない項目が在庫に混ざらない。

```
tools/expctl idea add \
  --action "ユーザ単位の集約特徴（購入額の mean / std / count）を12列作り、既存の特徴集合に足す。" \
  --axes features --tier explore --magnitude 0.003 \
  --evidence "priors/tabular.md#t03" --cost 1.0
```

写すときの規則。

- `evidence` には出典を書く。priors 由来なら `priors/common.md#c01` の形。
  出典を指していないと検証に落ちる。
  **このコンペで観測されたことと混ぜない**（まだ何も観測していない）。
- **軸を散らす。** 在庫が3軸未満だとゲートが通らない。
  同じ発想の変奏で8件揃えても、打ち手を持っていることにはならない。
- `expected.magnitude` は、このコンペの指標のスケールに直した数値で書く。
  priors の「大きい/中程度」をそのまま書き写さない。
- `cost` は 1.0 を標準的な1実験として相対で書く。
- 1件は1実験で試せる粒度まで割る。「特徴量エンジニアリング」は粒度が粗すぎる。

順序は `expected.magnitude / cost` の降順になるよう意識する。
`tools/expctl idea list` で確認できる。

### 6. 最初の実験を立てる

最初の実験は**必ず** `metric_fidelity` 軸にする。

```
tools/expctl new --tier explore --axes metric_fidelity
```

この実験でやること。

1. 公式定義を引く
2. 自前の指標実装を書く（`competition.yaml` の `metric.implementation` の場所）
3. 公式定義と一致することを示すテストを書く
4. 通ったら `competition.yaml` の `metric.verified` を true にする

ここが済むまで、他の軸の実験はゲートで止まる。これは意図した挙動なので、
`--force` で回避しない。

### 7. CLAUDE.md を書く

`templates/CLAUDE.md` を `CLAUDE.md` としてコピーし、コンペ名と
データ形式に合わせて具体化する。以降のセッションはこれを読んで規範に従う。

### 8. 確認する

```
tools/expctl status
```

phase・指標検証・網羅・在庫が想定通りか見る。
在庫が下限を割っていたら 5 に戻る。

## この初期設定で守ること

- **推測で埋めない。** 分からないことは TODO か `unknown` として残す。
  埋まっているように見えて中身が推測の欄は、あとで前提として扱われる。
- **`metric.verified` を先に true にしない。** ここが検証の土台。
- **在庫を8件未満で終わらせない。** 在庫が薄いと、序盤から打ち手が尽きた
  ように見えて保守化する。
