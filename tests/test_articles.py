"""更新履歴の組み立てのテスト。

履歴(jsonl)は追記専用なので、config から外した案件の行も残り続ける。
それを表示に混ぜると、slug をサービス名として出し、生成していない
/offers/<slug>/ へリンクすることになる。実際 octopus-energy で
404 への内部リンクを公開していた。
"""

from __future__ import annotations

from dataclasses import replace

from src.articles import _price_note, build_update_views
from src.catalog import Catalog, Offer, Watch
from src.extract import Value
from src.track import Change, Snapshot, build_changes, should_record


def _snapshot(slug: str) -> Snapshot:
    return Snapshot(ts="2026-08-12T09:05:17+00:00", slug=slug, ok=True,
                    signature="5feceb66ffc86f38", values=(),
                    token_count=2, note="")


def _change(slug: str) -> Change:
    return Change(ts="2026-08-12T09:05:17+00:00", slug=slug,
                  before=None, after=_snapshot(slug))


def _catalog() -> Catalog:
    return Catalog(offers=(), comparisons=(), conversion_rates={})


class TestUpdateViews:
    def test_configにない案件は更新履歴に出さない(self):
        # 8/12 に試して config から外した案件。行は jsonl に残っている。
        views = build_update_views([_change("octopus-energy")],
                                   _catalog(), limit=60)
        assert views == []

    def test_件数の上限は残った件数ではなく元の件数にかかる(self):
        # 除外を先にやると limit の意味が変わってしまう。
        # 「直近N件のうち出せるもの」を出す挙動を固定する。
        changes = [_change("octopus-energy"), _change("toukobe")]
        views = build_update_views(changes, _catalog(), limit=1)
        assert views == []


class TestPriceNote:
    """料金が出せない理由を取り違えないこと。

    「公式が料金を公表していない」と「こちらが取り損ねた」は
    読者にとって意味が違う。前者を後者の言い方で書くと、
    どこかに料金が載っているのに隠していると読まれる。
    """

    def _offer(self, labels, listed=()):
        return Offer(
            slug="x", name="x", vendor="x", kind="trial", category="x",
            landing_url="https://example.com/", affiliate_url="",
            impression_url="",
            watch=Watch(url="https://example.com/", labels=tuple(labels),
                        min_amount=0.0),
            pitch="", points=(), verdict="", listed_prices=listed)

    def test_ラベル未指定なら公式に料金の記載が無いと書く(self):
        note = _price_note(self._offer([]), None, stale=False,
                           needs_review=False)
        assert "掲載されていません" in note
        assert "確認できませんでした" not in note

    def test_ラベル指定ありで取れなければ取得失敗と書く(self):
        note = _price_note(self._offer(["プランA"]), None, stale=False,
                           needs_review=False)
        assert "確認できませんでした" in note

    def test_利用者無料のサービスは料金が無いと書く(self):
        # 転職エージェントは求職者から費用を取らない。「公式に記載が無い」と
        # 書くと、どこかに費用があるのに伏せているように読まれる。
        offer = replace(self._offer([]), pricing="free")
        note = _price_note(offer, None, stale=False, needs_review=False)
        assert "費用がかからない" in note
        assert "掲載されていません" not in note


def _snap(ts: str, sig: str, sigv: int, amount: float) -> Snapshot:
    return Snapshot(ts=ts, slug="nativecamp", ok=True, signature=sig,
                    values=(Value(label="プレミアムプラン", amount=amount,
                                  confidence="low"),),
                    token_count=26, note="", sigv=sigv)


class TestRealChange:
    """署名の揺れを変化として公開しないこと。

    実測: ネイティブキャンプは A/B テストで署名が 401e9bdd ↔ 0b37b0e2 を
    往復し、料金6,800円のまま公開中の更新履歴に偽の変化が積み上がった。
    """

    def test_旧版の署名が往復しても値が同じなら変化ではない(self):
        hist = {"nativecamp": [_snap("2026-09-10T00:00:00+00:00", "401e9bdd", 1, 6800),
                               _snap("2026-09-11T00:00:00+00:00", "0b37b0e2", 1, 6800),
                               _snap("2026-09-12T00:00:00+00:00", "401e9bdd", 1, 6800)]}
        labels = [c.label for c in build_changes(hist)]
        assert labels == ["掲載開始"]

    def test_旧版でも値が変わっていれば変化として残す(self):
        # 実測: DMM英会話は 09-01 に初月特典価格が追加され、本当に変わった。
        hist = {"nativecamp": [_snap("2026-08-07T00:00:00+00:00", "e44d7759", 1, 6980),
                               _snap("2026-09-01T00:00:00+00:00", "646f1d72", 1, 1745)]}
        assert "内容が変わりました" in [c.label for c in build_changes(hist)]

    def test_署名の版が変わった日は変化にしないが記録はする(self):
        old = _snap("2026-09-14T00:00:00+00:00", "401e9bdd", 1, 6800)
        new = _snap("2026-09-15T00:00:00+00:00", "aaaaaaaa", 2, 6800)
        assert should_record(old, new) is True
        assert [c.label for c in build_changes({"nativecamp": [old, new]})] == ["掲載開始"]

    def test_新版どうしなら署名の違いをそのまま変化とする(self):
        a = _snap("2026-09-15T00:00:00+00:00", "aaaaaaaa", 2, 6800)
        b = _snap("2026-09-16T00:00:00+00:00", "bbbbbbbb", 2, 6800)
        assert "内容が変わりました" in [c.label for c in build_changes({"nativecamp": [a, b]})]
