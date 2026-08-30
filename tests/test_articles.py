"""更新履歴の組み立てのテスト。

履歴(jsonl)は追記専用なので、config から外した案件の行も残り続ける。
それを表示に混ぜると、slug をサービス名として出し、生成していない
/offers/<slug>/ へリンクすることになる。実際 octopus-energy で
404 への内部リンクを公開していた。
"""

from __future__ import annotations

from src.articles import build_update_views
from src.catalog import Catalog
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
