#!/usr/bin/env python3
"""セッションの境界を安くする hook。

SessionStart : 引き継ぎをコンテキストに注入する。startup / resume / clear / compact
               すべてで走るので、compact の直後にも durable な状態が戻る。
               これでセッションを捨てても、compact されても、何も失われない。
PreCompact   : compact を止めはしない（コンテキストが埋まった状態で止めると詰む）。
               代わりに、書き出されていない状態を指摘して引き継ぎを保存する。

compact を打ちたくなること自体が、何かが書き出されていないという信号。
その信号をここで拾って、次からは書き出させる。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HANDOFF = Path(".claude") / "handoff.md"


def _find_root(payload: dict) -> Path | None:
    start = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or ".").resolve()
    for candidate in (start, *start.parents):
        if (candidate / "competition.yaml").exists():
            return candidate
    return None


def _initialized(root: Path) -> bool:
    """/init-competition が済んでいるか。未初期化なら黙って何もしない。"""
    try:
        import yaml

        doc = yaml.safe_load((root / "competition.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    return str(doc.get("name") or "").strip() not in ("", "TODO")


def _brief(root: Path) -> str | None:
    os.environ["EXPKIT_ROOT"] = str(root)
    try:
        from expkit.brief import build
        from expkit.state import load_state

        return build(load_state(root), root)
    except Exception as e:  # noqa: BLE001 — hook は失敗しても作業を止めない
        return f"（引き継ぎを生成できなかった: {type(e).__name__}: {e}）\n"


def session_start(payload: dict) -> int:
    root = _find_root(payload)
    if root is None or not _initialized(root):
        return 0

    text = _brief(root)
    if not text:
        return 0

    source = payload.get("source", "")
    if source == "compact":
        text = ("（compact 直後。以下はファイルから読み直した状態であり、"
                "compact の要約ではない。判断はこちらを使う。）\n\n" + text)

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }, ensure_ascii=False))
    return 0


def _unexternalized(root: Path) -> list[str]:
    """セッションと一緒に失われる状態を数える。"""
    problems: list[str] = []
    os.environ["EXPKIT_ROOT"] = str(root)
    try:
        from expkit.brief import in_flight
        from expkit.state import load_state

        state = load_state(root)
        for exp, why in in_flight(state, root):
            if not (exp.record.get("notes") or "").strip():
                problems.append(
                    f"{exp.id} が途中で止まっている（{why}）のに notes が空。"
                    " どこまで進んだかを experiments/"
                    f"{exp.id}/record.yaml の notes に書く。"
                )
    except Exception:
        pass

    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", "experiments", "decisions",
             "knowledge", "ideas"],
            cwd=root, capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            n = len(out.stdout.strip().split("\n"))
            problems.append(
                f"記録に未コミットの変更が {n} 件ある。"
                " コミットしないと、次のセッションが git 履歴から辿れない。"
            )
    except (OSError, subprocess.SubprocessError):
        pass
    return problems


def pre_compact(payload: dict) -> int:
    root = _find_root(payload)
    if root is None or not _initialized(root):
        return 0

    text = _brief(root)
    if text:
        path = root / HANDOFF
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    problems = _unexternalized(root)
    msg = [
        "compact の前に。",
        "",
        f"引き継ぎを {HANDOFF} に保存した。compact 後は SessionStart hook が"
        " ファイルから状態を読み直すので、要約ではなく実データが戻る。",
    ]
    if problems:
        msg += ["", "ただし、書き出されていない状態がある。compact すると失われる。", ""]
        msg += [f"  - {p}" for p in problems]
        msg += ["", "compact を続ける前に、上を書き出すことを勧める。"]
    else:
        msg += ["", "書き出されていない状態は見つからなかった。"]

    msg += [
        "",
        "次から compact を減らすには、実験1本ごとにセッションを切る。"
        " 引き継ぎが自動で復元されるので、切る側のコストはほぼ無い。"
        " データ探索・失敗の原因追跡・公開解法の読み込みはサブエージェントに投げると、"
        " 本体のコンテキストが埋まらない。",
        "",
    ]
    sys.stderr.write("\n".join(msg))
    # compact は止めない。コンテキストが埋まった状態で止めると逃げ場が無くなる。
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    event = payload.get("hook_event_name", "")
    if event == "SessionStart":
        return session_start(payload)
    if event == "PreCompact":
        return pre_compact(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
