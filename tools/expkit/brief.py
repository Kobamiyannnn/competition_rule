"""引き継ぎ。セッションを捨てても何も失われない状態を作る。

セッションを切れないのは規律の問題ではなく、切ると運用状態が失われるから。
失われるものを全部ここに集めれば、切るのが一番楽な選択肢になる。

出力は毎回のセッション開始に注入されるので、長さを厳しく抑える。
長い引き継ぎは、それ自体が次のセッションのコンテキストを食う。
"""

from __future__ import annotations

from pathlib import Path

from .config import load_lint_config
from .gates import (
    calibration,
    conservatism_findings,
    current_phase,
    cv_trust,
    gate_backlog,
    inconclusive_streak,
    policy,
    recon_findings,
)
from .lint import lint_record
from .paths import knowledge_dir
from .state import Experiment, State

RECENT_EXPERIMENTS = 5
OPERATIONS_CHAR_CAP = 2500


def _fmt(v: object, nd: int = 5) -> str:
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return "-" if v is None else str(v)


def _oneline(text: object, limit: int = 70) -> str:
    s = " ".join(str(text or "").split())
    return (s[:limit] + "…") if len(s) > limit else (s or "-")


def in_flight(state: State, root: Path) -> list[tuple[Experiment, str]]:
    """途中で止まっている実験。record はあるが metrics が無い、または lint が落ちる。

    これが「いま何をしていたか」の唯一の情報源。
    ここに出ない実験は、終わっているか始まっていないかのどちらか。
    """
    cfg = load_lint_config(root)
    out: list[tuple[Experiment, str]] = []
    for e in state.experiments:
        if e.metrics is None:
            out.append((e, "metrics.json がまだ無い"))
            continue
        report = lint_record(
            e.record, cfg=cfg, exp_id=e.id, root=root,
            known_experiments=state.experiment_ids, known_axes=state.axis_ids,
        )
        if report.errors:
            rules = ", ".join(sorted({f.rule for f in report.errors})[:4])
            out.append((e, f"記録が lint に落ちている: {rules}"))
    return out


def _section_in_flight(state: State, root: Path) -> list[str]:
    pending = in_flight(state, root)
    if not pending:
        return ["## いま進行中", "", "止まっている実験は無い。次の一手から始められる。", ""]

    lines = ["## いま進行中", ""]
    for e, why in pending:
        rec = e.record
        lines.append(f"### {e.id} — {why}")
        lines.append(f"- tier: {rec.get('tier', '-')} / 軸: {','.join(e.axes) or '-'} "
                     f"/ 基準: {rec.get('based_on') or '-'}")
        lines.append(f"- 変更: {_oneline((rec.get('change') or {}).get('summary'), 90)}")
        for h in rec.get("hypothesis") or []:
            if isinstance(h, dict):
                lines.append(f"- 仮説 {h.get('id', '?')}: {_oneline(h.get('statement'), 80)}")
                lines.append(f"  反証: {_oneline(h.get('falsification'), 80)}")
        if rec.get("notes"):
            lines.append(f"- 実行メモ: {_oneline(rec.get('notes'), 120)}")
        lines.append("")
    return lines


def _section_recent(state: State) -> list[str]:
    exps = sorted(state.experiments, key=lambda e: e.id)[-RECENT_EXPERIMENTS:]
    if not exps:
        return ["## 直近の実験", "", "まだ1本も無い。", ""]

    rows = [["id", "tier", "軸", "cv.mean", "lb.public", "変更"]]
    for e in exps:
        rows.append([
            e.id, e.tier or "-", ",".join(e.axes) or "-",
            _fmt(e.metric("cv.mean")), _fmt(e.metric("lb.public")),
            _oneline((e.record.get("change") or {}).get("summary"), 44),
        ])
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    body = ["| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(rows[0])) + " |",
            "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    for r in rows[1:]:
        body.append("| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(r)) + " |")

    return [f"## 直近の実験（全{len(state.experiments)}本のうち新しい{len(exps)}本）", "",
            *body, "",
            "全件は `uv run expctl table`、1本の中身は `uv run expctl render <exp_id>`。", ""]


def _section_open_decisions(state: State) -> list[str]:
    open_ = [d for d in state.decisions if not isinstance(d.get("outcome"), dict)]
    if not open_:
        return []
    lines = ["## 答え合わせ待ちの決定", ""]
    for d in open_:
        chosen = next((o for o in d.get("options") or []
                       if isinstance(o, dict) and o.get("id") == d.get("chosen")), {})
        exp = chosen.get("expected") or {}
        lines.append(
            f"- **{d.get('id')}** ({d.get('type')}): {_oneline(d.get('question'), 60)}"
        )
        lines.append(
            f"  選択 {d.get('chosen')} / 期待 {exp.get('metric', '?')} "
            f"{exp.get('direction', '?')} {exp.get('magnitude', '?')}"
        )
    lines += ["", "対応する実験が終わったら "
              "`uv run expctl decide close <dec_id> --experiment <exp_id>`。", ""]
    return lines


def _section_allowed(state: State) -> list[str]:
    ph = current_phase(state)
    pol = policy(state)
    trust = cv_trust(state)
    counts = state.tier_counts(window=int(pol["tier_window"]))
    total = sum(counts.values())
    quota = ph.get("tier_quota") or {}

    recon = recon_findings(state)
    lines = [
        "## いま取れる手",
        "",
        f"- phase: **{ph.get('id')}** — {ph.get('goal', '')}",
        f"- 指標実装の検証: {'済' if state.metric_verified else '**未**（他の軸に進めない）'}",
        "- 地固め: " + (
            f"済（出典 {len(state.sources)} 件 / 事実 {len(state.facts)} 件）"
            if not recon else
            f"**未**（出典 {len(state.sources)} 件 / 事実 {len(state.facts)} 件）。"
            "modeling 系の軸に進めない。`/survey` と `/learn-domain` で埋める"
        ),
        f"- CV の信頼性: {trust.summary()}",
    ]

    if total:
        need = [
            t for t in ("exploit", "explore", "moonshot")
            if float(quota.get(t, 0.0)) > 0
            and float(quota.get(t, 0.0)) - counts.get(t, 0) / total
            > float(pol["tier_quota_tolerance"])
        ]
        ratio = " / ".join(f"{t} {counts[t] / total:.0%}(目標{quota.get(t, 0):.0%})"
                           for t in ("exploit", "explore", "moonshot"))
        lines.append(f"- 直近{total}本の tier: {ratio}")
        if need:
            lines.append(f"- **次の実験は tier: {need[0]} にする**（配分が目標を割っている）")

    assumed = [f for f in state.facts if f.get("confidence") == "assumed"]
    if assumed:
        ids = ", ".join(str(f.get("id") or "?") for f in assumed[:5])
        lines.append(f"- 未検証の仮定: {len(assumed)} 件（{ids}）。残っている間は打ち切れない")
    streak = inconclusive_streak(state)
    if streak:
        limit = int(pol["inconclusive_streak_limit"])
        tail = "。**次の実験は error_analysis 軸**" if streak >= limit else ""
        lines.append(f"- 判定できなかった決定が {streak} 回連続{tail}")

    saturated = [a for a in sorted(state.axis_ids) if state.axis_status(a) == "saturated"]
    if saturated:
        lines.append(f"- 飽和済みで exploit できない軸: {', '.join(saturated)}")
    untouched = state.untouched_axes()
    if untouched:
        lines.append(f"- 未着手の軸（打ち手はまだある）: {', '.join(untouched)}")
    lines.append(f"- アイデア在庫: open {len(state.open_ideas())} 件"
                 f"（下限 {pol['backlog_min_open']}）")

    cal = calibration(state)
    if cal.n:
        hr = "n/a" if cal.hit_rate is None else f"{cal.hit_rate:.0%}"
        lines.append(f"- 決定の較正: hit {cal.hit} / miss {cal.miss} / "
                     f"inconclusive {cal.inconclusive}、的中率 {hr}")

    warn = conservatism_findings(state) + gate_backlog(state) + recon
    if warn:
        lines += ["", "**指摘**", ""]
        lines += [f"- {' '.join(f.message.split())}" for f in warn]
    lines.append("")
    return lines


def _section_operations(root: Path) -> list[str]:
    path = knowledge_dir(root) / "operations.md"
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    # ファイル先頭の「書き方の説明」は毎回注入しても情報が増えないので、
    # 最初の水平線より後だけを載せる。区切りが無ければ全文。
    _head, sep, body = raw.partition("\n---\n")
    text = (body if sep else raw).strip()
    if not text:
        return []
    truncated = len(text) > OPERATIONS_CHAR_CAP
    if truncated:
        text = text[:OPERATIONS_CHAR_CAP]
    lines = ["## 運用上の状態（knowledge/operations.md）", "", text, ""]
    if truncated:
        lines += [f"（{OPERATIONS_CHAR_CAP}字で切った。全文は knowledge/operations.md。"
                  " ここが長いなら、古くなった記述を消す。）", ""]
    return lines


def build(state: State, root: Path) -> str:
    name = state.competition.get("name", "(未設定)")
    lines = [
        f"# 引き継ぎ — {name}",
        "",
        "このセッションが始まる前の状態。すべて記録から生成している。",
        "",
        *_section_in_flight(state, root),
        *_section_allowed(state),
        *_section_open_decisions(state),
        *_section_recent(state),
        *_section_operations(root),
        "## 最初にやること",
        "",
        "1. 進行中の実験があるなら、それを終わらせてから次に進む",
        "2. 無いなら `/decide-next` で次の一手を決める",
        "3. この引き継ぎの数値を信じすぎない。判断の前に `uv run expctl status` を実行する",
        "",
    ]
    return "\n".join(lines) + "\n"
