"""意思決定のゲート。

linter が「記録の質」を守るのに対し、ここは「探索の質」を守る。
守る対象は三つ。
  - 弱い根拠での打ち切りを許さない（指標実装とCV信頼性の検証を前提にする）
  - ノイズを結論と読み替えさせない（LB の差はノイズ幅と比べてから判定する）
  - 保守化を許さない（tier クォータ、飽和後の exploit 禁止、在庫の維持）
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .state import Experiment, State, spearman

DEFAULT_POLICY = {
    "landscape_min_sources": 5,
    "domain_min_facts": 5,          # ドメインの事実の下限
    "domain_min_confirmed": 2,      # うち自分で確かめたものの下限
    "inconclusive_streak_limit": 3, # 連続でこれだけ判定できなかったら誤り分析へ
    "landscape_min_kinds": 2,
    "landscape_min_transferred": 3,
    "backlog_min_open": 8,
    "backlog_min_axes": 3,         # 在庫が何軸に散っている必要があるか
    "tier_window": 10,
    "tier_quota_tolerance": 0.15,
    "cv_trust_min_pairs": 3,
    "cv_trust_min_spearman": 0.3,
    "hit_rate_ceiling": 0.8,       # 的中率がこれを超えたら賭けていない
    "hit_rate_window": 10,
    "cv_noise_fallback": 0.0,
}

DEFAULT_PHASES = [
    {
        "id": "p0_recon",
        "goal": "地固め。評価指標の実装一致、テストの作られ方の把握、"
                "手法の調査（landscape）と、対象そのものの理解（domain）。"
                "手を動かす前に調べる。",
        "tier_quota": {"exploit": 0.0, "explore": 1.0, "moonshot": 0.0},
    },
    {
        "id": "p1_survey",
        "goal": "モデル族を意図的に雑に走査して、深く掘る先を選ぶ。",
        "tier_quota": {"exploit": 0.2, "explore": 0.7, "moonshot": 0.1},
    },
    {
        "id": "p2_saturate",
        "goal": "選んだパイプラインの可動域を出し切る。ハイパラと既存軸を飽和させる。",
        "tier_quota": {"exploit": 0.7, "explore": 0.3, "moonshot": 0.0},
    },
    {
        "id": "p3_ideas",
        "goal": "飽和した軸の外側へ。新しい打ち手を試せる立場に立っている。",
        "tier_quota": {"exploit": 0.3, "explore": 0.5, "moonshot": 0.2},
    },
]


@dataclass
class GateFinding:
    gate: str
    severity: str          # error / warn / info
    message: str

    def format(self) -> str:
        mark = {"error": "BLOCK", "warn": "WARN ", "info": "INFO "}.get(self.severity, self.severity)
        return f"  [{mark}] {self.gate}: {self.message}"


def policy(state: State) -> dict:
    p = dict(DEFAULT_POLICY)
    p.update(state.competition.get("policy") or {})
    return p


def phases(state: State) -> list[dict]:
    return list(state.competition.get("phases") or DEFAULT_PHASES)


def current_phase(state: State) -> dict:
    pid = state.phase
    for ph in phases(state):
        if ph.get("id") == pid:
            return ph
    return phases(state)[0]


# ---------------------------------------------------------------------------
# CV の信頼性
# ---------------------------------------------------------------------------

@dataclass
class CvTrust:
    n_pairs: int
    rho: float | None
    status: str            # unverified / calibrated / broken

    def summary(self) -> str:
        rho = "n/a" if self.rho is None else f"{self.rho:+.2f}"
        return f"{self.status} (pairs={self.n_pairs}, spearman={rho})"


def cv_trust(state: State) -> CvTrust:
    pairs = state.cv_lb_pairs()
    pol = policy(state)
    rho = spearman([p[1] for p in pairs], [p[2] for p in pairs]) if len(pairs) >= 2 else None

    if len(pairs) < int(pol["cv_trust_min_pairs"]):
        status = "unverified"
    elif rho is None or rho < float(pol["cv_trust_min_spearman"]):
        status = "broken"
    else:
        status = "calibrated"
    return CvTrust(n_pairs=len(pairs), rho=rho, status=status)


# ---------------------------------------------------------------------------
# 地固め
#
# 汎用の打ち手（knowledge/priors/）だけを在庫の種にすると、
# どのコンペでも同じ在庫になり、同じ実験しか出てこない。
# このコンペ固有の外部知識が入るまで、modeling 系の軸に進ませない。
# ---------------------------------------------------------------------------

# 地固めが済んでいなくても触れる軸。手を動かすというより、前提を確かめる作業。
RECON_ALLOWED_AXES = frozenset({"metric_fidelity", "validation", "data_leak"})


def recon_done(state: State) -> bool:
    return not recon_findings(state)


def recon_findings(state: State) -> list[GateFinding]:
    """地固めの不足を返す。空なら済んでいる。

    地固めは2本立て。何をやればいいか（landscape）と、何が本当か（domain）。
    どちらが欠けても、汎用の打ち手を汎用の順序で試すだけの進行になる。
    """
    from .config import load_lint_config
    from .lint import lint_domain, lint_landscape

    cfg = load_lint_config(state.root)
    pol = policy(state)
    out: list[GateFinding] = []

    per_source, whole = lint_landscape(
        state.landscape, cfg=cfg, policy=pol, known_ideas=state.idea_ids,
    )
    errors = list(whole.errors) + [f for r in per_source for f in r.errors]
    if errors:
        rules = sorted({f.rule for f in errors})
        head = whole.errors[0].message if whole.errors else errors[0].message
        out.append(GateFinding(
            gate="recon.landscape_incomplete", severity="error",
            message=f"地固め（手法の調査）が済んでいない（{len(errors)} 件）。"
                    f"{' '.join(head.split())}"
                    f" 詳細は `tools/expctl lint landscape`。規則: {', '.join(rules[:4])}",
        ))

    per_fact, dwhole = lint_domain(
        state.domain, cfg=cfg, policy=pol,
        known_experiments=state.experiment_ids, known_ideas=state.idea_ids,
    )
    derrors = list(dwhole.errors) + [f for r in per_fact for f in r.errors]
    if derrors:
        rules = sorted({f.rule for f in derrors})
        head = dwhole.errors[0].message if dwhole.errors else derrors[0].message
        out.append(GateFinding(
            gate="recon.domain_incomplete", severity="error",
            message=f"ドメイン知識が足りない（{len(derrors)} 件）。{' '.join(head.split())}"
                    f" 詳細は `tools/expctl lint domain`。規則: {', '.join(rules[:4])}",
        ))
    return out


# ---------------------------------------------------------------------------
# 次の実験を出すときのゲート
# ---------------------------------------------------------------------------

def gate_new_experiment(state: State, *, tier: str, axes: list[str]) -> list[GateFinding]:
    out: list[GateFinding] = []
    pol = policy(state)
    ph = current_phase(state)

    # 飽和した軸を exploit で触るのは禁止。試し尽くしたなら、もう触れない。
    for ax in axes:
        if state.axis_status(ax) == "saturated" and tier == "exploit":
            out.append(GateFinding(
                gate="saturation.exhausted", severity="error",
                message=f"軸 {ax} は飽和宣言済み。exploit で触り直さない。"
                        " 触るなら飽和判定を取り消す実験を explore として立てる。",
            ))
        if state.axis_status(ax) == "retired":
            out.append(GateFinding(
                gate="saturation.retired", severity="error",
                message=f"軸 {ax} は打ち切り済み。復活させるなら coverage.yaml の retired を外す。",
            ))

    # 指標実装が公式定義と一致していない間は、指標の話しかできない。
    if not state.metric_verified:
        allowed = {"metric_fidelity", "validation"}
        if axes and not (set(axes) & allowed):
            out.append(GateFinding(
                gate="phase.metric_unverified", severity="error",
                message="competition.yaml の metric.verified が false。"
                        " 自前の指標実装が公式定義と一致することを示すまで、"
                        " metric_fidelity か validation 以外の軸に進まない。",
            ))

    # 地固めが済むまで modeling 系の軸に進まない。
    # 調べる前に手を動かすと、汎用の打ち手を汎用の順序で試すだけになる。
    if axes and not (set(axes) & RECON_ALLOWED_AXES):
        recon = recon_findings(state)
        if recon:
            out.extend(recon)
            out.append(GateFinding(
                gate="recon.axis_blocked", severity="error",
                message="地固めが済むまで触れるのは "
                        f"{', '.join(sorted(RECON_ALLOWED_AXES))} だけ。"
                        " 手法の調査は `/survey`（knowledge/landscape.yaml）、"
                        " ドメイン知識は `/learn-domain`（knowledge/domain.yaml）。",
            ))

    # tier クォータ。phase ごとに配分が違う。
    quota = ph.get("tier_quota") or {}
    window = int(pol["tier_window"])
    counts = state.tier_counts(window=window)
    total = sum(counts.values())
    if total >= max(3, window // 2) and quota:
        tol = float(pol["tier_quota_tolerance"])
        deficits = {
            t: float(quota.get(t, 0.0)) - (counts.get(t, 0) / total)
            for t in ("exploit", "explore", "moonshot")
            if float(quota.get(t, 0.0)) > 0
        }
        if deficits:
            worst = max(deficits, key=lambda t: deficits[t])
            if deficits[worst] > tol and tier != worst:
                obs = counts.get(worst, 0) / total
                out.append(GateFinding(
                    gate="tier.quota", severity="error",
                    message=f"直近{total}本の {worst} 比率が {obs:.0%}、"
                            f" phase {ph.get('id')} の目標 {quota.get(worst):.0%} を下回る。"
                            f" 次の実験は tier: {worst} にする。",
                ))

    # 判定できない実験が続いたら、次は誤り分析。
    # 数値が動かないのに闇雲に次を撃つのは、探索ではなく手詰まりの症状。
    streak = inconclusive_streak(state)
    limit = int(pol["inconclusive_streak_limit"])
    if streak >= limit and "error_analysis" not in axes:
        out.append(GateFinding(
            gate="stuck.inconclusive_streak", severity="error",
            message=f"決定が {streak} 回連続で inconclusive。"
                    " 差がノイズ幅に埋もれ続けている状態で次を撃っても、また判定できない。"
                    " 次の実験は error_analysis 軸にして、どのケースをなぜ外したかを見る。"
                    " 分かったことは knowledge/domain.yaml に入れる（`/learn-domain`）。",
        ))

    # 在庫。空にさせない。
    out.extend(gate_backlog(state))
    return out


def inconclusive_streak(state: State) -> int:
    """決着した決定を新しい順に見て、inconclusive が続いている数。"""
    n = 0
    for d in reversed(state.closed_decisions()):
        if d["outcome"]["result"] == "inconclusive":
            n += 1
        else:
            break
    return n


def valid_open_ideas(state: State) -> tuple[list[dict], list[dict]]:
    """検証を通った未実行のアイデアと、落ちたものを返す。

    在庫の下限を数だけで満たされると仕組みが形骸化するので、
    ゲートが数えるのは検証を通ったものだけにする。
    """
    from .config import load_lint_config
    from .lint import lint_idea

    cfg = load_lint_config(state.root)
    good: list[dict] = []
    bad: list[dict] = []
    for n, idea in enumerate(state.ideas):
        if not isinstance(idea, dict) or (idea.get("status") or "open") != "open":
            continue
        report = lint_idea(
            idea, cfg=cfg, index=n, known_axes=state.axis_ids,
            known_experiments=state.experiment_ids,
        )
        (bad if report.errors else good).append(idea)
    return good, bad


def gate_backlog(state: State) -> list[GateFinding]:
    pol = policy(state)
    minimum = int(pol["backlog_min_open"])
    good, bad = valid_open_ideas(state)
    out: list[GateFinding] = []

    if bad:
        ids = ", ".join(str(i.get("id") or "?") for i in bad[:6])
        out.append(GateFinding(
            gate="backlog.invalid_entries", severity="error",
            message=f"検証を通らないアイデアが {len(bad)} 件あり、在庫として数えていない: {ids}。"
                    " `tools/expctl lint backlog` で理由を見る。",
        ))

    if len(good) < minimum:
        out.append(GateFinding(
            gate="backlog.thin", severity="error",
            message=f"検証を通った未実行のアイデアが {len(good)} 件。下限 {minimum} 件。"
                    " 次の実験に進む前に ideas/backlog.yaml を補充する。"
                    " 補充源は knowledge/landscape.yaml（このコンペ固有）、"
                    " knowledge/priors/（汎用）、未着手の軸。",
        ))

    # 在庫全体の広がり。同じ発想の変奏で埋めさせない。
    min_axes = int(pol["backlog_min_axes"])
    axes = {a for i in good for a in (i.get("axes") or [])}
    if len(good) >= minimum and len(axes) < min_axes:
        out.append(GateFinding(
            gate="backlog.too_narrow", severity="error",
            message=f"在庫が {len(axes)} 軸にしか散っていない（下限 {min_axes} 軸）。"
                    " 別種の打ち手を足す。未着手の軸: "
                    + (", ".join(state.untouched_axes()[:6]) or "なし"),
        ))
    return out


# ---------------------------------------------------------------------------
# 打ち切りのゲート。ここが「弱い根拠での打ち切り」を潰す本体。
# ---------------------------------------------------------------------------

def gate_stop_decision(state: State) -> list[GateFinding]:
    out: list[GateFinding] = []

    # 調べていないなら、打ち手が無いのではなく知らないだけ。
    for f in recon_findings(state):
        out.append(GateFinding(
            gate="stop.recon_incomplete", severity="error",
            message="地固めが済んでいない。世の中がこの問題に何をやってきたかを"
                    " 調べる前に打ち切らない。" + f.message,
        ))

    if not state.metric_verified:
        out.append(GateFinding(
            gate="stop.metric_unverified", severity="error",
            message="評価指標の自前実装が公式定義と一致することを示していない。"
                    " ローカルの数値が採点を再現している保証がないので、打ち切れない。",
        ))

    trust = cv_trust(state)
    if trust.status != "calibrated":
        out.append(GateFinding(
            gate="stop.cv_untrusted", severity="error",
            message=f"CV の信頼性が {trust.summary()}。"
                    " CV を根拠にした打ち切りはできない。"
                    " CV-LB のペアを増やすか、CV 戦略そのものを実験対象にする。",
        ))

    untouched = state.untouched_axes()
    if untouched:
        out.append(GateFinding(
            gate="stop.untouched_axes", severity="error",
            message=f"未着手の軸が {len(untouched)} 本残っている: {', '.join(untouched)}。"
                    " 打ち手が無いのではなく、まだ触っていない。",
        ))

    unsat = [a for a in state.unsaturated_axes() if a not in untouched]
    if unsat:
        out.append(GateFinding(
            gate="stop.unsaturated_axes", severity="error",
            message=f"飽和していない軸が残っている: {', '.join(unsat)}。"
                    " 飽和判定を coverage.yaml に書くか、実験を続ける。",
        ))

    from .lint import assumed_facts

    assumed = assumed_facts(state.domain)
    if assumed:
        ids = ", ".join(str(f.get("id") or "?") for f in assumed[:6])
        out.append(GateFinding(
            gate="stop.assumed_facts_remain", severity="error",
            message=f"未検証の仮定が {len(assumed)} 件残っている（{ids}）。"
                    " 検証していない前提を抱えたまま「打ち手がない」とは書けない。"
                    " 確かめて confirmed にするか、誤りと分かったら消す。",
        ))

    good, _bad = valid_open_ideas(state)
    if good:
        ids = ", ".join(str(i.get("id") or "?") for i in good[:6])
        out.append(GateFinding(
            gate="stop.backlog_not_empty", severity="error",
            message=f"未実行のアイデアが {len(good)} 件ある（{ids}）。"
                    " 在庫が空でない限り「打ち手がない」とは書けない。"
                    " 捨てるなら1件ずつ `expctl idea retire <id> --reason ...` で"
                    " 理由を残す。",
        ))

    return out


# ---------------------------------------------------------------------------
# 決定の答え合わせ。ノイズ幅を必ず通す。
# ---------------------------------------------------------------------------

def noise_floor(state: State, metric_path: str, exp: Experiment | None = None) -> float:
    """その指標で「差がある」と言える最小幅。"""
    if metric_path.startswith("lb"):
        return state.lb_noise

    pol = policy(state)
    if exp is not None:
        std = exp.metric("cv.std")
        folds = exp.metric("cv.folds")
        if isinstance(std, (int, float)) and isinstance(folds, list) and folds:
            # fold 平均の標準誤差。これ未満の差は fold の引き方で消える。
            return float(std) / math.sqrt(len(folds))
    return float(pol["cv_noise_fallback"])


@dataclass
class Judgement:
    result: str            # hit / miss / inconclusive
    metric: str
    baseline: float | None
    actual: float | None
    delta: float | None
    predicted: float | None
    ratio: float | None
    noise: float
    note: str


def judge(state: State, decision: dict, exp: Experiment) -> Judgement:
    chosen_id = decision.get("chosen")
    option = next(
        (o for o in decision.get("options") or []
         if isinstance(o, dict) and o.get("id") == chosen_id),
        None,
    )
    expected = (option or {}).get("expected") or {}
    metric_path = str(expected.get("metric") or "cv.mean")
    direction = expected.get("direction")
    predicted = expected.get("magnitude")
    predicted = float(predicted) if isinstance(predicted, (int, float)) else None

    actual_v = exp.metric(metric_path)
    base_id = exp.record.get("based_on")
    base_exp = next((e for e in state.experiments if e.id == base_id), None)
    base_v = base_exp.metric(metric_path) if base_exp else None
    noise = noise_floor(state, metric_path, exp)

    if not isinstance(actual_v, (int, float)) or not isinstance(base_v, (int, float)):
        return Judgement(
            result="inconclusive", metric=metric_path, baseline=base_v, actual=actual_v,
            delta=None, predicted=predicted, ratio=None, noise=noise,
            note="比較の基準が取れない。record の based_on と metrics を確かめる。",
        )

    delta = float(actual_v) - float(base_v)
    signed = delta if direction == "increase" else -delta
    ratio = (signed / predicted) if (predicted not in (None, 0)) else None

    if abs(delta) < noise:
        return Judgement(
            result="inconclusive", metric=metric_path, baseline=float(base_v),
            actual=float(actual_v), delta=delta, predicted=predicted, ratio=ratio, noise=noise,
            note=f"差 {delta:+.5f} がノイズ幅 {noise:.5f} 未満。"
                 " 効かなかったのではなく、この実験では判定できない。",
        )
    if signed >= (predicted if predicted is not None else 0):
        return Judgement(
            result="hit", metric=metric_path, baseline=float(base_v), actual=float(actual_v),
            delta=delta, predicted=predicted, ratio=ratio, noise=noise,
            note=f"期待した向きに {signed:+.5f}。",
        )
    return Judgement(
        result="miss", metric=metric_path, baseline=float(base_v), actual=float(actual_v),
        delta=delta, predicted=predicted, ratio=ratio, noise=noise,
        note=f"期待 {predicted} に対して実際 {signed:+.5f}。",
    )


# ---------------------------------------------------------------------------
# 較正。賭けなさすぎ／見積もりの甘さを見えるようにする。
# ---------------------------------------------------------------------------

@dataclass
class Calibration:
    n: int
    hit: int
    miss: int
    inconclusive: int
    mean_ratio: float | None

    @property
    def hit_rate(self) -> float | None:
        decided = self.hit + self.miss
        return (self.hit / decided) if decided else None


def calibration(state: State, window: int | None = None) -> Calibration:
    closed = state.closed_decisions()
    if window:
        closed = closed[-window:]
    hit = sum(1 for d in closed if d["outcome"]["result"] == "hit")
    miss = sum(1 for d in closed if d["outcome"]["result"] == "miss")
    inc = sum(1 for d in closed if d["outcome"]["result"] == "inconclusive")
    ratios = [
        float(d["outcome"]["ratio"]) for d in closed
        if isinstance(d["outcome"].get("ratio"), (int, float))
    ]
    return Calibration(
        n=len(closed), hit=hit, miss=miss, inconclusive=inc,
        mean_ratio=(sum(ratios) / len(ratios)) if ratios else None,
    )


def conservatism_findings(state: State) -> list[GateFinding]:
    """消極性そのものを検出する。記録が溜まっているから測れる。"""
    out: list[GateFinding] = []
    pol = policy(state)
    cal = calibration(state, window=int(pol["hit_rate_window"]))

    if cal.hit_rate is not None and cal.n >= 5:
        ceiling = float(pol["hit_rate_ceiling"])
        if cal.hit_rate > ceiling:
            out.append(GateFinding(
                gate="conservatism.hit_rate_too_high", severity="warn",
                message=f"直近の決定の的中率が {cal.hit_rate:.0%}（上限の目安 {ceiling:.0%}）。"
                        " 当たる決定しか立てていない。賭ける幅を上げる。",
            ))
    if cal.mean_ratio is not None and cal.mean_ratio > 2.0 and cal.n >= 5:
        out.append(GateFinding(
            gate="conservatism.underbidding", severity="warn",
            message=f"実際の変化が期待の平均 {cal.mean_ratio:.1f} 倍。"
                    " 期待値を低く見積もりすぎている。",
        ))

    window = int(pol["tier_window"])
    counts = state.tier_counts(window=window)
    total = sum(counts.values())
    if total >= window and counts["explore"] + counts["moonshot"] == 0:
        out.append(GateFinding(
            gate="conservatism.exploit_only", severity="error",
            message=f"直近{total}本がすべて exploit。既存の勝ち筋の外に出ていない。",
        ))
    return out
