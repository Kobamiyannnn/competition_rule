"""記述欄の検証。

検証は3層。
  1. スキーマ  : 欄の有無と型と enum
  2. 構造      : 反証条件、metrics 参照、裸の数値、参照の解決
  3. 語彙      : ヘッジ・比喩・評価語・空句・曖昧な量的表現

lint_waived で抜けられるのは語彙規則だけ。反証条件と改竄検知は抜けられない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Iterable

from . import metrics as metrics_mod
from .schema import (
    DECISION_RESULTS,
    DECISION_TYPES,
    DIRECTIONS,
    HYPOTHESIS_STATUS,
    IDEA_STATUS,
    TIERS,
)

# 抜け道を認めない規則。ここを緩めると基盤の意味がなくなる。
UNWAIVABLE = (
    "integrity.",
    "falsification.",
    "schema.",
    "ref.",
    "link.",
    "observation.no_ref",
)

SEVERITY_ORDER = {"allow": 0, "waived": 1, "warn": 2, "error": 3}

_REF = re.compile(r"\$\{([^}]+)\}")
# 裸の数値: 小数、パーセント、3桁以上の整数。設定値らしい小さな整数は見逃す。
_BARE_NUMBER = re.compile(r"(?<![\w.])(?:\d+\.\d+|\d+(?:\.\d+)?%|\d{3,})(?![\w.])")


@dataclass
class Finding:
    rule: str
    severity: str
    field: str
    message: str
    excerpt: str = ""

    def format(self) -> str:
        mark = {"error": "ERROR", "warn": "WARN ", "waived": "WAIVE"}.get(self.severity, self.severity)
        tail = f"  << {self.excerpt} >>" if self.excerpt else ""
        return f"  [{mark}] {self.field} / {self.rule}: {self.message}{tail}"


@dataclass
class Report:
    target: str
    findings: list[Finding] = dc_field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, **kw: Any) -> None:
        self.findings.append(Finding(**kw))

    def format(self) -> str:
        if not self.findings:
            return f"{self.target}: ok"
        head = f"{self.target}: {len(self.errors)} error / {len(self.findings)} finding"
        body = "\n".join(f.format() for f in sorted(
            self.findings, key=lambda f: -SEVERITY_ORDER.get(f.severity, 0)
        ))
        return f"{head}\n{body}"


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------

def _visible_len(text: str) -> int:
    """空白と改行を除いた文字数。全角・半角は区別しない。"""
    return len(re.sub(r"\s+", "", text or ""))


def _strip_refs(text: str) -> str:
    return _REF.sub(" ", text or "")


def _match_any(text: str, patterns: Iterable[str]) -> str | None:
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0)
    return None


def _waived_rules(doc: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for w in doc.get("lint_waived") or []:
        if isinstance(w, dict) and w.get("rule"):
            out[str(w["rule"])] = str(w.get("reason") or "")
    return out


def _texts(doc: dict, kind: str) -> list[tuple[str, str, str]]:
    """(論理フィールド名, 表示用の場所, 本文) を返す。"""
    out: list[tuple[str, str, str]] = []

    def push(logical: str, where: str, value: Any) -> None:
        if isinstance(value, str) and value.strip():
            out.append((logical, where, value))

    if kind == "record":
        push("record.change.summary", "change.summary", (doc.get("change") or {}).get("summary"))
        push("record.observation", "observation", doc.get("observation"))
        push("record.notes", "notes", doc.get("notes"))
        for i, h in enumerate(doc.get("hypothesis") or []):
            if not isinstance(h, dict):
                continue
            hid = h.get("id") or f"#{i}"
            push("record.hypothesis.statement", f"hypothesis[{hid}].statement", h.get("statement"))
            push("record.hypothesis.falsification", f"hypothesis[{hid}].falsification", h.get("falsification"))
    elif kind == "idea":
        push("idea.action", "action", doc.get("action"))
    elif kind == "decision":
        push("decision.question", "question", doc.get("question"))
        for i, o in enumerate(doc.get("options") or []):
            if not isinstance(o, dict):
                continue
            oid = o.get("id") or f"#{i}"
            push("decision.options.action", f"options[{oid}].action", o.get("action"))
        push("decision.rationale.reason", "rationale.reason", (doc.get("rationale") or {}).get("reason"))
        outcome = doc.get("outcome")
        if isinstance(outcome, dict):
            push("decision.outcome.note", "outcome.note", outcome.get("note"))
    return out


# ---------------------------------------------------------------------------
# 語彙・字数
# ---------------------------------------------------------------------------

def lint_prose_field(
    logical: str, where: str, text: str, cfg: dict, report: Report,
    *, waived: dict[str, str] | None = None,
) -> None:
    """記述欄1つに、字数・語彙・裸の数値の規則をかける。"""
    limits = cfg.get("limits", {})
    profiles = cfg.get("profiles", {})
    field_profiles = cfg.get("field_profiles", {})
    vocab = cfg.get("vocab", {})
    bare_ok = set((cfg.get("structure") or {}).get("bare_number_allowed_fields") or [])
    waived = waived or {}

    limit = limits.get(logical)
    if limit and _visible_len(text) > limit:
        report.add(
            rule="length.exceeded", severity="error", field=where,
            message=f"{_visible_len(text)}字。上限 {limit}字。削る。",
        )

    # TODO のまま提出させない
    if re.search(r"\bTODO\b", text):
        report.add(
            rule="schema.todo_left", severity="error", field=where,
            message="TODO が残っている。",
        )

    profile = profiles.get(field_profiles.get(logical, "interpretive"), {})
    stripped = _strip_refs(text)

    for rule_name, rule in vocab.items():
        severity = profile.get(rule_name, "allow")
        if severity == "allow":
            continue
        hit = _match_any(stripped, rule.get("patterns") or [])
        if not hit:
            continue
        full_rule = f"vocab.{rule_name}"
        if full_rule in waived:
            severity = "waived"
        report.add(
            rule=full_rule, severity=severity, field=where,
            message=rule.get("message") or "禁止語彙。", excerpt=hit,
        )

    if logical not in bare_ok:
        m = _BARE_NUMBER.search(stripped)
        if m:
            report.add(
                rule="numeric.bare", severity="error", field=where,
                message="裸の数値。metrics.json を ${...} で参照する。",
                excerpt=m.group(0),
            )


def _lint_prose(doc: dict, kind: str, cfg: dict, report: Report) -> None:
    waived = _waived_rules(doc)
    for logical, where, text in _texts(doc, kind):
        lint_prose_field(logical, where, text, cfg, report, waived=waived)


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------

def lint_record(
    doc: dict,
    *,
    cfg: dict,
    exp_id: str,
    root: Path | None = None,
    known_experiments: set[str] | None = None,
    known_axes: set[str] | None = None,
) -> Report:
    report = Report(target=f"experiments/{exp_id}/record.yaml")

    for key in ("id", "created_at", "tier", "change", "observation"):
        if doc.get(key) in (None, "", [], {}):
            report.add(rule="schema.missing", severity="error", field=key,
                       message="必須の欄が空。")

    if doc.get("id") not in (None, exp_id):
        report.add(rule="schema.id_mismatch", severity="error", field="id",
                   message=f"ディレクトリ名 {exp_id} と id が食い違う。")

    tier = doc.get("tier")
    if tier is not None and tier not in TIERS:
        report.add(rule="schema.enum", severity="error", field="tier",
                   message=f"tier は {'/'.join(TIERS)} のいずれか。得られた値: {tier!r}")

    if known_axes is not None:
        for ax in doc.get("axes") or []:
            if ax not in known_axes:
                report.add(rule="schema.unknown_axis", severity="error", field="axes",
                           message=f"coverage.yaml に無い軸: {ax!r}")
    if not (doc.get("axes") or []):
        report.add(rule="schema.no_axis", severity="warn", field="axes",
                   message="触った軸が空。網羅マップが更新されない。")

    based_on = doc.get("based_on")
    if based_on and known_experiments is not None and based_on not in known_experiments:
        report.add(rule="link.based_on_missing", severity="error", field="based_on",
                   message=f"存在しない実験を参照している: {based_on!r}")

    # 反証条件。ここが基盤の核。
    _lint_hypotheses(doc, cfg, report)

    # metrics.json の改竄検知と参照解決
    _lint_metrics_link(doc, exp_id, root, report)

    _lint_prose(doc, "record", cfg, report)
    _lint_waivers(doc, report)
    return report


def _lint_hypotheses(doc: dict, cfg: dict, report: Report) -> None:
    structure = cfg.get("structure", {})
    cond_markers = structure.get("falsification_condition_markers") or []
    obs_markers = structure.get("falsification_observable_markers") or []

    for i, h in enumerate(doc.get("hypothesis") or []):
        where = f"hypothesis[{h.get('id') or i}]" if isinstance(h, dict) else f"hypothesis[{i}]"
        if not isinstance(h, dict):
            report.add(rule="schema.type", severity="error", field=where,
                       message="hypothesis の要素はマッピング。")
            continue

        hid_raw = h.get("id")
        if not (str(hid_raw).strip() if hid_raw is not None else ""):
            report.add(rule="schema.missing", severity="error", field=f"{where}.id",
                       message="仮説には ID が要る（決定から参照するため）。")

        statement = (h.get("statement") or "").strip()
        falsification = (h.get("falsification") or "").strip()

        if not statement:
            report.add(rule="schema.missing", severity="error", field=f"{where}.statement",
                       message="statement が空。仮説を書かないなら要素ごと消す。")

        if not falsification:
            report.add(
                rule="falsification.missing", severity="error", field=f"{where}.falsification",
                message="反証条件が無い。書けないなら、この仮説は記録に入れない（要素ごと消す）。",
            )
        else:
            has_cond = _match_any(falsification, cond_markers)
            has_obs = _match_any(_strip_refs(falsification), obs_markers) or _REF.search(falsification)
            if not has_cond:
                report.add(
                    rule="falsification.not_conditional", severity="error",
                    field=f"{where}.falsification",
                    message="条件文になっていない。「〜なら / 〜の場合」の形で書く。",
                )
            if not has_obs:
                report.add(
                    rule="falsification.not_observable", severity="error",
                    field=f"{where}.falsification",
                    message="観測可能な条件が無い。閾値・比較・metrics 参照のいずれかを含める。",
                )

        status = h.get("status", "open")
        if status not in HYPOTHESIS_STATUS:
            report.add(rule="schema.enum", severity="error", field=f"{where}.status",
                       message=f"status は {'/'.join(HYPOTHESIS_STATUS)} のいずれか。")
        if status in ("supported", "refuted") and not h.get("resolved_by"):
            report.add(rule="schema.missing", severity="error", field=f"{where}.resolved_by",
                       message="決着した仮説には、決着させた実験 ID が要る。")


def _suggest_keys(doc: dict, ref: str) -> str:
    """解決できなかった参照に対して、その一つ上の階層で実在するキーを挙げる。"""
    parts = ref.split(".")
    cur: object = doc
    walked: list[str] = []
    for name in parts:
        base = re.sub(r"\[\d+\]", "", name)
        if isinstance(cur, dict) and base in cur:
            cur = cur[base]
            walked.append(base)
            continue
        if isinstance(cur, dict):
            avail = ", ".join(sorted(cur)[:12]) or "(空)"
            prefix = ".".join(walked)
            at = f"{prefix} の下" if prefix else "最上位"
            return f" {at}にあるのは: {avail}"
        return ""
    return ""


def _lint_metrics_link(doc: dict, exp_id: str, root: Path | None, report: Report) -> None:
    refs: list[tuple[str, str]] = []
    for _logical, where, text in _texts(doc, "record"):
        for m in _REF.finditer(text):
            refs.append((where, m.group(1).strip()))

    # 観測は測定値に接地させる。config への参照は測定値ではないので数に入れない。
    measured = [r for w, r in refs if w == "observation" and r.startswith("metrics.")]
    if not measured:
        report.add(
            rule="observation.no_ref", severity="error", field="observation",
            message="測定値への参照が無い。観測は ${metrics....} で数値に接地させる"
                    "（設定値 ${config....} は測定値ではないので数に入らない）。",
        )

    try:
        mdoc = metrics_mod.load(exp_id, root)
    except FileNotFoundError:
        if refs:
            report.add(rule="ref.no_metrics", severity="error", field="observation",
                       message="metrics.json が無いのに参照している。実験を回して書き出す。")
        else:
            report.add(rule="ref.no_metrics", severity="error", field="metrics.json",
                       message="metrics.json が無い。実験スクリプトから expkit.metrics.write で書く。")
        return

    try:
        metrics_mod.verify(exp_id, root)
    except metrics_mod.MetricsTampered as e:
        report.add(rule="integrity.metrics_tampered", severity="error",
                   field="metrics.json", message=str(e))

    for where, ref in refs:
        try:
            metrics_mod.resolve(mdoc, ref)
        except KeyError:
            report.add(rule="ref.unresolved", severity="error", field=where,
                       message=f"metrics.json に無い参照: ${{{ref}}}." + _suggest_keys(mdoc, ref))

    if (mdoc.get("git") or {}).get("dirty"):
        report.add(rule="repro.dirty_worktree", severity="warn", field="metrics.json",
                   message="コミットされていない変更がある状態で実験が回っている。再現できない。")


def _lint_waivers(doc: dict, report: Report) -> None:
    for i, w in enumerate(doc.get("lint_waived") or []):
        where = f"lint_waived[{i}]"
        if not isinstance(w, dict):
            report.add(rule="schema.type", severity="error", field=where,
                       message="lint_waived の要素はマッピング。")
            continue
        rule = str(w.get("rule") or "")
        reason = str(w.get("reason") or "").strip()
        if not rule:
            report.add(rule="schema.missing", severity="error", field=f"{where}.rule",
                       message="どの規則を抜けるのか書く。")
        if any(rule.startswith(p) for p in UNWAIVABLE):
            report.add(rule="waiver.forbidden", severity="error", field=where,
                       message=f"{rule} は抜けられない規則。")
        if len(reason) < 20:
            report.add(rule="waiver.thin_reason", severity="error", field=f"{where}.reason",
                       message="抜ける理由が20字未満。抜け道の乱用を防ぐため理由を書く。")


# ---------------------------------------------------------------------------
# idea — アイデア在庫
#
# 在庫の下限は「打ち手がない」と言わせないための仕組みなので、
# 中身の薄い項目で埋められると形骸化する。ここが検証の要。
# ---------------------------------------------------------------------------

def lint_idea(
    idea: dict,
    *,
    cfg: dict,
    index: int,
    known_axes: set[str] | None = None,
    known_experiments: set[str] | None = None,
) -> Report:
    ident = idea.get("id") if isinstance(idea, dict) else None
    report = Report(target=f"ideas/backlog.yaml [{ident or f'#{index}'}]")

    if not isinstance(idea, dict):
        report.add(rule="schema.type", severity="error", field=f"ideas[{index}]",
                   message="在庫の要素はマッピング。")
        return report

    for key in ("id", "action", "axes", "tier", "expected", "evidence", "cost"):
        if idea.get(key) in (None, "", [], {}):
            report.add(rule="schema.missing", severity="error", field=key,
                       message="必須の欄が空。")

    if idea.get("tier") is not None and idea.get("tier") not in TIERS:
        report.add(rule="schema.enum", severity="error", field="tier",
                   message=f"tier は {'/'.join(TIERS)} のいずれか。")

    status = idea.get("status", "open")
    if status not in IDEA_STATUS:
        report.add(rule="schema.enum", severity="error", field="status",
                   message=f"status は {'/'.join(IDEA_STATUS)} のいずれか。")
    if status == "retired" and not str(idea.get("retired_reason") or "").strip():
        report.add(
            rule="idea.retired_without_reason", severity="error", field="retired_reason",
            message="捨てるなら理由を書く。理由なく消せると、在庫の下限は意味を失う。",
        )

    if known_axes is not None:
        for ax in idea.get("axes") or []:
            if ax not in known_axes:
                report.add(rule="schema.unknown_axis", severity="error", field="axes",
                           message=f"coverage.yaml に無い軸: {ax!r}")

    _lint_expected(idea.get("expected"), "expected", report, prefix=False)

    cost = idea.get("cost")
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost <= 0:
        report.add(
            rule="schema.cost", severity="error", field="cost",
            message="コストを正の数値で書く（1.0 が標準的な1実験）。"
                    " 書けないと gain/cost で並べられず、確度の高い順に試せない。",
        )

    structure = cfg.get("structure", {})

    action = str(idea.get("action") or "").strip()
    min_chars = int(structure.get("idea_action_min_chars", 20))
    if action and _visible_len(action) < min_chars:
        report.add(
            rule="idea.action_too_vague", severity="error", field="action",
            message=f"{_visible_len(action)}字。{min_chars}字未満は1実験の粒度になっていない。"
                    " 「特徴量を追加」ではなく、何をどう作るのかまで書く。",
        )

    evidence = str(idea.get("evidence") or "").strip()
    if evidence and not _match_any(evidence, structure.get("idea_evidence_patterns") or []):
        report.add(
            rule="idea.evidence_without_source", severity="error", field="evidence",
            message="出典を指していない。priors の項目（priors/common.md#c01）、"
                    " このコンペの実験（exp0003）、決定（dec0002）、URL のいずれかを含める。"
                    " 出典を要求するのは、在庫を思いつきで埋められないようにするため。",
        )
    if known_experiments is not None:
        for ref in re.findall(r"\bexp\d{4}\b", evidence):
            if ref not in known_experiments:
                report.add(rule="link.experiment_missing", severity="error", field="evidence",
                           message=f"存在しない実験を出典にしている: {ref}")

    _lint_prose(idea, "idea", cfg, report)
    return report


def lint_backlog(
    backlog: dict,
    *,
    cfg: dict,
    policy: dict,
    known_axes: set[str] | None = None,
    known_experiments: set[str] | None = None,
) -> tuple[list[Report], Report]:
    """個々のアイデアの検査と、在庫全体の検査を返す。"""
    ideas = list(backlog.get("ideas") or [])
    per_idea = [
        lint_idea(i, cfg=cfg, index=n, known_axes=known_axes,
                  known_experiments=known_experiments)
        for n, i in enumerate(ideas)
    ]

    whole = Report(target="ideas/backlog.yaml")
    seen: set[str] = set()
    for i in ideas:
        if not isinstance(i, dict):
            continue
        ident = str(i.get("id") or "")
        if ident and ident in seen:
            whole.add(rule="idea.duplicate_id", severity="error", field="ideas",
                      message=f"ID が重複している: {ident}")
        seen.add(ident)

    # 在庫全体が複数の軸にまたがっているか。
    # 同じ発想の変奏で下限を満たされると、在庫の仕組みが形骸化する。
    open_ideas = [i for i in ideas
                  if isinstance(i, dict) and (i.get("status") or "open") == "open"]
    min_axes = int(policy.get("backlog_min_axes", 3))
    axes = {a for i in open_ideas for a in (i.get("axes") or [])}
    if len(open_ideas) >= min_axes and len(axes) < min_axes:
        whole.add(
            rule="backlog.too_narrow", severity="error", field="ideas",
            message=f"未実行のアイデアが {len(axes)} 軸にしか散っていない（下限 {min_axes} 軸）。"
                    " 同じ発想の変奏で在庫を埋めている。"
                    " 未着手の軸か knowledge/priors/ から別種の打ち手を足す。",
        )
    return per_idea, whole


# ---------------------------------------------------------------------------
# decision
# ---------------------------------------------------------------------------

def lint_decision(
    doc: dict,
    *,
    cfg: dict,
    dec_id: str,
    known_experiments: set[str] | None = None,
    known_hypotheses: set[str] | None = None,
) -> Report:
    report = Report(target=f"decisions/{dec_id}.yaml")

    for key in ("id", "created_at", "type", "question", "options", "chosen", "rationale"):
        if doc.get(key) in (None, "", [], {}):
            report.add(rule="schema.missing", severity="error", field=key, message="必須の欄が空。")

    if doc.get("id") not in (None, dec_id):
        report.add(rule="schema.id_mismatch", severity="error", field="id",
                   message=f"ファイル名 {dec_id} と id が食い違う。")

    dtype = doc.get("type")
    if dtype is not None and dtype not in DECISION_TYPES:
        report.add(rule="schema.enum", severity="error", field="type",
                   message=f"type は {'/'.join(DECISION_TYPES)} のいずれか。")

    options = doc.get("options") or []
    option_ids = set()
    if len(options) < 2 and dtype == "run":
        report.add(rule="decision.single_option", severity="warn", field="options",
                   message="選択肢が1つ。比較していない決定は根拠が弱い。")
    for i, o in enumerate(options):
        where = f"options[{o.get('id') or i}]" if isinstance(o, dict) else f"options[{i}]"
        if not isinstance(o, dict):
            report.add(rule="schema.type", severity="error", field=where, message="要素はマッピング。")
            continue
        option_ids.add(o.get("id"))
        _lint_expected(o.get("expected"), where, report)

    if doc.get("chosen") and options and doc.get("chosen") not in option_ids:
        report.add(rule="schema.chosen_missing", severity="error", field="chosen",
                   message=f"存在しない選択肢を選んでいる: {doc.get('chosen')!r}")

    rationale = doc.get("rationale") or {}
    based = rationale.get("based_on_experiments") or []
    if not based:
        report.add(
            rule="decision.no_evidence", severity="error", field="rationale.based_on_experiments",
            message="根拠にした実験が無い。最初の一手なら、その旨を書いた実験を1本記録してから決める。",
        )
    if known_experiments is not None:
        for e in based:
            if e not in known_experiments:
                report.add(rule="link.experiment_missing", severity="error",
                           field="rationale.based_on_experiments",
                           message=f"存在しない実験: {e!r}")
    if known_hypotheses is not None:
        for h in rationale.get("based_on_hypotheses") or []:
            if h not in known_hypotheses:
                report.add(rule="link.hypothesis_missing", severity="error",
                           field="rationale.based_on_hypotheses",
                           message=f"存在しない仮説: {h!r}（形式は exp0001#h1）")

    outcome = doc.get("outcome")
    if isinstance(outcome, dict):
        if outcome.get("result") not in DECISION_RESULTS:
            report.add(rule="schema.enum", severity="error", field="outcome.result",
                       message=f"result は {'/'.join(DECISION_RESULTS)} のいずれか。")
        if known_experiments is not None and outcome.get("experiment") not in known_experiments:
            report.add(rule="link.experiment_missing", severity="error", field="outcome.experiment",
                       message="存在しない実験を指している。")

    _lint_prose(doc, "decision", cfg, report)
    _lint_waivers(doc, report)
    return report


def _lint_expected(expected: Any, where: str, report: Report, *, prefix: bool = True) -> None:
    """期待する観測を検査する。決定とアイデアで共通。

    ここが数値で書けないものは、機械で答え合わせできない。
    答え合わせできないと較正が取れず、賭けなさすぎも見積もりの甘さも見えなくなる。
    """
    base = f"{where}.expected" if prefix else where

    if not isinstance(expected, dict):
        report.add(rule="schema.missing", severity="error", field=base,
                   message="期待する観測が無い。機械で答え合わせできないものは台帳に残さない。")
        return
    if not expected.get("metric"):
        report.add(rule="schema.missing", severity="error", field=f"{base}.metric",
                   message="どの指標が動くのかを書く（例: cv.mean）。")
    if expected.get("direction") not in DIRECTIONS:
        report.add(rule="schema.enum", severity="error", field=f"{base}.direction",
                   message=f"direction は {'/'.join(DIRECTIONS)} のいずれか。")
    mag = expected.get("magnitude")
    if not isinstance(mag, (int, float)) or isinstance(mag, bool):
        report.add(
            rule="schema.magnitude", severity="error", field=f"{base}.magnitude",
            message="期待する変化量を数値で書く。書けないなら、根拠が言葉だけになっている。",
        )
    elif mag <= 0:
        report.add(rule="schema.magnitude", severity="error", field=f"{base}.magnitude",
                   message="変化量は絶対値の正数で書く。向きは direction が持つ。")
