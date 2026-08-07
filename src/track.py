"""料金スナップショットの蓄積と変化の検出。サイトの資産の本体。

公式サイトは「今」の料金しか載せない。先月いくらだったかは、その時点で
記録していない限り永久に手に入らない。だから毎日確認して、変わった日だけを
日付つきで残す。これが「日々自動で更新される」の中身であり、
後発が追いつけない部分でもある。

【変化の判定は signature で行う】
  個々のプランの金額はラベル近傍から推定していて当てにならない
  （通常価格と割引価格が並んでいると区別できない）。
  一方 signature はページ上の金額集合のハッシュなので、
  「何かが変わった」だけは高い精度で分かる。更新履歴はこちらを根拠にする。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from src.extract import Extraction, Value


@dataclass(frozen=True)
class Snapshot:
    ts: str
    slug: str
    ok: bool
    signature: str
    values: tuple[Value, ...]
    token_count: int
    note: str

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.ts)

    def value(self, label: str) -> Value | None:
        for v in self.values:
            if v.label == label:
                return v
        return None

    @property
    def shown(self) -> tuple[Value, ...]:
        """サイトに出してよい値だけ。

        low は必ず伏せる。間違った料金を出すくらいなら空欄の方がよい。
        """
        return tuple(v for v in self.values if v.confidence == "high")


@dataclass(frozen=True)
class Change:
    ts: str
    slug: str
    before: Snapshot | None
    after: Snapshot

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.ts)

    @property
    def is_first(self) -> bool:
        return self.before is None

    @property
    def label(self) -> str:
        return "掲載開始" if self.is_first else "内容が変わりました"

    def diff_lines(self) -> list[tuple[str, str, str]]:
        """(ラベル, 変更前, 変更後)。表示できる値だけを比べる。"""
        if self.before is None:
            return [(v.label, "—", v.display) for v in self.after.shown]
        out: list[tuple[str, str, str]] = []
        labels = {v.label for v in self.before.shown} | {
            v.label for v in self.after.shown}
        for label in sorted(labels):
            a = self.before.value(label)
            b = self.after.value(label)
            before = a.display if a and a.confidence == "high" else "—"
            after = b.display if b and b.confidence == "high" else "—"
            if before != after:
                out.append((label, before, after))
        return out


def to_snapshot(slug: str, ex: Extraction, now: datetime) -> Snapshot:
    return Snapshot(ts=now.isoformat(), slug=slug, ok=ex.ok,
                    signature=ex.signature, values=ex.values,
                    token_count=ex.token_count, note=ex.note)


def _value_to_dict(v: Value) -> dict:
    return {"label": v.label, "amount": v.amount, "confidence": v.confidence}


def _value_from_dict(d: dict) -> Value:
    return Value(label=d["label"], amount=float(d["amount"]),
                 confidence=d.get("confidence", "low"))


def _to_dict(s: Snapshot) -> dict:
    return {"ts": s.ts, "slug": s.slug, "ok": s.ok, "signature": s.signature,
            "values": [_value_to_dict(v) for v in s.values],
            "token_count": s.token_count, "note": s.note}


def _from_dict(d: dict) -> Snapshot:
    return Snapshot(ts=d["ts"], slug=d["slug"], ok=bool(d.get("ok", True)),
                    signature=d.get("signature", ""),
                    values=tuple(_value_from_dict(v) for v in d.get("values", [])),
                    token_count=int(d.get("token_count", 0)),
                    note=d.get("note", ""))


def load_history(path: Path) -> dict[str, list[Snapshot]]:
    """slug ごとの時系列。壊れた行は捨てて読み進める。

    1行が壊れていても履歴全体を失わないのが JSONL を使っている理由。
    """
    history: dict[str, list[Snapshot]] = {}
    if not path.exists():
        return history
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                snap = _from_dict(json.loads(line))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            history.setdefault(snap.slug, []).append(snap)
    for snaps in history.values():
        snaps.sort(key=lambda s: s.ts)
    return history


def append(path: Path, snaps: list[Snapshot]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for s in snaps:
            f.write(json.dumps(_to_dict(s), ensure_ascii=False,
                               sort_keys=True) + "\n")


def should_record(previous: Snapshot | None, current: Snapshot) -> bool:
    """履歴に1行足すべきか。

    取得に失敗した日は記録しない。失敗を履歴に混ぜると「値段が消えた」
    という変化として残り、更新履歴が嘘になる。
    """
    if not current.ok:
        return False
    if previous is None:
        return True
    return previous.signature != current.signature


def last_ok(snaps: list[Snapshot]) -> Snapshot | None:
    for s in reversed(snaps):
        if s.ok:
            return s
    return None


def build_changes(history: dict[str, list[Snapshot]]) -> list[Change]:
    """履歴全体から変化の一覧を作る。新しい順。/updates/ の中身。"""
    changes: list[Change] = []
    for slug, snaps in history.items():
        oks = [s for s in snaps if s.ok]
        if not oks:
            continue
        changes.append(Change(ts=oks[0].ts, slug=slug, before=None,
                              after=oks[0]))
        for prev, cur in pairwise(oks):
            if prev.signature != cur.signature:
                changes.append(Change(ts=cur.ts, slug=slug, before=prev,
                                      after=cur))
    changes.sort(key=lambda c: c.ts, reverse=True)
    return changes


def tracked_since(history: dict[str, list[Snapshot]]) -> datetime | None:
    stamps = [s.ts for snaps in history.values() for s in snaps[:1]]
    return datetime.fromisoformat(min(stamps)) if stamps else None


def is_stale(checked_at: str, stale_after_days: int,
             now: datetime | None = None) -> bool:
    """最終確認から時間が経ちすぎていないか。

    古い料金を現在の料金として出し続けるのは読者を騙すことになるので、
    必ず期限を切って「確認できていません」に切り替える。
    """
    if not checked_at:
        return True
    now = now or datetime.now(UTC)
    return (now - datetime.fromisoformat(checked_at)).days > stale_after_days


def save_latest(path: Path, snaps: list[Snapshot], now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"checked_at": now.isoformat(),
         "offers": {s.slug: _to_dict(s) for s in snaps}},
        ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_latest(path: Path) -> tuple[dict[str, Snapshot], str]:
    if not path.exists():
        return {}, ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}, ""
    return ({k: _from_dict(v) for k, v in (payload.get("offers") or {}).items()},
            str(payload.get("checked_at", "")))
