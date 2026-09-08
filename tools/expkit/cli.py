"""expctl — この基盤の操作口。

数値を書く口は expkit.metrics.write と `expctl lb` だけ。
それ以外から metrics.json に触ると integrity ハッシュがずれて lint が落ちる。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import metrics as metrics_mod
from .config import load_lint_config
from .gates import (
    calibration,
    inconclusive_streak,
    policy,
    recon_findings,
    conservatism_findings,
    current_phase,
    cv_trust,
    gate_backlog,
    gate_new_experiment,
    gate_stop_decision,
    judge,
)
from .lint import (
    lint_backlog,
    lint_decision,
    lint_domain,
    lint_idea,
    lint_landscape,
    lint_record,
)
from .paths import (
    decision_path,
    decisions_dir,
    experiments_dir,
    knowledge_dir,
    record_path,
    repo_root,
)
from .schema import decision_template, record_template
from .state import Experiment, State, load_state

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _next_id(directory: Path, prefix: str, suffix: str = "") -> str:
    n = 0
    if directory.exists():
        for p in directory.iterdir():
            name = p.stem if suffix else p.name
            if name.startswith(prefix) and name[len(prefix):].isdigit():
                n = max(n, int(name[len(prefix):]))
    return f"{prefix}{n + 1:04d}"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _dump_yaml(path: Path, doc: dict) -> None:
    path.write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )


def _print_gates(findings: list, *, header: str) -> bool:
    """error が1件でもあれば True（＝止める）。"""
    if not findings:
        return False
    print(f"{BOLD}{header}{RESET}")
    for f in findings:
        print(f.format())
    return any(f.severity == "error" for f in findings)


# ---------------------------------------------------------------------------
# new
# ---------------------------------------------------------------------------

def cmd_new(args: argparse.Namespace) -> int:
    root = repo_root()
    state = load_state(root)
    axes = [a.strip() for a in (args.axes or "").split(",") if a.strip()]

    unknown = [a for a in axes if a not in state.axis_ids]
    if unknown:
        print(f"知らない軸: {', '.join(unknown)}", file=sys.stderr)
        print(f"使える軸: {', '.join(sorted(state.axis_ids))}", file=sys.stderr)
        return 2

    findings = gate_new_experiment(state, tier=args.tier, axes=axes)
    blocked = _print_gates(findings, header="ゲート判定")
    if blocked and not args.force:
        print("\n実験を作らずに終了した。上の指摘に対処するか --force で押し切る"
              "（押し切った事実は record に残る）。", file=sys.stderr)
        return 1

    exp_id = args.id or _next_id(experiments_dir(root), "exp")
    path = record_path(exp_id, root)
    if path.exists():
        print(f"{path} はすでにある。", file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)

    # テンプレートのコメントを保ちたいので、YAML に通さず文字列のまま値を差し替える。
    text = record_template(exp_id, _now())
    text = text.replace("tier: explore", f"tier: {args.tier}")
    text = text.replace("axes: []", "axes: [" + ", ".join(axes) + "]" if axes else "axes: []")
    if args.based_on:
        text = text.replace("based_on: null", f"based_on: {args.based_on}")
    if args.decision:
        text = text.replace("decision: null", f"decision: {args.decision}")
    if args.idea:
        text = text.replace("idea: null", f"idea: {args.idea}")

    # ゲートを押し切ったなら、その事実と内容を記録に残す。
    # 抜け道を使ったこと自体が、あとで読める形で残らないと意味がない。
    if blocked and args.force:
        forced = ["", "# --force でゲートを押し切って作った実験。", "gates_forced:"]
        for f in findings:
            if f.severity == "error":
                msg = " ".join(f.message.split())
                forced.append(f"  - gate: {f.gate}")
                forced.append("    message: |")
                forced.append(f"      {msg}")
        text += "\n".join(forced) + "\n"

    path.write_text(text, encoding="utf-8")

    if args.idea:
        _mark_idea(root, args.idea, status="running", experiment=exp_id)

    print(f"{path} を作った。")
    print("次にやること: 実験スクリプトから expkit.metrics.write で metrics.json を書き、")
    print(f"              record.yaml の記述欄を埋め、`expctl lint {exp_id}` を通す。")
    return 0


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------

def cmd_lint(args: argparse.Namespace) -> int:
    root = repo_root()
    state = load_state(root)
    cfg = load_lint_config(root)
    targets: list[str] = args.targets or []

    reports = []
    exp_ids = state.experiment_ids
    hyp_ids = state.hypothesis_ids
    axis_ids = state.axis_ids

    def lint_one_experiment(e: Experiment) -> None:
        reports.append(lint_record(
            e.record, cfg=cfg, exp_id=e.id, root=root,
            known_experiments=exp_ids, known_axes=axis_ids,
        ))

    def lint_one_decision(d: dict, dec_id: str) -> None:
        reports.append(lint_decision(
            d, cfg=cfg, dec_id=dec_id,
            known_experiments=exp_ids, known_hypotheses=hyp_ids,
        ))

    def lint_the_backlog() -> None:
        per_idea, whole = lint_backlog(
            state.backlog, cfg=cfg, policy=policy(state),
            known_axes=axis_ids, known_experiments=exp_ids,
        )
        reports.extend(per_idea)
        reports.append(whole)

    def lint_the_domain() -> None:
        per_fact, whole = lint_domain(
            state.domain, cfg=cfg, policy=policy(state),
            known_experiments=exp_ids, known_ideas=state.idea_ids,
        )
        reports.extend(per_fact)
        reports.append(whole)

    def lint_the_landscape() -> None:
        per_source, whole = lint_landscape(
            state.landscape, cfg=cfg, policy=policy(state), known_ideas=state.idea_ids,
        )
        reports.extend(per_source)
        reports.append(whole)

    if targets:
        for t in targets:
            # ファイルパスでも ID でも受ける（hook から呼ぶため）
            p = Path(t)
            if p.suffix in (".yaml", ".yml") and p.exists():
                resolved = p.resolve()
                if resolved.parent.parent.name == "experiments":
                    name = resolved.parent.name
                    e = next((x for x in state.experiments if x.id == name), None)
                    if e:
                        lint_one_experiment(e)
                        continue
                if resolved.parent.name == "decisions":
                    lint_one_decision(_load_yaml(resolved), resolved.stem)
                    continue
                if resolved.parent.name == "ideas":
                    lint_the_backlog()
                    continue
                if resolved.name == "landscape.yaml":
                    lint_the_landscape()
                    continue
                if resolved.name == "domain.yaml":
                    lint_the_domain()
                    continue
                print(f"{t}: 記録でも決定でもないので検査しない。")
                continue
            e = next((x for x in state.experiments if x.id == t), None)
            if e:
                lint_one_experiment(e)
                continue
            if t in ("backlog", "ideas"):
                lint_the_backlog()
                continue
            if t in ("landscape", "recon"):
                lint_the_landscape()
                if t == "recon":
                    lint_the_domain()
                continue
            if t == "domain":
                lint_the_domain()
                continue
            dp = decision_path(t, root)
            if dp.exists():
                lint_one_decision(_load_yaml(dp), t)
                continue
            print(f"{t}: 見つからない。", file=sys.stderr)
            return 2
    else:
        for e in state.experiments:
            lint_one_experiment(e)
        if decisions_dir(root).exists():
            for f in sorted(decisions_dir(root).glob("*.yaml")):
                lint_one_decision(_load_yaml(f), f.stem)
        lint_the_backlog()
        lint_the_landscape()
        lint_the_domain()

    if not reports:
        print("検査対象が無い。")
        return 0

    n_err = 0
    for r in reports:
        if r.findings or args.verbose:
            print(r.format())
        n_err += len(r.errors)

    total = len(reports)
    if n_err:
        print(f"\n{BOLD}{total} 件中 {n_err} 件の error。{RESET}"
              " 語彙規則だけは record の lint_waived で理由つきで抜けられる。"
              " 反証条件・改竄検知・スキーマは抜けられない。")
        return 1
    print(f"\n{total} 件すべて ok。")
    return 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    root = repo_root()
    state = load_state(root)
    ph = current_phase(state)
    trust = cv_trust(state)
    cal = calibration(state)

    print(f"{BOLD}phase{RESET}        {ph.get('id')}  {DIM}{ph.get('goal','')}{RESET}")
    print(f"{BOLD}指標実装{RESET}     verified={state.metric_verified}"
          + ("" if state.metric_verified else f"  {DIM}← ここが false の間は打ち切れない{RESET}"))
    recon = recon_findings(state)
    n_src = len(state.sources)
    n_fact = len(state.facts)
    n_assumed = len([f for f in state.facts if f.get("confidence") == "assumed"])
    print(f"{BOLD}地固め{RESET}       "
          + (f"済（出典 {n_src} 件 / 事実 {n_fact} 件）" if not recon
             else f"{DIM}未 — 出典 {n_src} 件 / 事実 {n_fact} 件。"
                  f"modeling 系の軸に進めない{RESET}"))
    if n_assumed:
        print(f"{BOLD}未検証の仮定{RESET} {n_assumed} 件  "
              f"{DIM}← 残っている間は打ち切れない{RESET}")
    streak = inconclusive_streak(state)
    if streak:
        limit = int(policy(state)["inconclusive_streak_limit"])
        mark = "  ← 次は error_analysis 軸" if streak >= limit else ""
        print(f"{BOLD}判定できず{RESET}   {streak} 回連続{DIM}{mark}{RESET}")
    print(f"{BOLD}CV信頼性{RESET}     {trust.summary()}")
    print(f"{BOLD}実験{RESET}         {len(state.experiments)} 本   "
          f"{BOLD}決定{RESET} {len(state.decisions)} 件（決着 {cal.n} 件）")

    counts = state.tier_counts(window=10)
    quota = ph.get("tier_quota") or {}
    total = sum(counts.values()) or 1
    tiers = "  ".join(
        f"{t}={counts[t]}({counts[t]/total:.0%}/目標{quota.get(t,0):.0%})"
        for t in ("exploit", "explore", "moonshot")
    )
    print(f"{BOLD}tier(直近10){RESET}  {tiers}")

    if cal.n:
        hr = f"{cal.hit_rate:.0%}" if cal.hit_rate is not None else "n/a"
        mr = f"{cal.mean_ratio:.2f}" if cal.mean_ratio is not None else "n/a"
        print(f"{BOLD}決定の較正{RESET}   hit={cal.hit} miss={cal.miss} "
              f"inconclusive={cal.inconclusive}  的中率={hr}  実際/期待={mr}")

    print(f"{BOLD}アイデア在庫{RESET} open={len(state.open_ideas())} / 全{len(state.ideas)}")

    print(f"\n{BOLD}探索の網羅{RESET}")
    for a in sorted(state.axis_ids):
        st = state.axis_status(a)
        n = len(state.axis_experiments(a))
        mark = {"untouched": "未着手", "open": "進行中", "saturated": "飽和  ", "retired": "打切り"}[st]
        print(f"  {mark}  {a:<18} 実験 {n} 本")

    warn = conservatism_findings(state) + gate_backlog(state) + recon
    if warn:
        print(f"\n{BOLD}指摘{RESET}")
        for f in warn:
            print(f.format())
    return 0


# ---------------------------------------------------------------------------
# table / lb / cvlb
# ---------------------------------------------------------------------------

def cmd_table(args: argparse.Namespace) -> int:
    state = load_state()
    cols = [c.strip() for c in args.columns.split(",") if c.strip()]
    header = ["id", "tier", "axes", *cols]
    rows = [header]
    for e in state.experiments:
        row = [e.id, e.tier or "-", ",".join(e.axes) or "-"]
        for c in cols:
            v = e.metric(c)
            row.append(f"{v:.5f}" if isinstance(v, float) else ("-" if v is None else str(v)))
        rows.append(row)

    widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
    for i, r in enumerate(rows):
        print("| " + " | ".join(c.ljust(widths[j]) for j, c in enumerate(r)) + " |")
        if i == 0:
            print("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    return 0


def cmd_lb(args: argparse.Namespace) -> int:
    root = repo_root()
    metrics_mod.annotate(
        args.experiment, path="metrics.lb.public", value=float(args.public),
        by="expctl lb", root=root,
    )
    if args.private is not None:
        metrics_mod.annotate(
            args.experiment, path="metrics.lb.private", value=float(args.private),
            by="expctl lb", root=root,
        )
    print(f"{args.experiment}: LB を記録した（手入力として provenance に残る）。")
    return cmd_cvlb(argparse.Namespace())


def cmd_cvlb(args: argparse.Namespace) -> int:
    root = repo_root()
    state = load_state(root)
    pairs = state.cv_lb_pairs()
    trust = cv_trust(state)

    lines = [
        "# CV と LB の対応",
        "",
        "<!-- expctl cvlb が生成する。手で編集しない。 -->",
        "",
        f"- ペア数: {len(pairs)}",
        "- 順位相関(Spearman): " + ("n/a" if trust.rho is None else f"{trust.rho:+.3f}"),
        f"- 判定: **{trust.status}**",
        "",
        "| experiment | cv.mean | lb.public |",
        "|---|---|---|",
    ]
    for eid, cv, lb in pairs:
        lines.append(f"| {eid} | {cv:.5f} | {lb:.5f} |")
    if not pairs:
        lines.append("| (まだ無い) | | |")
    lines += [
        "",
        "判定の意味:",
        "",
        "- `unverified` — ペアが足りない。CV だけを根拠にした打ち切りはできない。",
        "- `broken` — 相関が低い。CV は採点を再現していない。CV 戦略自体を実験対象にする。",
        "- `calibrated` — CV を根拠にしてよい。",
        "",
    ]
    out = knowledge_dir(root) / "cv_lb.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"{out} を更新した（{trust.summary()}）。")
    return 0


# ---------------------------------------------------------------------------
# decide
# ---------------------------------------------------------------------------

def cmd_decide_new(args: argparse.Namespace) -> int:
    root = repo_root()
    state = load_state(root)

    if args.type == "stop":
        findings = gate_stop_decision(state)
        blocked = _print_gates(findings, header="打ち切りゲート")
        if blocked and not args.force:
            print("\n打ち切りの決定は作らなかった。"
                  " 「打ち手がない」ではなく、上のどれかがまだ残っている。", file=sys.stderr)
            return 1

    dec_id = args.id or _next_id(decisions_dir(root), "dec", suffix=".yaml")
    path = decision_path(dec_id, root)
    if path.exists():
        print(f"{path} はすでにある。", file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    text = decision_template(dec_id, _now()).replace("type: run", f"type: {args.type}")
    path.write_text(text, encoding="utf-8")
    print(f"{path} を作った。埋めたら `expctl lint {dec_id}` を通す。")
    return 0


def cmd_decide_close(args: argparse.Namespace) -> int:
    root = repo_root()
    state = load_state(root)
    path = decision_path(args.decision, root)
    if not path.exists():
        print(f"{path} が無い。", file=sys.stderr)
        return 2

    exp = next((e for e in state.experiments if e.id == args.experiment), None)
    if exp is None:
        print(f"実験 {args.experiment} が無い。", file=sys.stderr)
        return 2

    doc = _load_yaml(path)
    j = judge(state, doc, exp)
    doc["outcome"] = {
        "experiment": exp.id,
        "result": j.result,
        "metric": j.metric,
        "baseline": j.baseline,
        "actual": j.actual,
        "delta": j.delta,
        "predicted": j.predicted,
        "ratio": round(j.ratio, 3) if j.ratio is not None else None,
        "noise_floor": j.noise,
        "judged_at": _now(),
        "judged_by": "expctl decide close",
        "note": args.note or "",
    }
    _dump_yaml(path, doc)

    print(f"{args.decision}: {BOLD}{j.result}{RESET}  {j.note}")
    if j.result == "inconclusive":
        print(f"{DIM}判定できないことと、効かないことは別。"
              f" この決定を miss として次に進めない。{RESET}")
    return 0


# ---------------------------------------------------------------------------
# saturate / idea
# ---------------------------------------------------------------------------

def cmd_saturate(args: argparse.Namespace) -> int:
    root = repo_root()
    state = load_state(root)
    path = knowledge_dir(root) / "coverage.yaml"
    doc = _load_yaml(path) if path.exists() else state.coverage

    axis = next((a for a in doc.get("axes") or [] if a.get("id") == args.axis), None)
    if axis is None:
        print(f"軸 {args.axis} が coverage.yaml に無い。", file=sys.stderr)
        return 2

    n = len(state.axis_experiments(args.axis))
    if n == 0 and not args.force:
        print(f"軸 {args.axis} にはまだ実験が1本も当たっていない。"
              " 触らずに飽和とは言えない。", file=sys.stderr)
        return 1

    axis["status"] = "retired" if args.retire else "saturated"
    axis["saturation"] = {
        "criterion": args.criterion,
        "declared_at": _now(),
        "evidence_experiments": [e.strip() for e in (args.evidence or "").split(",") if e.strip()],
        "experiment_count": n,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _dump_yaml(path, doc)
    verb = "打ち切り" if args.retire else "飽和"
    print(f"軸 {args.axis} を{verb}として記録した（実験 {n} 本）。")
    if not args.retire:
        print("これ以降、この軸への tier: exploit の実験はゲートで止まる。")
    return 0


def _backlog_path(root: Path) -> Path:
    return root / "ideas" / "backlog.yaml"


def _load_backlog(root: Path) -> dict:
    path = _backlog_path(root)
    return _load_yaml(path) if path.exists() else {"ideas": []}


def cmd_idea_add(args: argparse.Namespace) -> int:
    root = repo_root()
    state = load_state(root)
    cfg = load_lint_config(root)

    axes = [a.strip() for a in args.axes.split(",") if a.strip()]
    idea = {
        "id": args.id or _next_id_in_backlog(state),
        "created_at": _now(),
        "action": args.action,
        "axes": axes,
        "tier": args.tier,
        "expected": {
            "metric": args.metric,
            "direction": args.direction,
            "magnitude": float(args.magnitude),
        },
        "evidence": args.evidence,
        "cost": float(args.cost),
        "status": "open",
        "source": args.source,
        "experiment": None,
        "retired_reason": None,
    }

    report = lint_idea(idea, cfg=cfg, index=0, known_axes=state.axis_ids,
                       known_experiments=state.experiment_ids)
    if report.errors:
        print(report.format())
        print("\n在庫に入れなかった。在庫の下限は「打ち手がない」と言わせないための"
              "仕組みなので、中身の薄い項目を数に入れない。", file=sys.stderr)
        return 1

    backlog = _load_backlog(root)
    backlog.setdefault("ideas", []).append(idea)
    _backlog_path(root).parent.mkdir(parents=True, exist_ok=True)
    _dump_yaml(_backlog_path(root), backlog)
    print(f"{idea['id']} を在庫に入れた。")
    return 0


def _next_id_in_backlog(state: State) -> str:
    n = 0
    for i in state.ideas:
        ident = str(i.get("id") or "") if isinstance(i, dict) else ""
        if ident.startswith("i") and ident[1:].isdigit():
            n = max(n, int(ident[1:]))
    return f"i{n + 1:04d}"


def cmd_idea_retire(args: argparse.Namespace) -> int:
    root = repo_root()
    backlog = _load_backlog(root)
    idea = next((i for i in backlog.get("ideas") or []
                 if isinstance(i, dict) and i.get("id") == args.idea), None)
    if idea is None:
        print(f"{args.idea} が在庫に無い。", file=sys.stderr)
        return 2

    reason = args.reason.strip()
    if len(reason) < 20:
        print("捨てる理由が20字未満。理由なく捨てられると、在庫の下限は意味を失う。",
              file=sys.stderr)
        return 1

    idea["status"] = "retired"
    idea["retired_reason"] = reason
    _dump_yaml(_backlog_path(root), backlog)
    print(f"{args.idea} を retired にした。")

    findings = gate_backlog(load_state(root))
    if _print_gates(findings, header="在庫の状態"):
        print("\n在庫が下限を割った。次の実験に進む前に補充する。", file=sys.stderr)
        return 1
    return 0


def _mark_idea(root: Path, idea_id: str, *, status: str, experiment: str | None) -> None:
    """在庫の項目に実験を結びつける。手で status を書き換えさせないため。"""
    path = _backlog_path(root)
    if not path.exists():
        return
    backlog = _load_yaml(path)
    for i in backlog.get("ideas") or []:
        if isinstance(i, dict) and i.get("id") == idea_id:
            i["status"] = status
            if experiment:
                i["experiment"] = experiment
            _dump_yaml(path, backlog)
            return


def cmd_landscape_check(args: argparse.Namespace) -> int:
    """出典の URL が実在するかを確かめる。

    捏造された出典が混ざると evidence の仕組み全体が意味を失うので、
    機械で確かめられる部分だけでも確かめる。
    """
    import urllib.error
    import urllib.request

    state = load_state()
    sources = state.sources
    if not sources:
        print("出典がまだ無い。")
        return 0

    bad = 0
    for src in sources:
        url = str(src.get("url") or "").strip()
        ident = src.get("id", "?")
        if not url:
            print(f"{ident}: URL が無い")
            bad += 1
            continue
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "expctl"})
        try:
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                print(f"{ident}: {resp.status} {url}")
        except urllib.error.HTTPError as e:
            # 405 は HEAD 非対応。到達はしているので生存とみなす。
            if e.code in (403, 405):
                print(f"{ident}: {e.code}（到達はした） {url}")
            else:
                print(f"{ident}: HTTP {e.code} {url}")
                bad += 1
        except Exception as e:  # noqa: BLE001 — ネットワークの失敗は種類を問わず報告する
            print(f"{ident}: 到達できない（{type(e).__name__}） {url}")
            bad += 1

    if bad:
        print(f"\n{bad} 件が確認できなかった。"
              " 開けない出典は landscape から消す。実際に読んでいない出典を残さない。")
        return 1
    print(f"\n{len(sources)} 件すべて到達できた。")
    return 0


def cmd_idea_list(args: argparse.Namespace) -> int:
    state = load_state()
    ideas = state.ideas if args.all else state.open_ideas()

    def score(i: dict) -> float:
        exp = i.get("expected") or {}
        gain = exp.get("magnitude")
        cost = i.get("cost")
        if not isinstance(gain, (int, float)) or not isinstance(cost, (int, float)) or cost <= 0:
            return -1.0
        return float(gain) / float(cost)

    ideas = sorted(ideas, key=score, reverse=True)
    if not ideas:
        print("在庫が空。補充する。")
        return 0
    for i in ideas:
        s = score(i)
        s_txt = f"{s:.4f}" if s >= 0 else "  n/a "
        exp = i.get("expected") or {}
        print(f"{BOLD}{i.get('id','?'):<8}{RESET} gain/cost={s_txt}  "
              f"tier={i.get('tier','?'):<8} axes={','.join(i.get('axes') or []) or '-'}  "
              f"status={i.get('status','open')}")
        action = " ".join(str(i.get("action") or "").split())
        print(f"         {action[:100]}")
        print(f"         {DIM}期待 {exp.get('metric','?')} {exp.get('direction','?')} "
              f"{exp.get('magnitude','?')} / 根拠 {i.get('evidence','?')}{RESET}")
    return 0


# ---------------------------------------------------------------------------
# brief
# ---------------------------------------------------------------------------

def cmd_brief(args: argparse.Namespace) -> int:
    from .brief import build as build_brief

    root = repo_root()
    text = build_brief(load_state(root), root)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"{out} に書いた。")
    else:
        print(text, end="")
    return 0


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def cmd_render(args: argparse.Namespace) -> int:
    import re

    root = repo_root()
    state = load_state(root)
    e = next((x for x in state.experiments if x.id == args.experiment), None)
    if e is None:
        print(f"実験 {args.experiment} が無い。", file=sys.stderr)
        return 2

    def sub(m: "re.Match[str]") -> str:
        try:
            v = metrics_mod.resolve(e.metrics or {}, m.group(1).strip())
        except KeyError:
            return m.group(0)
        return f"{v:.5f}" if isinstance(v, float) else str(v)

    text = record_path(e.id, root).read_text(encoding="utf-8")
    print(re.sub(r"\$\{([^}]+)\}", sub, text))
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="expctl", description="コンペ実験の記録と意思決定")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="実験を1本立てる（ゲート判定を通してから）")
    n.add_argument("--tier", required=True, choices=("exploit", "explore", "moonshot"))
    n.add_argument("--axes", default="", help="触る軸をカンマ区切りで")
    n.add_argument("--based-on", default=None, help="差分の基準になる実験 ID")
    n.add_argument("--decision", default=None)
    n.add_argument("--idea", default=None)
    n.add_argument("--id", default=None)
    n.add_argument("--force", action="store_true", help="ゲートを押し切る")
    n.set_defaults(func=cmd_new)

    l = sub.add_parser("lint", help="記録と決定を検査する")
    l.add_argument("targets", nargs="*", help="実験ID / 決定ID / ファイルパス。省略で全件")
    l.add_argument("-v", "--verbose", action="store_true")
    l.set_defaults(func=cmd_lint)

    s = sub.add_parser("status", help="phase・網羅・信頼性・較正・消極性を出す")
    s.set_defaults(func=cmd_status)

    t = sub.add_parser("table", help="実験一覧を metrics から作る")
    t.add_argument("--columns", default="cv.mean,cv.std,lb.public")
    t.set_defaults(func=cmd_table)

    b = sub.add_parser("lb", help="LB スコアを手入力で差す（経路が記録に残る）")
    b.add_argument("experiment")
    b.add_argument("--public", required=True)
    b.add_argument("--private", default=None)
    b.set_defaults(func=cmd_lb)

    c = sub.add_parser("cvlb", help="CV-LB 対応表と信頼性判定を更新する")
    c.set_defaults(func=cmd_cvlb)

    d = sub.add_parser("decide", help="意思決定の台帳")
    dsub = d.add_subparsers(dest="subcmd", required=True)
    dn = dsub.add_parser("new")
    dn.add_argument("--type", default="run", choices=("run", "stop", "pivot"))
    dn.add_argument("--id", default=None)
    dn.add_argument("--force", action="store_true")
    dn.set_defaults(func=cmd_decide_new)
    dc = dsub.add_parser("close", help="期待と実際を突き合わせて hit/miss/inconclusive を機械判定")
    dc.add_argument("decision")
    dc.add_argument("--experiment", required=True)
    dc.add_argument("--note", default="")
    dc.set_defaults(func=cmd_decide_close)

    sa = sub.add_parser("saturate", help="軸を飽和（または打ち切り）として宣言する")
    sa.add_argument("axis")
    sa.add_argument("--criterion", required=True, help="何をもって試し尽くしたと言えるのか")
    sa.add_argument("--evidence", default="", help="根拠の実験 ID をカンマ区切りで")
    sa.add_argument("--retire", action="store_true", help="飽和ではなく打ち切りにする")
    sa.add_argument("--force", action="store_true")
    sa.set_defaults(func=cmd_saturate)

    i = sub.add_parser("idea", help="アイデア在庫")
    isub = i.add_subparsers(dest="subcmd", required=True)
    il = isub.add_parser("list", help="gain/cost 順に並べる")
    il.add_argument("--all", action="store_true")
    il.set_defaults(func=cmd_idea_list)

    ia = isub.add_parser("add", help="在庫に足す。検証を通らないと入らない")
    ia.add_argument("--action", required=True, help="何をするか。1実験の粒度で20字以上")
    ia.add_argument("--axes", required=True, help="触る軸をカンマ区切りで")
    ia.add_argument("--tier", required=True, choices=("exploit", "explore", "moonshot"))
    ia.add_argument("--magnitude", required=True, help="期待する変化量（正の数値）")
    ia.add_argument("--evidence", required=True,
                    help="出典。priors/common.md#c01 / exp0003 / dec0002 / URL のいずれかを含める")
    ia.add_argument("--metric", default="cv.mean")
    ia.add_argument("--direction", default="increase", choices=("increase", "decrease"))
    ia.add_argument("--cost", default="1.0", help="1.0 が標準的な1実験")
    ia.add_argument("--source", default=None)
    ia.add_argument("--id", default=None)
    ia.set_defaults(func=cmd_idea_add)

    ir = isub.add_parser("retire", help="在庫から捨てる。理由が要る")
    ir.add_argument("idea")
    ir.add_argument("--reason", required=True, help="なぜ捨てるのか。20字以上")
    ir.set_defaults(func=cmd_idea_retire)

    ls = sub.add_parser("landscape", help="地固め（knowledge/landscape.yaml）")
    lsub = ls.add_subparsers(dest="subcmd", required=True)
    lc = lsub.add_parser("check", help="出典の URL が実在するかを確かめる")
    lc.add_argument("--timeout", type=float, default=10.0)
    lc.set_defaults(func=cmd_landscape_check)

    r = sub.add_parser("render", help="${metrics...} を実値に置いて記録を読む")
    r.add_argument("experiment")
    r.set_defaults(func=cmd_render)

    br = sub.add_parser("brief", help="引き継ぎを出す。セッション開始時に自動で走る")
    br.add_argument("--out", default=None, help="ファイルに書き出す（既定は標準出力）")
    br.set_defaults(func=cmd_brief)

    rp = sub.add_parser("report", help="記録からレポートを組み立てる")
    rp.add_argument("--out", default=None)
    from .report import cmd_report
    rp.set_defaults(func=cmd_report)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:  # noqa: BLE001 — CLI の境界で握る
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
