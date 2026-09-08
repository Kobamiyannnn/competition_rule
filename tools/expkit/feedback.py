"""基盤そのものへのフィードバックを、記録から組み立てる。

docs/open-questions.md に並んでいる数値は1コンペも回さずに置いた当て推量で、
使ってみないと当たり外れが分からない。そのとき「使いにくかった」という感想ではなく
「どの規則が何回発火し、何回 waive されたか」で判断できるようにする。

出力にコンペのデータは含めない。規則名・ゲート名・回数と、
基盤自身の設定値だけを出す。private なコンペでも公開リポジトリに貼れる。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

from . import events
from .gates import calibration, cv_trust, policy
from .state import State, load_state


def _table(rows: list[list[str]]) -> list[str]:
    if len(rows) < 2:
        return ["(該当なし)", ""]
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    out = ["| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(rows[0])) + " |",
           "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    for r in rows[1:]:
        out.append("| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(r)) + " |")
    out.append("")
    return out


def _oneline(text: object, limit: int = 60) -> str:
    s = " ".join(str(text or "").split())
    return (s[:limit] + "…") if len(s) > limit else (s or "-")


# ---------------------------------------------------------------------------
# 語彙規則の当たり外れ
# ---------------------------------------------------------------------------

def _waivers(state: State) -> tuple[Counter, dict[str, list[str]]]:
    """記録に残っている lint_waived を集計する。

    waive されたということは、その場ではその規則が間違っていたということ。
    waive 率が高い規則は誤検知を疑う。
    """
    counts: Counter = Counter()
    reasons: dict[str, list[str]] = {}
    docs = [e.record for e in state.experiments] + list(state.decisions)
    for doc in docs:
        for w in doc.get("lint_waived") or []:
            if not isinstance(w, dict):
                continue
            rule = str(w.get("rule") or "?")
            counts[rule] += 1
            reasons.setdefault(rule, []).append(str(w.get("reason") or ""))
    return counts, reasons


def _forced_gates(state: State) -> Counter:
    counts: Counter = Counter()
    for e in state.experiments:
        for g in e.record.get("gates_forced") or []:
            if isinstance(g, dict) and g.get("gate"):
                counts[str(g["gate"])] += 1
    return counts


def _section_vocabulary(state: State) -> list[str]:
    counts, reasons = _waivers(state)
    # 同じファイルを何度保存しても hook が毎回走るので、
    # (規則, 対象) の組で重複を除く。数えたいのは「何箇所で止めたか」。
    seen_pairs = {
        (ev.get("rule"), ev.get("target"))
        for ev in events.load(state.root)
        if ev.get("kind") == "lint_blocked" and ev.get("rule")
    }
    blocked = Counter(rule for rule, _target in seen_pairs)

    rules = sorted(set(counts) | set(blocked))
    rows = [["規則", "発火", "うち直した", "うち waive", "waive 率"]]
    for rule in rules:
        fixed, waived = blocked.get(rule, 0), counts.get(rule, 0)
        total = fixed + waived
        rate = f"{waived / total:.0%}" if total else "-"
        rows.append([rule, str(total), str(fixed), str(waived), rate])

    lines = [
        "### 語彙・記述の規則",
        "",
        "「直した」は指摘を受けて書き直した箇所、「waive」は規則の側が"
        "間違っていたとして抜けた回数。",
        "",
        *_table(rows),
    ]
    if counts:
        lines += ["waive の理由（規則ごと）", ""]
        for rule in sorted(counts):
            for r in reasons.get(rule, [])[:5]:
                lines.append(f"- `{rule}`: {_oneline(r, 80)}")
        lines.append("")
    lines += [
        "> waive 率が高い規則は誤検知を疑う。理由が毎回同じなら、"
        "`lint.yaml` でその規則の `patterns` を絞る。",
        "> 理由が毎回「表現を変えるのが面倒」なら、規則ではなく使い方の問題。",
        "",
    ]
    return lines


# ---------------------------------------------------------------------------
# ゲートの重さ
# ---------------------------------------------------------------------------

def _section_gates(state: State) -> list[str]:
    evs = events.load(state.root)
    blocked = Counter(
        ev.get("gate") for ev in evs
        if ev.get("kind") == "gate_blocked" and ev.get("gate")
    )
    forced = _forced_gates(state)

    rows = [["ゲート", "止めた回数", "--force で押し切った回数"]]
    for gate in sorted(set(blocked) | set(forced)):
        rows.append([gate, str(blocked.get(gate, 0)), str(forced.get(gate, 0)) or "0"])

    lines = ["### ゲート", "", *_table(rows)]
    if not evs:
        lines += [
            "> `.expkit/events.jsonl` が空。ゲートの記録はローカルにしか残らないので、"
            "別の環境で回した分は入っていない。",
            "",
        ]
    else:
        lines += [
            "> 止めた回数が多く押し切りが 0 のゲートは、機能しているか過剰かのどちらか。"
            " 押し切りが多いゲートは、条件が実態に合っていない。",
            "",
        ]
    return lines


# ---------------------------------------------------------------------------
# 当て推量の設定値
# ---------------------------------------------------------------------------

def _section_policy(state: State) -> list[str]:
    pol = policy(state)
    cal = calibration(state)
    trust = cv_trust(state)
    evs = events.load(state.root)
    blocked = Counter(
        ev.get("gate") for ev in evs if ev.get("kind") == "gate_blocked"
    )

    sources = state.sources
    transferred = [s for s in sources if s.get("transferred_to")]
    facts = state.facts
    confirmed = [f for f in facts if f.get("confidence") == "confirmed"]
    assumed = [f for f in facts if f.get("confidence") == "assumed"]

    hit_rate = "n/a" if cal.hit_rate is None else f"{cal.hit_rate:.0%}"
    mean_ratio = "n/a" if cal.mean_ratio is None else f"{cal.mean_ratio:.2f}"

    rows = [["設定", "既定値", "実際に観測されたこと"]]
    rows.append([
        "backlog_min_open", str(pol["backlog_min_open"]),
        f"backlog.thin が {blocked.get('backlog.thin', 0)} 回発火",
    ])
    rows.append([
        "backlog_min_axes", str(pol["backlog_min_axes"]),
        f"backlog.too_narrow が {blocked.get('backlog.too_narrow', 0)} 回発火",
    ])
    rows.append([
        "hit_rate_ceiling", str(pol["hit_rate_ceiling"]),
        f"決着 {cal.n} 件・的中率 {hit_rate}・実際/期待 {mean_ratio}",
    ])
    rows.append([
        "inconclusive_streak_limit", str(pol["inconclusive_streak_limit"]),
        f"stuck.inconclusive_streak が {blocked.get('stuck.inconclusive_streak', 0)} 回発火"
        f"・inconclusive {cal.inconclusive} 件",
    ])
    rows.append([
        "landscape_min_sources", str(pol["landscape_min_sources"]),
        f"出典 {len(sources)} 件・うち在庫に移した {len(transferred)} 件",
    ])
    rows.append([
        "domain_min_facts / confirmed",
        f"{pol['domain_min_facts']} / {pol['domain_min_confirmed']}",
        f"事実 {len(facts)} 件・confirmed {len(confirmed)}・assumed {len(assumed)}",
    ])
    rows.append([
        "cv_trust_min_pairs", str(pol["cv_trust_min_pairs"]),
        f"CV-LB {trust.n_pairs} 組・判定 {trust.status}",
    ])
    rows.append([
        "tier_quota（phase 別）", "-",
        f"tier.quota が {blocked.get('tier.quota', 0)} 回発火",
    ])
    rows.append([
        "飽和判定", "-",
        f"saturation.exhausted が {blocked.get('saturation.exhausted', 0)} 回発火",
    ])

    return [
        "### 当て推量で置いた設定値",
        "",
        *_table(rows),
        "> `docs/open-questions.md` に、それぞれ何を見て決め直すかが書いてある。",
        "",
    ]


# ---------------------------------------------------------------------------
# 改善提案
# ---------------------------------------------------------------------------

def _section_proposals(state: State) -> list[str]:
    proposals = [p for p in state.proposal_list if p.get("status", "open") == "open"]
    if not proposals:
        return [
            "### 改善の提案",
            "",
            "（未投稿の提案なし。基盤に足りないものに気づいたら"
            " `uv run expctl propose` で残す。）",
            "",
        ]

    lines = ["### 改善の提案", ""]
    for p in proposals:
        lines.append(f"#### `{p.get('kind', '?')}` — {p.get('id', '?')}")
        lines.append("")
        lines.append(f"**起きたこと**（{', '.join(str(a) for a in p.get('at') or [])}）")
        lines.append("")
        lines.append(str(p.get("incident") or "").strip())
        lines.append("")
        lines.append("**無かったもの**")
        lines.append("")
        lines.append(str(p.get("missing") or "").strip())
        workaround = str(p.get("workaround") or "").strip()
        if workaround:
            lines.append("")
            lines.append(f"**回避**: {workaround}")
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# 環境
# ---------------------------------------------------------------------------

def _section_environment(state: State) -> list[str]:
    def git(*args: str) -> str:
        try:
            out = subprocess.run(["git", *args], cwd=state.root,
                                 capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return "?"
        return out.stdout.strip() if out.returncode == 0 else "?"

    head = git("rev-parse", "--short", "HEAD")
    base = "?"
    if git("remote").find("template") >= 0:
        base = git("merge-base", "--all", "HEAD", "template/main")[:7] or "?"

    return [
        "### 環境",
        "",
        f"- このリポジトリの HEAD: `{head}`",
        f"- テンプレートとの分岐点: `{base}`",
        f"- 実験 {len(state.experiments)} 本 / 決定 {len(state.decisions)} 件",
        "",
    ]


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------

def build(state: State, *, as_issue: bool = False) -> str:
    name = state.competition.get("name", "(未設定)")
    head = "## " if as_issue else "# "
    title = (
        "competition_rule を1コンペ通して使った結果"
        if as_issue else f"competition_rule へのフィードバック — {name}"
    )

    lines = [f"{head}{title}", ""]
    if as_issue:
        lines += [
            "`uv run expctl feedback --issue` の出力。"
            "コンペのデータは含まない（規則名・ゲート名・回数と、基盤自身の設定値だけ）。",
            "",
        ]
    else:
        lines += [
            "<!-- uv run expctl feedback が生成する。手で編集しない。",
            "     コンペのデータは含まないので、そのまま公開リポジトリに貼れる。 -->",
            "",
        ]

    lines += _section_environment(state)
    lines += _section_policy(state)
    lines += _section_vocabulary(state)
    lines += _section_gates(state)
    lines += _section_proposals(state)
    return "\n".join(lines) + "\n"


def cmd_feedback(args: argparse.Namespace) -> int:
    from .paths import repo_root

    root = repo_root()
    state = load_state(root)
    text = build(state, as_issue=args.issue)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"{out} に書いた。")
        return 0

    print(text, end="")
    if args.issue:
        print("\n---", file=sys.stderr)
        print("これをテンプレートリポジトリの Issue に貼る。"
              "投稿は自動でしない。中身を読んでから出す。", file=sys.stderr)
    return 0
