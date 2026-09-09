# competition_rule

コンペに挑戦するためのテンプレートリポジトリ。
**実験の記録を正確に取り、その記録から次の一手を正確に決める**ことに絞ってある。

エージェントに任せると記録が比喩とヘッジで太り、その記録を読んだ次のセッションが
解釈を再解釈して意味不明の判断をする。それを構造で防ぐ。
規範を書くだけでは守られないので、lint と hook で機械的に強制する。

なぜこの形なのかは [`docs/design.md`](docs/design.md)。

## 何が強制されるか

| | 強制されること |
|---|---|
| **記録** | 数値は機械しか書けない。記述欄は裸の数値を書けず `${metrics.cv.mean}` で参照する。`observation` は300字。ヘッジ・比喩・擬人化は146パターンで検出。仮説には反証条件が必須 |
| **調査** | `landscape.yaml`（何をやればいいか）と `domain.yaml`（何が本当か）が埋まるまで、モデリング系の軸に進めない |
| **探索** | 指標実装の検証、CV-LB 相関、探索の網羅、アイデア在庫、tier 配分、飽和判定をゲートが見る。判定できない実験が3回続くと誤り分析を強制される |
| **セッション** | 引き継ぎがセッション開始時に自動注入される。compact 直後もファイルから実データが戻る |
| **レポート** | 本文は記録から機械生成。人が書けるのは600字の結論だけ |

## 必要なもの

**[uv](https://docs.astral.sh/uv/)。** Python の実行と依存の管理は uv に一本化してある。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
brew install uv                                    # Homebrew
```

pip でのフォールバックは用意していない。用意すると「uv が無い環境でだけ挙動が違う」
状態が生まれ、再現性を土台にしているこの基盤ではそれ自体が害になる。
Python の版は `.python-version` で固定してあり、uv が必要な版を用意する。

## 始める

### GitHub から（推奨）

このリポジトリを Settings → Template repository にしておくと、
**Use this template** から履歴なし・`origin` が自分のリポジトリの状態で始められる。

作ったら hook を有効にして依存を入れる。

```bash
git config core.hooksPath tools/githooks
uv sync
```

### clone から

```bash
git clone <このリポジトリ> my-competition && cd my-competition
bash tools/bootstrap.sh                    # uv の確認・git の付け替え・uv sync まで
git remote add origin <自分のリポジトリ>   # private を推奨
```

`git clone` すると `origin` はテンプレートを指したままになる。気づかずに
プッシュするとコンペの作業が公開テンプレート側に飛ぶので、`bootstrap.sh` が
`origin` を `template` に改名し、誤プッシュを止める pre-push hook を有効にする。

履歴ごと捨てるなら `--fresh`（テンプレートの更新は取り込めなくなる）。

### 別の端末で続きをやる

**記録は git で運べるが、端末ごとの設定は運べない。** clone しただけでは
`core.hooksPath` が空のままで、テンプレートへの誤プッシュを止めるガードが無効になる。

```bash
git clone <自分のコンペリポジトリ> && cd <それ>
bash tools/bootstrap.sh        # hooksPath の設定と uv sync
```

`bootstrap.sh` は origin がテンプレートを指していないことを見て、
改名は飛ばし、設定と依存の導入だけをやる。

足りないものは `uv run expctl doctor` でいつでも確認できる。
Claude Code を開いたときも、設定が足りなければ引き継ぎの冒頭で警告が出る。

データ（`data/`）は git に入らないので、`knowledge/operations.md` の
取得手順を見て入れ直す。摩擦の記録（`.expkit/`）も端末ごとなので、
`expctl feedback` はその端末で起きた分しか集計しない。

### 立ち上げる

```bash
claude
> /init-competition
```

コンペ情報を聞き取り、`competition.yaml`・`coverage.yaml`・`validation.yaml`・
`landscape.yaml`・`domain.yaml`・アイデア在庫・`CLAUDE.md` を埋める。

**すぐに実験は始まらない。** 最初の phase は `p0_recon`（地固め）で、
評価指標の実装検証と調査が済むまでゲートが先へ進ませない。

## 実験の1周

```bash
uv run expctl status                            # 状態を見る（要約させない）
uv run expctl idea list                         # 在庫を gain/cost 順に見る
uv run expctl decide new --type run             # 決定を台帳に書く
uv run expctl new --tier explore --axes features \
    --based-on exp0003 --decision dec0002 --idea i0007
# 実験を回す（スクリプトから expkit.metrics.write で metrics.json を書く）
# record.yaml の記述欄を埋める
uv run expctl lint exp0004                      # 規範を通す
uv run expctl decide close dec0002 --experiment exp0004   # 機械で答え合わせ
```

phase は `p0_recon` → `p1_survey` → `p2_saturate` → `p3_ideas` の順に進む。

## スキル

| スキル | 用途 |
|---|---|
| `/init-competition` | コンペ用に一度で立ち上げる |
| `/survey` | 地固め。問題を定式化し、上位解法・論文・討論を調べて記録する |
| `/learn-domain` | ドメイン知識を貯める。データ観察と誤り分析から `domain.yaml` を埋める |
| `/log-experiment` | 実験記録の書き方。良い例と悪い例つき |
| `/decide-next` | 次の一手の決め方。「打ち手がない」と感じたときの確認手順 |
| `/write-report` | レポートの作り方。結論600字の書き方 |

## コマンド

| コマンド | 用途 |
|---|---|
| `expctl doctor` | この端末が使える状態か確かめる（別の端末で clone した直後に） |
| `expctl status` | phase・地固め・網羅・CV信頼性・tier配分・較正・消極性 |
| `expctl brief` | 引き継ぎ。セッション開始時に hook が自動で走らせる |
| `expctl new` | 実験を立てる（ゲート判定を通してから） |
| `expctl lint [target]` | 記録・決定・在庫・地固めを検査。省略で全件 |
| `expctl decide new/close` | 決定の作成と機械的な答え合わせ |
| `expctl saturate <axis>` | 軸を飽和（または打ち切り）として宣言 |
| `expctl idea add/retire/list` | アイデア在庫の操作 |
| `expctl landscape check` | 出典の URL が実在するか確かめる |
| `expctl lb <exp>` | LB スコアを差す（手入力として provenance に残る） |
| `expctl cvlb` | CV-LB 対応表と信頼性判定を更新 |
| `expctl table` | 実験一覧を metrics から生成 |
| `expctl render <exp>` | `${...}` を実値に置いて記録を読む |
| `expctl report` | レポートを組み立てる |
| `expctl propose` | 基盤そのものへの改善提案を残す（実際に起きた場面への紐づけが必須） |
| `expctl feedback` | 基盤へのフィードバックを記録から組み立てる（`--issue` で貼る形） |

## ファイル

```
pyproject.toml             依存とコマンド定義。uv add でコンペのライブラリを足す
uv.lock                    依存の固定。コミットする（再現性の土台）
.python-version            Python の版の固定
competition.yaml           コンペ定義。指標・提出形式・ノイズ幅・phase・policy
lint.yaml                  語彙規則と字数上限の上書き（任意）
CLAUDE.md                  /init-competition が生成する作業規範

experiments/exp0001/
  record.yaml              記述欄。lint がかかる
  metrics.json             機械しか書かない。sha256 で守る
decisions/dec0001.yaml     意思決定の台帳。期待と実際を突き合わせる
feedback/proposals.yaml    基盤への改善提案。実際に起きた場面への紐づけが必須
ideas/backlog.yaml         アイデア在庫。空にできない
knowledge/
  landscape.yaml           地固め。何をやればいいか（/survey が埋める）
  domain.yaml              地固め。何が本当か（/learn-domain が埋める）
  coverage.yaml            探索の網羅マップと飽和判定
  validation.yaml          CV 戦略と、分かっていないことの台帳
  operations.md            回し方・環境の癖・踏んだ失敗。引き継ぎに載る
  cv_lb.md                 CV-LB 対応（expctl cvlb が生成）
  priors/                  種別別の実績ある打ち手（汎用）
reports/
  conclusion.md            人が書く唯一の欄。600字、lint あり
  report.md                expctl report が生成。手で編集しない

tools/
  expkit/                  lint・ゲート・状態集計・引き継ぎ・レポート生成（uv run expctl）
  hooks/                   記録の lint 強制、metrics.json 保護、引き継ぎ注入
  githooks/pre-push        テンプレートへの誤プッシュ防止
  bootstrap.sh             clone 直後の git 付け替え
  tests/                   240件
templates/                 実験スクリプトと CLAUDE.md の雛形
```

## 設定を変える

`lint.yaml` をリポジトリ直下に置くと既定にマージされる。語彙の誤検知が続くなら、
その規則の `patterns` を上書きするか `vocab.<rule>.extra` に追加する。

`competition.yaml` の `policy` で在庫の下限、tier の窓と許容幅、CV 信頼性の閾値、
的中率の上限、地固めの下限、誤り分析を強制する連続回数を変えられる。

コンペで使うライブラリは `uv add numpy pandas lightgbm` のように足す。
何を使って実験したかが `uv.lock` に残り、`metrics.json` がその指紋を記録するので、
「この実験と同じ依存で回っているか」を後から1つの値で照合できる。

## 気をつけること

**コンペから受け取ったデータはコミットしない。** 規約でほぼ確実に再配布禁止。
`data/` と `input/` を `.gitignore` で除外してある。取得元と展開手順は
`knowledge/operations.md` に書き、データそのものは置かない。

**提出物は `submissions/<実験ID>.csv` に置く。** `expctl lb exp0007 --public 0.8734`
がその名前でファイルを探し、sha256 と行数を `metrics.json` に記録する。
Code Competition のようにノートブックが実行される形式なら
`competition.yaml` の `submission.kind` を `notebook` にすると探さなくなる。

`submissions/` も既定では `.gitignore` に入れてあるが、**理由は再配布禁止ではない。**
提出物はあなたの予測であって、コンペのデータではない。除外しているのは、
開催中の公開リポジトリに置くと多くのコンペで禁止されている「チーム外への共有」に
当たりうるからで、リポジトリの可視性はテンプレート側から判別できないため
安全側に倒してある。**private なリポジトリなら `.gitignore` から外してよい。**
そうすれば同じ提出を実験の回し直しなしで再現できる。

外さない場合も、指紋が残っているので「手元のファイルが当時のものか」は照合できる。

**記録は除外されない。** `record.yaml` / `metrics.json` / `decisions/` /
`knowledge/` / `feedback/` / `uv.lock` はそのままコミットされる。
`*.csv` を一括で除外しているので、自分で書いた小さな対照表などを追跡したいときは
`git add -f` で個別に足す。

**抜け道は記録に残る。** 語彙規則は `lint_waived`（規則名と20字以上の理由）、
ゲートは `--force`（押し切った内容が `record.yaml` に残る）で抜けられる。
反証条件・改竄検知・スキーマ・参照解決の4つは抜けられない。

**基盤が邪魔をしたら記録する。** ゲートを `--force` で押し切った、`lint_waived` を
使った、記録したいことに置き場所が無かった — そういう場面は
`uv run expctl propose` で残す。`--at` で実際の実験か決定に紐づけるのが必須で、
紐づかない提案は願望として弾かれる。コンペが終わったら
`uv run expctl feedback --issue` が測定値と提案をまとめる。
コンペのデータは含まないので、そのまま公開リポジトリに貼れる。

**この基盤は1コンペも回さずに作った。** 語彙規則の誤検知率、各種の下限と閾値は
すべて当て推量。[`docs/open-questions.md`](docs/open-questions.md) に、
それぞれ何を観察して決め直すかと既知の穴を書いてある。1コンペ回したらまずそこを見る。

## テスト

```bash
uv run pytest -q
```

CI は pytest に加えて pyflakes、**スキルの例文と linter の判定の一致**、
同梱設定の読み込み、シェルスクリプトの構文、hook の終了コードを確認する。
コンペで使い始めたリポジトリでは `expctl lint` も走る。

## もっと詳しく

- [`docs/design.md`](docs/design.md) — なぜこの形なのか。各機構の理由と設計上の緊張
- [`docs/open-questions.md`](docs/open-questions.md) — 未決の設定と既知の穴
