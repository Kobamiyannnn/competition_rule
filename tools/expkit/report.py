"""レポート生成。

レポートは著作物ではなく生成物として扱う。
本文の大半は records と metrics から機械的に組み立て、
LLM が書くのは reports/conclusion.md ただ一つ、しかも600字上限で lint がかかる。
自由に書ける面積を与えなければ、太った表現は入りようがない。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_lint_config
from .gates import calibration, conservatism_findings, current_phase, cv_trust
from .lint import Report, lint_prose_field
from .paths import reports_dir, repo_root
from .state import State, load_state

CONCLUSION_NAME = "conclusion.md"


def lint_conclusion(text: str, cfg: dict, report: Report) -> None:
    """結論の欄は observation と同じ strict プロファイルで検査する。

    レポート中で自由に書ける唯一の場所なので、ここだけは緩めない。
    """
    cfg = {
        **cfg,
        "limits": {"report.conclusion": cfg["limits"]["report.conclusion"]},
        "field_profiles": {"report.conclusion": "strict"},
    }
    lint_prose_field("report.conclusion", "reports/conclusion.md", text, cfg, report)


def _fmt(v: object, nd: int = 5) -> str:
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return "-" if v is None else str(v)


def _table(rows: list[list[str]]) -> list[str]:
    if len(rows) < 2:
        return ["(データなし)", ""]
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    out = ["| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(rows[0])) + " |",
           "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    for r in rows[1:]:
        out.append("| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(r)) + " |")
    out.append("")
    return out


def _section_overview(state: State) -> list[str]:
    comp = state.competition
    metric = comp.get("metric") or {}
    ph = current_phase(state)
    return [
        "## コンペ",
        "",
        f"- 名称: {comp.get('name', '(未設定)')}",
        f"- 評価指標: {metric.get('name', '(未設定)')} / {metric.get('direction', '?')}",
        f"- 指標実装の検証: {'済' if state.metric_verified else '**未**'}",
        f"- 現在の phase: {ph.get('id')} — {ph.get('goal', '')}",
        f"- LB のノイズ幅: {state.lb_noise}",
        "",
    ]


def _section_validation(state: State) -> list[str]:
    trust = cv_trust(state)
    pairs = state.cv_lb_pairs()
    lines = [
        "## 検証の信頼性",
        "",
        f"CV と LB のペアは {len(pairs)} 組、順位相関は "
        + ("算出不能" if trust.rho is None else f"{trust.rho:+.3f}")
        + f"。判定は **{trust.status}**。",
        "",
    ]
    if trust.status != "calibrated":
        lines += [
            "> この判定の間、CV のみを根拠にした打ち切りはゲートで止まる。",
            "",
        ]
    rows = [["experiment", "cv.mean", "lb.public"]]
    for eid, cv, lb in pairs:
        rows.append([eid, _fmt(cv), _fmt(lb)])
    lines += _table(rows)
    return lines


def _section_experiments(state: State) -> list[str]:
    rows = [["id", "tier", "軸", "cv.mean", "cv.std", "lb.public", "変更"]]
    for e in state.experiments:
        summary = " ".join(str((e.record.get("change") or {}).get("summary") or "").split())
        rows.append([
            e.id, e.tier or "-", ",".join(e.axes) or "-",
            _fmt(e.metric("cv.mean")), _fmt(e.metric("cv.std"), 5),
            _fmt(e.metric("lb.public")),
            summary[:60],
        ])
    return ["## 実験", "", *_table(rows)]


def _section_coverage(state: State) -> list[str]:
    rows = [["軸", "状態", "実験数", "飽和判定の根拠"]]
    labels = {"untouched": "未着手", "open": "進行中", "saturated": "飽和", "retired": "打ち切り"}
    for a in sorted(state.axis_ids):
        axis = state.axis(a) or {}
        sat = axis.get("saturation") or {}
        crit = " ".join(str(sat.get("criterion") or "").split())
        rows.append([
            a, labels[state.axis_status(a)],
            str(len(state.axis_experiments(a))), crit[:50] or "-",
        ])
    lines = ["## 探索の網羅", "", *_table(rows)]
    untouched = state.untouched_axes()
    if untouched:
        lines += [
            f"未着手の軸が {len(untouched)} 本残っている: {', '.join(untouched)}。",
            "打ち手が尽きたのではなく、まだ触っていない。",
            "",
        ]
    return lines


def _section_decisions(state: State) -> list[str]:
    rows = [["id", "type", "選択", "結果", "期待", "実際", "実際/期待", "ノイズ幅"]]
    for d in state.decisions:
        o = d.get("outcome") if isinstance(d.get("outcome"), dict) else {}
        rows.append([
            str(d.get("id", "?")), str(d.get("type", "?")), str(d.get("chosen", "-")),
            str(o.get("result", "未決着")), _fmt(o.get("predicted")), _fmt(o.get("delta")),
            _fmt(o.get("ratio"), 2), _fmt(o.get("noise_floor")),
        ])
    cal = calibration(state)
    lines = ["## 意思決定の台帳", "", *_table(rows)]
    if cal.n:
        hr = "n/a" if cal.hit_rate is None else f"{cal.hit_rate:.0%}"
        mr = "n/a" if cal.mean_ratio is None else f"{cal.mean_ratio:.2f}"
        lines += [
            f"決着 {cal.n} 件。hit {cal.hit} / miss {cal.miss} / inconclusive {cal.inconclusive}。"
            f" 的中率 {hr}、実際は期待の {mr} 倍。",
            "",
            "> inconclusive は「効かなかった」ではなく「この実験では判定できなかった」。"
            " 差がノイズ幅を下回った回数がここに出る。",
            "",
        ]
    return lines


def _section_hypotheses(state: State) -> list[str]:
    settled, open_ = [], []
    for e in state.experiments:
        for h in e.record.get("hypothesis") or []:
            if not isinstance(h, dict):
                continue
            hid = f"{e.id}#{h.get('id', '?')}"
            stmt = " ".join(str(h.get("statement") or "").split())
            fals = " ".join(str(h.get("falsification") or "").split())
            status = h.get("status", "open")
            if status in ("supported", "refuted"):
                settled.append([hid, status, stmt[:70], str(h.get("resolved_by") or "-")])
            else:
                open_.append([hid, stmt[:60], fals[:60]])

    lines = ["## 仮説", "", "### 決着したもの", ""]
    lines += _table([["id", "判定", "内容", "決着させた実験"], *settled])
    lines += ["### 未決着のもの", ""]
    lines += _table([["id", "内容", "反証条件"], *open_])
    if open_:
        lines += ["> 未決着の仮説は、次の実験の候補そのもの。反証条件がそのまま手順になる。", ""]
    return lines


def _section_backlog(state: State) -> list[str]:
    rows = [["id", "tier", "軸", "期待", "コスト", "根拠", "内容"]]
    for i in state.open_ideas():
        exp = i.get("expected") or {}
        action = " ".join(str(i.get("action") or "").split())
        rows.append([
            str(i.get("id", "?")), str(i.get("tier", "-")), ",".join(i.get("axes") or []) or "-",
            f"{exp.get('metric','?')} {exp.get('direction','?')} {exp.get('magnitude','?')}",
            _fmt(i.get("cost"), 1), str(i.get("evidence", "-"))[:24], action[:50],
        ])
    return ["## 未実行のアイデア在庫", "", *_table(rows)]


def _section_conclusion(state: State, root: Path) -> tuple[list[str], Report | None]:
    path = reports_dir(root) / CONCLUSION_NAME
    if not path.exists():
        return (
            [
                "## 結論",
                "",
                f"（未記入。`{path.relative_to(root)}` に600字以内で書く。"
                " ここがレポート中で人と LLM が自由に書ける唯一の場所で、lint がかかる。）",
                "",
            ],
            None,
        )
    text = path.read_text(encoding="utf-8").strip()
    report = Report(target=str(path.relative_to(root)))
    lint_conclusion(text, load_lint_config(root), report)
    return ["## 結論", "", text, ""], report


def build(state: State, root: Path) -> tuple[str, Report | None]:
    conclusion, creport = _section_conclusion(state, root)
    lines = [
        f"# 実験レポート — {state.competition.get('name', '(未設定)')}",
        "",
        "<!-- expctl report が生成する。手で編集しない。",
        "     直したいときは records / metrics / reports/conclusion.md を直す。 -->",
        "",
        *conclusion,
        *_section_overview(state),
        *_section_validation(state),
        *_section_experiments(state),
        *_section_coverage(state),
        *_section_decisions(state),
        *_section_hypotheses(state),
        *_section_backlog(state),
    ]
    warn = conservatism_findings(state)
    if warn:
        lines += ["## 探索の姿勢に対する指摘", ""]
        lines += [f"- {f.message}" for f in warn]
        lines += [""]
    return "\n".join(lines) + "\n", creport


def cmd_report(args: argparse.Namespace) -> int:
    root = repo_root()
    state = load_state(root)
    text, creport = build(state, root)

    out = Path(args.out) if args.out else reports_dir(root) / "report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"{out} を生成した。")

    if creport is not None and creport.findings:
        print(creport.format())
        if creport.errors:
            print("\n結論の欄が lint を通っていない。直してから配る。")
            return 1
    return 0
