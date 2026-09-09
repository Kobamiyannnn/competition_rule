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
    FACT_CONFIDENCE,
    FACT_SOURCES,
    DECISION_TYPES,
    DIRECTIONS,
    HYPOTHESIS_STATUS,
    IDEA_STATUS,
    PROPOSAL_KINDS,
    PROPOSAL_STATUS,
    SOURCE_KINDS,
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
    elif kind == "landscape_task":
        task = doc.get("task") or {}
        for f in ("statement", "formulation", "why_hard"):
            push(f"landscape.task.{f}", f"task.{f}", task.get(f))
    elif kind == "proposal":
        push("proposal.incident", "incident", doc.get("incident"))
        push("proposal.missing", "missing", doc.get("missing"))
        push("proposal.workaround", "workaround", doc.get("workaround"))
    elif kind == "domain_fact":
        push("domain.fact.statement", "statement", doc.get("statement"))
        push("domain.fact.evidence", "evidence", doc.get("evidence"))
        push("domain.fact.implication", "implication", doc.get("implication"))
    elif kind == "landscape_source":
        push("landscape.source.relevance", "relevance", doc.get("relevance"))
        push("landscape.source.takeaway", "takeaway", doc.get("takeaway"))
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
    _lint_forced_gates(doc, report)
    _lint_created_by(doc, report)
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


def _lint_created_by(doc: dict, report: Report) -> None:
    """`expctl new` を通って作られた実験かを確かめる。

    ディレクトリを手で作って record.yaml を置けば、tier クォータも
    地固めのゲートも在庫の下限も一度も評価されない。
    この欄が無いと、迂回が「書き忘れ」と見分けられない。
    書けば通ってしまう程度の印だが、迂回が省略ではなく明示的な行為になる。
    """
    if not str(doc.get("created_by") or "").strip():
        report.add(
            rule="schema.missing", severity="error", field="created_by",
            message="`uv run expctl new` を通っていない。"
                    " 手で作るとゲート（tier 配分・地固め・在庫の下限）が"
                    " 一度も評価されない。expctl new で作り直す。",
        )


def _lint_forced_gates(doc: dict, report: Report) -> None:
    """ゲートを押し切ったなら、なぜ押し切ったのかを書かせる。

    語彙規則の waive には20字以上の理由を要求しているのに、
    ゲートの押し切りには何も要求していなかった。抜け道の重さが逆で、
    影響の大きい方が緩いままだった。
    """
    for i, g in enumerate(doc.get("gates_forced") or []):
        where = f"gates_forced[{i}]"
        if not isinstance(g, dict):
            report.add(rule="schema.type", severity="error", field=where,
                       message="gates_forced の要素はマッピング。")
            continue
        if not str(g.get("gate") or "").strip():
            report.add(rule="schema.missing", severity="error", field=f"{where}.gate",
                       message="どのゲートを押し切ったのか書く。")
        reason = str(g.get("reason") or "").strip()
        if len(reason) < 20:
            report.add(
                rule="force.thin_reason", severity="error", field=f"{where}.reason",
                message="押し切った理由が20字未満。"
                        " ゲートは実験の方向そのものを止めているので、"
                        " 語彙規則を抜けるより重い判断になる。"
                        " 基盤の側に問題があるなら `uv run expctl propose` にも残す。",
            )


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
# proposal — 基盤そのものへの改善提案
#
# 願望と不具合報告を分けるのが目的。
# 「あると便利」は再設計の材料にならないが、
# 「exp0012 でこうしたかったが置き場所が無かった」は材料になる。
# だから実際に起きた出来事（at）への紐づけを必須にする。
# ---------------------------------------------------------------------------

def lint_proposal(
    proposal: dict,
    *,
    cfg: dict,
    index: int,
    known_experiments: set[str] | None = None,
    known_decisions: set[str] | None = None,
) -> Report:
    ident = proposal.get("id") if isinstance(proposal, dict) else None
    report = Report(target=f"feedback/proposals.yaml [{ident or f'#{index}'}]")

    if not isinstance(proposal, dict):
        report.add(rule="schema.type", severity="error", field=f"proposals[{index}]",
                   message="提案の要素はマッピング。")
        return report

    for key in ("id", "kind", "incident", "missing"):
        if not str(proposal.get(key) or "").strip():
            report.add(rule="schema.missing", severity="error", field=key,
                       message="必須の欄が空。")

    kind = proposal.get("kind")
    if kind is not None and kind not in PROPOSAL_KINDS:
        report.add(rule="schema.enum", severity="error", field="kind",
                   message=f"kind は {'/'.join(PROPOSAL_KINDS)} のいずれか。")

    status = proposal.get("status", "open")
    if status not in PROPOSAL_STATUS:
        report.add(rule="schema.enum", severity="error", field="status",
                   message=f"status は {'/'.join(PROPOSAL_STATUS)} のいずれか。")

    # ここが質を決める。実際の出来事に紐づいていない提案は願望であって、
    # 再設計の材料にならない。
    at = proposal.get("at") or []
    if not at:
        report.add(
            rule="proposal.not_grounded", severity="error", field="at",
            message="起きた実験か決定の ID が無い。"
                    " 具体的な場面に紐づかない提案は願望であって、再設計の材料にならない。"
                    " 思いついただけなら書かない。",
        )
    for ref in at:
        ref_s = str(ref)
        if known_experiments is not None and ref_s.startswith("exp")                 and ref_s not in known_experiments:
            report.add(rule="link.experiment_missing", severity="error", field="at",
                       message=f"存在しない実験: {ref_s}")
        if known_decisions is not None and ref_s.startswith("dec")                 and ref_s not in known_decisions:
            report.add(rule="link.decision_missing", severity="error", field="at",
                       message=f"存在しない決定: {ref_s}")
        if not (ref_s.startswith("exp") or ref_s.startswith("dec")):
            report.add(rule="proposal.bad_reference", severity="error", field="at",
                       message=f"実験（exp0001）か決定（dec0001）の ID で書く: {ref_s!r}")

    structure = cfg.get("structure", {})
    min_incident = int(structure.get("proposal_incident_min_chars", 40))
    incident = str(proposal.get("incident") or "").strip()
    if incident and _visible_len(incident) < min_incident:
        report.add(
            rule="proposal.incident_too_thin", severity="error", field="incident",
            message=f"{_visible_len(incident)}字。{min_incident}字未満。"
                    " 「使いにくかった」で終わらせず、何をしようとして何が起きたかを書く。",
        )

    if status == "filed" and not str(proposal.get("filed_as") or "").strip():
        report.add(rule="schema.missing", severity="error", field="filed_as",
                   message="filed にするなら、投げた先の URL を書く。")

    _lint_prose(proposal, "proposal", cfg, report)
    return report


def lint_proposals(
    doc: dict,
    *,
    cfg: dict,
    known_experiments: set[str] | None = None,
    known_decisions: set[str] | None = None,
) -> tuple[list[Report], Report]:
    proposals = list(doc.get("proposals") or [])
    per_item = [
        lint_proposal(p, cfg=cfg, index=n, known_experiments=known_experiments,
                      known_decisions=known_decisions)
        for n, p in enumerate(proposals)
    ]
    whole = Report(target="feedback/proposals.yaml")
    seen: set[str] = set()
    for p in proposals:
        if not isinstance(p, dict):
            continue
        ident = str(p.get("id") or "")
        if ident and ident in seen:
            whole.add(rule="proposal.duplicate_id", severity="error", field="proposals",
                      message=f"ID が重複している: {ident}")
        seen.add(ident)
    return per_item, whole


# ---------------------------------------------------------------------------
# domain — ドメイン知識の台帳
#
# landscape が「何をやればいいか」なら、こちらは「何が本当か」。
# 調べ終わらない種類の知識なので、誤り分析の出力もここに入る。
#
# confidence を型で分けるのが肝。検証していない仮定が前提として使われるのが
# 「弱い根拠で打ち切る」の正体で、それを見えるようにする。
# ---------------------------------------------------------------------------

def lint_domain(
    domain: dict,
    *,
    cfg: dict,
    policy: dict,
    known_experiments: set[str] | None = None,
    known_ideas: set[str] | None = None,
) -> tuple[list[Report], Report]:
    structure = cfg.get("structure", {})
    min_impl = int(structure.get("domain_implication_min_chars", 25))
    confirmed_patterns = structure.get("domain_confirmed_evidence_patterns") or []

    facts = list(domain.get("facts") or [])
    per_fact: list[Report] = []
    whole = Report(target="knowledge/domain.yaml")
    seen: set[str] = set()
    confirmed = 0

    for n, fact in enumerate(facts):
        ident = fact.get("id") if isinstance(fact, dict) else None
        report = Report(target=f"knowledge/domain.yaml [{ident or f'#{n}'}]")
        per_fact.append(report)

        if not isinstance(fact, dict):
            report.add(rule="schema.type", severity="error", field=f"facts[{n}]",
                       message="事実の要素はマッピング。")
            continue

        for key in ("id", "statement", "source", "evidence", "confidence", "implication"):
            if not str(fact.get(key) or "").strip():
                report.add(rule="schema.missing", severity="error", field=key,
                           message="必須の欄が空。")

        ident_s = str(fact.get("id") or "")
        if ident_s and ident_s in seen:
            whole.add(rule="domain.duplicate_id", severity="error", field="facts",
                      message=f"ID が重複している: {ident_s}")
        seen.add(ident_s)

        source = fact.get("source")
        if source is not None and source not in FACT_SOURCES:
            report.add(rule="schema.enum", severity="error", field="source",
                       message=f"source は {'/'.join(FACT_SOURCES)} のいずれか。")

        conf = fact.get("confidence")
        if conf is not None and conf not in FACT_CONFIDENCE:
            report.add(rule="schema.enum", severity="error", field="confidence",
                       message=f"confidence は {'/'.join(FACT_CONFIDENCE)} のいずれか。")

        evidence = str(fact.get("evidence") or "").strip()
        if conf == "confirmed":
            confirmed += 1
            if evidence and not _match_any(evidence, confirmed_patterns):
                report.add(
                    rule="domain.unconfirmable_evidence", severity="error", field="evidence",
                    message="confirmed を名乗るなら、他人が再確認できる根拠を書く"
                            "（確認に使った実験 exp0003 / URL / 地固めの出典 s0001）。"
                            " 自分で確かめていないなら likely か assumed にする。",
                )
        if known_experiments is not None:
            for ref in re.findall(r"\bexp\d{4}\b", evidence):
                if ref not in known_experiments:
                    report.add(rule="link.experiment_missing", severity="error",
                               field="evidence",
                               message=f"存在しない実験を根拠にしている: {ref}")

        impl = str(fact.get("implication") or "").strip()
        if impl and _visible_len(impl) < min_impl:
            report.add(
                rule="domain.implication_too_thin", severity="error", field="implication",
                message=f"{_visible_len(impl)}字。{min_impl}字未満。"
                        " 打ち手にどう効くかが書けない事実は雑学であって、台帳に置く意味がない。",
            )

        resolved = fact.get("resolved_by")
        if conf == "confirmed" and source == "data_observation" and not evidence:
            report.add(rule="schema.missing", severity="error", field="evidence",
                       message="実データで確かめたなら、その手順を書く。")
        if resolved and known_experiments is not None and resolved not in known_experiments:
            report.add(rule="link.experiment_missing", severity="error", field="resolved_by",
                       message=f"存在しない実験を指している: {resolved!r}")

        if known_ideas is not None:
            for i in fact.get("transferred_to") or []:
                if i not in known_ideas:
                    report.add(rule="link.idea_missing", severity="error",
                               field="transferred_to",
                               message=f"在庫に無いアイデアを指している: {i!r}")

        _lint_prose(fact, "domain_fact", cfg, report)

    min_facts = int(policy.get("domain_min_facts", 5))
    min_confirmed = int(policy.get("domain_min_confirmed", 2))

    if len(facts) < min_facts:
        whole.add(
            rule="domain.too_few_facts", severity="error", field="facts",
            message=f"ドメインの事実が {len(facts)} 件。下限 {min_facts} 件。"
                    " 対象そのものについて分かっていることを書く。"
                    " データの成り立ち、現象の性質、ありえない値。",
        )
    if len(facts) >= min_facts and confirmed < min_confirmed:
        whole.add(
            rule="domain.nothing_confirmed", severity="error", field="facts",
            message=f"自分で確かめた事実が {confirmed} 件（下限 {min_confirmed}）。"
                    " 読んだだけの知識しかない状態で進むと、"
                    " 検証していない前提の上に実験を積むことになる。"
                    " 実データを見て確かめられることを確かめる。",
        )
    return per_fact, whole


def assumed_facts(domain: dict) -> list[dict]:
    """未検証の仮定。残っている間は打ち切れない。"""
    return [f for f in (domain.get("facts") or [])
            if isinstance(f, dict) and f.get("confidence") == "assumed"]


# ---------------------------------------------------------------------------
# landscape — 地固め
#
# knowledge/priors/ は汎用の打ち手で、どのコンペでも同じ内容になる。
# ここはこのコンペ固有の外部知識。汎用の在庫からは汎用のアイデアしか出ないので、
# ここが埋まるまで modeling 系の軸に進ませない。
# ---------------------------------------------------------------------------

def lint_landscape(
    landscape: dict,
    *,
    cfg: dict,
    policy: dict,
    known_ideas: set[str] | None = None,
) -> tuple[list[Report], Report]:
    """出典ごとの検査と、地固め全体の検査を返す。"""
    structure = cfg.get("structure", {})
    url_pattern = structure.get("landscape_url_pattern") or r"^https?://\S+$"
    min_takeaway = int(structure.get("landscape_takeaway_min_chars", 30))

    whole = Report(target="knowledge/landscape.yaml")
    task = landscape.get("task") or {}
    for field in ("statement", "formulation", "why_hard"):
        value = str(task.get(field) or "").strip()
        if not value:
            whole.add(rule="schema.missing", severity="error", field=f"task.{field}",
                      message="空。何を解く問題なのかを書かないと、調査の的が定まらない。")
    _lint_prose(landscape, "landscape_task", cfg, whole)

    sources = list(landscape.get("sources") or [])
    per_source: list[Report] = []
    seen: set[str] = set()
    kinds: set[str] = set()
    transferred = 0

    for n, src in enumerate(sources):
        ident = src.get("id") if isinstance(src, dict) else None
        report = Report(target=f"knowledge/landscape.yaml [{ident or f'#{n}'}]")
        per_source.append(report)

        if not isinstance(src, dict):
            report.add(rule="schema.type", severity="error", field=f"sources[{n}]",
                       message="出典の要素はマッピング。")
            continue

        for key in ("id", "kind", "title", "url", "relevance", "takeaway"):
            if not str(src.get(key) or "").strip():
                report.add(rule="schema.missing", severity="error", field=key,
                           message="必須の欄が空。")

        ident_s = str(src.get("id") or "")
        if ident_s and ident_s in seen:
            whole.add(rule="landscape.duplicate_id", severity="error", field="sources",
                      message=f"ID が重複している: {ident_s}")
        seen.add(ident_s)

        kind = src.get("kind")
        if kind is not None and kind not in SOURCE_KINDS:
            report.add(rule="schema.enum", severity="error", field="kind",
                       message=f"kind は {'/'.join(SOURCE_KINDS)} のいずれか。")
        elif kind:
            kinds.add(kind)

        url = str(src.get("url") or "").strip()
        if url and not re.match(url_pattern, url):
            report.add(
                rule="landscape.bad_url", severity="error", field="url",
                message="URL の形になっていない。実際に開いた URL をそのまま書く。",
            )

        takeaway = str(src.get("takeaway") or "").strip()
        if takeaway and _visible_len(takeaway) < min_takeaway:
            report.add(
                rule="landscape.takeaway_too_thin", severity="error", field="takeaway",
                message=f"{_visible_len(takeaway)}字。{min_takeaway}字未満だと打ち手に移せない。"
                        " 「良い結果を報告している」ではなく、何をどうするのかを書く。",
            )

        moved = src.get("transferred_to") or []
        if moved:
            transferred += 1
        if known_ideas is not None:
            for i in moved:
                if i not in known_ideas:
                    report.add(rule="link.idea_missing", severity="error",
                               field="transferred_to",
                               message=f"在庫に無いアイデアを指している: {i!r}")

        _lint_prose(src, "landscape_source", cfg, report)

    min_sources = int(policy.get("landscape_min_sources", 5))
    min_kinds = int(policy.get("landscape_min_kinds", 2))
    min_transferred = int(policy.get("landscape_min_transferred", 3))

    if len(sources) < min_sources:
        whole.add(
            rule="landscape.too_few_sources", severity="error", field="sources",
            message=f"出典が {len(sources)} 件。下限 {min_sources} 件。"
                    " 手を動かす前に、この問題に対して世の中が何をやってきたかを調べる。",
        )
    if len(sources) >= min_sources and len(kinds) < min_kinds:
        whole.add(
            rule="landscape.one_kind_only", severity="error", field="sources",
            message=f"出典の種類が {len(kinds)} 種類（下限 {min_kinds}）。"
                    " 論文だけでは実装の勘所が分からず、解法だけでは原理が分からない。"
                    f" 使える種類: {', '.join(SOURCE_KINDS)}",
        )
    if len(sources) >= min_sources and transferred < min_transferred:
        whole.add(
            rule="landscape.not_transferred", severity="error", field="sources",
            message=f"在庫に移した出典が {transferred} 件（下限 {min_transferred}）。"
                    " 調べただけで打ち手にしていない。"
                    " `expctl idea add --evidence s0001` で移し、transferred_to に書き戻す。",
        )
    return per_source, whole


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
