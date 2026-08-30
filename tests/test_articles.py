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
from src.track import Change, Snapshot


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
