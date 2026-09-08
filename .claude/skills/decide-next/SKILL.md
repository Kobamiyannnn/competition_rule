---
name: decide-next
description: 次に何を試すかを決め、決定を台帳に残す。アイデア在庫・探索の網羅・tier 配分・CV の信頼性をゲートとして通す。実験が終わって次の一手を選ぶとき、または「打ち手がない」と感じたときに使う。
---

# 次の実験を決める

決定は台帳に残す。何を根拠に何に賭けたかが残っていないと、
外れた理由も当たった理由も後から分からない。

## 順序

### 1. 状態を見る

```
tools/expctl status
```

見るのは5つ。

- **指標検証** — false なら `metric_fidelity` 以外に進めない
- **CV信頼性** — `unverified` / `broken` なら CV だけを根拠にしない
- **探索の網羅** — 未着手の軸が残っているか
- **tier 配分** — 目標を割っている tier があるか
- **アイデア在庫** — 下限を割っていないか

**LLM に状態を要約させない。** このコマンドの出力をそのまま読む。
要約を挟むと、そこで数値が落ちて評価語に置き換わる。

### 2. 在庫から選ぶ

```
tools/expctl idea list
```

`expected.magnitude / cost` の降順で出る。**上位から選ぶのが既定**。
上位を飛ばすなら、決定の `rationale.reason` にその理由を書く。

在庫が下限を割っているなら、選ぶ前に補充する。補充源は5つ。

1. **`knowledge/landscape.yaml`** — `/survey` で集めたこのコンペ固有の出典。
   `takeaway` のうち在庫に移していないものが残っていないか見る
2. **`knowledge/domain.yaml`** — 貯めた事実の `implication`。
   在庫に移していないものが残っていないか見る
3. `knowledge/priors/` — 汎用。未消化の項目
4. 未着手の軸 — `expctl status` に出ている
5. **未決着の仮説の反証条件** — そのまま実験の手順になる
6. 追加の調査 — 1〜4 が尽きたら `/survey` をもう一度回す。
   序盤に調べたときとは問題の見え方が変わっているので、違う出典が見つかる

補充は `expctl idea add` で行う。入れる前に検証がかかる。

```
tools/expctl idea add \
  --action "adversarial validation で train と test の分布差を測り、判別に効く列を落とす。" \
  --axes validation --tier explore --magnitude 0.002 \
  --evidence "priors/common.md#c04" --cost 0.8
```

検証は3点を見る。**数だけ揃えても在庫として数えられない。**

- `action` が20字以上で、1実験の粒度まで割れていること
- `evidence` が出典を指すこと（`priors/common.md#c01` / `exp0003` / `dec0002` / URL）
- 在庫全体が3軸以上に散っていること（同じ発想の変奏で埋めない）

捨てるときも理由が要る。

```
tools/expctl idea retire i0004 --reason "i0003 と同じ集約を別の統計量で作るだけで、独立した検証にならない。"
```

### 3. 決定を書く

```
tools/expctl decide new --type run
```

`options` は2つ以上書く。比較していない決定は根拠が弱い。
`expected` は機械が答え合わせに使うので、必ず数値で書く。

```yaml
expected:
  metric: cv.mean
  direction: increase
  magnitude: 0.003
```

`magnitude` が書けないなら、その決定は根拠が言葉だけになっている。
在庫の `expected` から写すか、根拠にした実験の変化量から見積もる。

**低く見積もって的中率を上げようとしない。** `expctl status` の
「実際/期待」がずれて露見する。当たる決定しか立てていない状態は、
賭けていないという意味なので警告が出る。

### 4. 実験を立てて回す

```
tools/expctl new --tier <tier> --axes <axes> --based-on <exp> --decision <dec> --idea <idea>
```

ゲートに止められたら、止めた理由に対処する。`--force` は使わない
（使うと押し切った事実が record に残る）。

### 5. 答え合わせ

```
tools/expctl decide close <dec_id> --experiment <exp_id>
```

`hit` / `miss` / `inconclusive` が機械判定される。

**`inconclusive` を `miss` として扱わない。** 差がノイズ幅を下回ったとき、
それは「効かなかった」ではなく「この実験では判定できなかった」。
判定するには、fold を増やす、seed を変えて繰り返す、
効果が出やすい設定で試す、のいずれかが要る。

### 判定できない状態が続いたら

`inconclusive` が3回続くとゲートが次の実験に `error_analysis` 軸を要求する。

**数値が動かないのに闇雲に次を撃つのは、探索ではなく手詰まりの症状。**
OOF 予測を切り口を変えて集計し、誤差が集中している切り口を探す。
分かったことは `knowledge/domain.yaml` に入れる（`/learn-domain`）。

そこで見つかる着想が、いま在庫にある汎用の打ち手より当たる。

## 「打ち手がない」と感じたとき

これは高確率で誤りなので、順に確認する。

1. `expctl status` の未着手の軸 — 触っていない軸が残っていないか
2. `expctl idea list` — 在庫に何が残っているか
3. `knowledge/landscape.yaml` — 在庫に移していない `takeaway` が残っていないか
4. `knowledge/priors/` — 未消化の項目があるか
5. 未決着の仮説 — 反証条件がそのまま次の実験になる
6. `knowledge/validation.yaml` の `test_construction.unknown` —
   分かっていないことが残っているなら、それを調べるのが打ち手
7. `/survey` をもう一度回す — 序盤とは問題の見え方が変わっているので、
   同じ検索でも違う出典が目に入る

本当に打ち切るなら `--type stop` で決定を立てる。
ゲートが通らないなら、まだ打ち切る段階ではない。

## 保守化しかけたときの兆候

`expctl status` が出す警告を無視しない。

- **的中率が高すぎる** — 当たる決定しか立てていない。`moonshot` を混ぜる
- **実際が期待の2倍以上** — 見積もりが低すぎる。賭ける幅を上げる
- **直近が全部 exploit** — 既存の勝ち筋の外に出ていない

ハイパーパラメータの探索は、安全で生産的に見えるので無限に続けられる。
そうならないよう、**飽和の判定基準を探索を始める前に決める**。

```
tools/expctl saturate hyperparameters \
  --criterion "直近30試行で best がノイズ幅0.002を超えて更新されない" \
  --evidence exp0012,exp0013
```

飽和を宣言した軸には、以降 `tier: exploit` で触れなくなる。
それが「アイデアを試せる立場に立つ」ということ。
