---
name: init-competition
description: コンペ用のリポジトリを一度で立ち上げる。competition.yaml・coverage.yaml・validation.yaml・アイデア在庫・CLAUDE.md を埋め、評価指標の実装検証を最初の作業として立てる。新しいコンペを始めるとき、このテンプレートを clone した直後に使う。
---

# コンペの初期設定

このリポジトリを、特定のコンペで実際に回せる状態にする。
終わったときに揃っているのは次の7つ。

1. `competition.yaml` — 指標・ノイズ幅・phase・policy
2. `knowledge/coverage.yaml` — このコンペに合わせた探索の軸
3. `knowledge/validation.yaml` — テストの作られ方について分かっていること
4. `knowledge/landscape.yaml` — 地固め。何をやればいいか（手法の調査）
5. `knowledge/domain.yaml` — 地固め。何が本当か（対象そのものの性質）
6. `ideas/backlog.yaml` — `policy.backlog_min_open` 以上の未実行アイデア
7. `CLAUDE.md` — 以降のセッションが規範に従うための指示

**すぐに実験を回し始めない。** 最初の phase は `p0_recon`（地固め）で、
指標実装の検証と調査が済むまで modeling 系の軸にはゲートで進めない。
調べる前に手を動かすと、汎用の打ち手を汎用の順序で試すだけになる。

## 手順

### 0. 環境を整える（clone で始めた場合）

まだやっていなければ、最初にこれを実行する。

```
bash tools/bootstrap.sh
```

uv が無ければここで止まり、入れ方を案内する。**代替経路は用意していない。**
用意すると「uv が無い環境でだけ挙動が違う」状態が生まれ、
再現性を土台にしているこの基盤ではそれ自体が害になる。

やること。

- `origin` を `template` に改名する。以降 `origin` は未設定になるので、
  コンペ用のリポジトリ（**private を推奨**）を作って `git remote add origin <URL>`
- テンプレートへの誤プッシュを止める pre-push hook を有効にする
- `data/` を作る（`.gitignore` でコミット対象から外れている）

履歴ごと捨てたいなら `bash tools/bootstrap.sh --fresh`。
ただし `git pull template main` でテンプレートの更新を取り込めなくなる。

GitHub の「Use this template」で始めた場合は、履歴も `origin` も
最初から正しいので、hook の有効化と依存の導入だけやる。

```
git config core.hooksPath tools/githooks
uv sync
```

**コンペのデータをコミットしない。** 規約でほぼ確実に再配布禁止になっている。
`.gitignore` で除外してあるが、`git add -f` で強制追加しない。
データの取得元と展開手順は `knowledge/operations.md` に書く。

### 1. 聞く

利用者に次を聞く。分かる範囲でよい。**推測で埋めない**。

- コンペ名と URL
- 評価指標の名前と、公式ページに書かれている定義
- データの形式（テーブル / テキスト / 画像 / 音声 / 時系列 / その他）
- train と test の分かれ方について分かっていること
- 締切、提出回数の上限、外部データの可否
- **提出の形式** — csv などを自分で作ってアップロードするのか（`file`）、
  Kaggle 側でノートブックが実行されるのか（`notebook`）
- 実行環境（GPU の有無、1実験にかけられる時間）
- 使う予定のライブラリ（後で `uv add` で足す）

URL があるなら公式ページと規約を読みに行く。読めたなら、読んだ内容を
`competition.yaml` に写す。読めなかったなら、その旨を TODO として残す。

### 2. ライブラリを入れて competition.yaml を埋める

コンペで使うライブラリは `uv add` で入れる。

```
uv add numpy pandas scikit-learn lightgbm
```

**`pip install` は使わない。** `uv.lock` に残らないと、その実験は再現できない。
`metrics.json` が `uv.lock` の指紋を記録するので、後から
「この実験と同じ依存で回っているか」を1つの値で照合できる。

そのうえで `competition.yaml` を埋める。

`submission.kind` を `file` か `notebook` のどちらかにする。
`file` なら提出物を `submissions/<実験ID>.csv` に置く規約になり、
`expctl lb` がその sha256 と行数を記録する。
`notebook` なら提出ファイルを探さなくなる。

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

### 5. 地固めをする

```
/survey
```

このコンペの問題を既知の定式化に落とし、過去の類似コンペの上位解法・
ディスカッション・論文を調べて `knowledge/landscape.yaml` に記録する。

**ここを飛ばさない。** `knowledge/priors/` は私が汎用の記憶から書いた
打ち手で、どのコンペでも同じ内容になる。汎用の在庫からは汎用のアイデアしか
出ない。上位との差はここで開く。

済んだかどうかは機械で判定する。

```
uv run expctl lint landscape
uv run expctl landscape check
```

### 6. ドメイン知識を貯め始める

```
/learn-domain
```

（`/learn-domain` を呼ぶ。データ観察の節だけを使う。）

`landscape` が「何をやればいいか」なら、こちらは「**何が本当か**」。
1行が何を表すか、目的変数の分布、欠損の構造、時間と場所の分かれ方、
ありえない値。**モデルが無くても実データを見れば確かめられる。**

`confidence` を必ず付ける。自分で確かめたものだけ `confirmed`、
読んだだけは `likely`、思っただけは `assumed`。
下限は事実5件、うち自分で確かめたもの2件。

```
uv run expctl lint domain
```

### 7. アイデア在庫を仕込む

補充源は3つある。**全部から入れる。**

1. `knowledge/landscape.yaml` — `/survey` で集めた出典の `takeaway`
2. `knowledge/domain.yaml` — 6 で貯めた事実の `implication`
3. `knowledge/priors/` — 汎用。`README.md` と `common.md`、データ形式に対応するファイル

`policy.backlog_min_open`（既定8件）以上を `ideas/backlog.yaml` に写す。
landscape 由来を最低3件は入れる（`landscape_min_transferred`）。
汎用だけで埋めると、どのコンペでも同じ在庫になる。

`ideas/backlog.yaml` を手で書くより `expctl idea add` を使う。
入れる前に検証がかかるので、通らない項目が在庫に混ざらない。

```
uv run expctl idea add \
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
`uv run expctl idea list` で確認できる。

### 8. 最初の実験を立てる

最初の実験は**必ず** `metric_fidelity` 軸にする。

```
uv run expctl new --tier explore --axes metric_fidelity
```

この実験でやること。

1. 公式定義を引く
2. 自前の指標実装を書く（`competition.yaml` の `metric.implementation` の場所）
3. 公式定義と一致することを示すテストを書く
4. 通ったら `competition.yaml` の `metric.verified` を true にする

ここが済むまで、他の軸の実験はゲートで止まる。これは意図した挙動なので、
`--force` で回避しない。地固め（5）も同じゲートを持っている。

### 9. CLAUDE.md を書く

`templates/CLAUDE.md` を `CLAUDE.md` としてコピーし、コンペ名と
データ形式に合わせて具体化する。以降のセッションはこれを読んで規範に従う。

### 10. 確認する

```
uv run expctl status
```

phase・指標検証・網羅・在庫が想定通りか見る。
在庫が下限を割っていたら 7 に戻る。

## この初期設定で守ること

- **推測で埋めない。** 分からないことは TODO か `unknown` として残す。
  埋まっているように見えて中身が推測の欄は、あとで前提として扱われる。
- **`metric.verified` を先に true にしない。** ここが検証の土台。
- **在庫を8件未満で終わらせない。** 在庫が薄いと、序盤から打ち手が尽きた
  ように見えて保守化する。
- **`pip install` を使わない。** `uv add` で入れる。lock に残らない依存で
  回した実験は再現できず、`metrics.json` の指紋も意味を失う。
- **地固めを飛ばして実験に入らない。** 5 と 6 を飛ばすと、`priors/` の汎用な
  打ち手を順に試すだけの進行になる。それは誰がやっても同じ結果にしかならない。
- **確かめていないことを `confirmed` と書かない。** 未検証の仮定が前提として
  使われるのが「弱い根拠で打ち切る」の正体。`assumed` と書けば、
  残っている限り打ち切りゲートが止めてくれる。
- **開いていない URL を landscape に書かない。** 出典が捏造されると、
  `evidence` を要求している仕組み全体が意味を失う。
