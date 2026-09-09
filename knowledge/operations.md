# 運用上の状態

セッションを切っても失われてはいけない、実験の回し方に関する知識。
`expctl brief` がここを引き継ぎに含めるので、セッション開始時に自動で読まれる。

**書くもの** — 次のセッションが同じ場所から動き出すのに要ること。

- コマンドと所要時間
- 環境の癖（メモリ上限、GPU、パスの前提）
- 一度踏んだ失敗と、その回避策
- データの置き場所と作り方

**書かないもの** — 別の場所に居場所がある。

- 実験の結果 → `experiments/*/metrics.json`
- 結果の解釈 → `record.yaml` の `observation` / `hypothesis`
- 何を試すか決めた理由 → `decisions/`
- 打ち手の候補 → `ideas/backlog.yaml`

引き継ぎには先頭 2500 字までしか載らない。
**古くなった記述は消す。** 追記だけしていると、載らなくなって意味を失う。

---

## コマンド

```
# TODO: 学習の回し方。Python は uv 越しに動かす。
# 例: uv run python src/run.py --exp exp0007      （所要 40 分）
# 例: uv run python src/submit.py --exp exp0007   （提出ファイル生成、2 分）
#
# ライブラリを足すときは uv add。pip install は使わない。
# uv.lock に残らないと、その実験は再現できない。
```

## 提出

TODO: 提出ファイルの作り方と出し方。
例: uv run python src/submit.py --exp exp0007  → submissions/exp0007.csv
    kaggle competitions submit -c <slug> -f submissions/exp0007.csv -m exp0007
    そのあと uv run expctl lb exp0007 --public <score>

## 環境

TODO: メモリ・GPU・並列数の上限、データの場所

## 踏んだ失敗

<!-- 例:
- fold 3 だけ OOM する。batch を 16 に落とすと通る（exp0005 で確認）
- 特徴量キャッシュは data/cache/ に残る。特徴量を変えたら消さないと古いものを読む
-->

TODO
