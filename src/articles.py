"""記事データの組み立て。

テンプレートには計算済みの値だけを渡す。Jinja2 の中で計算を始めると
表示とロジックが混ざって、数字が合わないときにどちらを直せばいいか分からなくなる。

【並び順】
  期待値 = 報酬額 × 成果タイプ別の想定成約率、を基本にしつつ、
  実際に試した評価（verdict）が書かれていない案件は必ず下に落とす。
  使ってもいないものを1位に置くのが、2026年3月のコアアップデートで
  最も強く叩かれた形なので、順位のロジックで構造的に防ぐ。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from src.catalog import Catalog, Offer, Site
from src.track import Change, Snapshot, last_ok

# 未検証の案件にかける係数。上位に来させないための重し。
UNVERIFIED_PENALTY = 0.25


@dataclass(frozen=True)
class PriceRow:
    label: str
    display: str


@dataclass(frozen=True)
class HistoryRow:
    date: str
    prices: tuple[PriceRow, ...]


@dataclass(frozen=True)
class OfferView:
    slug: str
    name: str
    vendor: str
    category: str
    kind: str
    kind_label: str
    effort: str
    pitch: str
    points: tuple[str, ...]
    verdict: str
    verified: bool
    outbound_url: str
    rel: str
    is_monetized: bool
    landing_url: str
    watch_url: str
    # 自動取得した料金。confidence が high のものだけが入る
    prices: tuple[PriceRow, ...]
    price_note: str
    needs_review: bool
    history: tuple[HistoryRow, ...]
    change_count: int
    tracked_since: str
    last_checked: str
    stale: bool
    reward_yen: float
    score: float


@dataclass(frozen=True)
class CompareView:
    left: OfferView
    right: OfferView

    @property
    def slug(self) -> str:
        return f"{self.left.slug}-vs-{self.right.slug}"

    @property
    def title(self) -> str:
        return f"{self.left.name}と{self.right.name}はどちらがいいか"


@dataclass(frozen=True)
class UpdateView:
    date: str
    slug: str
    name: str
    label: str
    lines: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class Stats:
    offers: int
    verified: int
    monetized: int
    updates: int
    tracked_since: str
    checked_at: str
    stale: bool


def _price_rows(offer: Offer, snap: Snapshot | None) -> tuple[PriceRow, ...]:
    """表示する料金。人が書き写した値を優先する。

    自動抽出はプランごとの金額を断定できないので（実測で全件 low になった）、
    人が入力した値があればそちらを出す。無ければ自動抽出の high のみ。
    """
    if offer.listed_prices:
        return tuple(PriceRow(label=p.label, display=p.display)
                     for p in offer.listed_prices)
    if snap is None:
        return ()
    return tuple(PriceRow(label=v.label, display=v.display)
                 for v in snap.shown)


def _needs_review(offer: Offer, history: list[Snapshot]) -> bool:
    """人が料金を確認した日より後に、ページの変化を検出したか。

    これが立っているのに放置すると、古い料金を現在の料金として
    出し続けることになる。サイトにもそのまま表示する。
    """
    if not offer.listed_prices or not offer.prices_checked:
        return False
    oks = [s for s in history if s.ok]
    after = [s for s in oks if s.ts[:10] > offer.prices_checked]
    if not after or len(oks) < 2:
        return False
    # 確認日より後に signature が動いていれば要確認
    base = [s for s in oks if s.ts[:10] <= offer.prices_checked]
    baseline = base[-1] if base else oks[0]
    return any(s.signature != baseline.signature for s in after)


def _price_note(offer: Offer, snap: Snapshot | None, stale: bool,
                needs_review: bool) -> str:
    """料金の出どころと信頼度を、読者向けの言葉で返す。"""
    if needs_review:
        return (f"公式サイトの内容が {offer.prices_checked} 以降に変わっています。"
                "下の料金は古い可能性があります。公式サイトでご確認ください。")
    if offer.listed_prices:
        base = f"公式サイトの表示を{offer.prices_checked or '掲載時'}に書き写したものです。"
        if offer.watch.enabled:
            base += "ページの変更は毎日自動で監視しています。"
        return base + "申し込み前に公式サイトでご確認ください。"
    if not offer.watch.enabled:
        return ("このサービスは料金がページ上のテキストとして取得できないため、"
                "自動追跡していません。公式サイトでご確認ください。")
    if snap is None or not snap.ok:
        return "料金を確認できませんでした。公式サイトでご確認ください。"
    if stale:
        return "最終確認から時間が経っています。公式サイトで最新の料金をご確認ください。"
    if not snap.shown:
        return ("このページからは料金を確実に読み取れませんでした"
                "（通常価格と割引価格が併記されている等）。公式サイトでご確認ください。")
    return "料金は自動取得した参考値です。申し込み前に公式サイトでご確認ください。"


def score(offer: Offer, rates: dict) -> float:
    """並び順の根拠。期待値 × 検証済みかどうか。"""
    cvr = float(rates.get(offer.kind, 0.01))
    base = offer.reward_yen * cvr
    # 報酬額を書いていない案件でも並べられるように、最低値を持たせる
    base = max(base, 1.0)
    return base if offer.verified else base * UNVERIFIED_PENALTY


def build_offer_views(catalog: Catalog, history: dict[str, list[Snapshot]],
                      latest: dict[str, Snapshot], site: Site,
                      stale: bool) -> list[OfferView]:
    views: list[OfferView] = []
    for offer in catalog.offers:
        snaps = [s for s in history.get(offer.slug, []) if s.ok]
        snap = latest.get(offer.slug) or last_ok(snaps)
        review = _needs_review(offer, snaps)
        # 履歴テーブルは自動取得の値だけを出す。人が書き写した値は
        # 「今の料金」であって過去の各時点の値ではない。
        rows = tuple(
            HistoryRow(date=s.when.strftime("%Y-%m-%d"),
                       prices=tuple(PriceRow(label=v.label, display=v.display)
                                    for v in s.shown))
            for s in reversed(snaps)
        )[: site.history_rows]
        changes = sum(1 for a, b in pairwise(snaps)
                      if a.signature != b.signature)
        views.append(OfferView(
            slug=offer.slug,
            name=offer.name,
            vendor=offer.vendor,
            category=offer.category,
            kind=offer.kind,
            kind_label=offer.kind_label,
            effort=offer.effort,
            pitch=offer.pitch,
            points=offer.points,
            verdict=offer.verdict,
            verified=offer.verified,
            outbound_url=offer.outbound_url,
            rel=offer.rel,
            is_monetized=offer.is_monetized,
            landing_url=offer.landing_url,
            watch_url=offer.watch.url,
            prices=_price_rows(offer, snap),
            price_note=_price_note(offer, snap, stale, review),
            needs_review=review,
            history=rows,
            change_count=changes,
            tracked_since=(snaps[0].when.strftime("%Y-%m-%d")
                           if snaps else "—"),
            last_checked=(snap.when.strftime("%Y-%m-%d") if snap else "—"),
            stale=stale,
            reward_yen=offer.reward_yen,
            score=score(offer, catalog.conversion_rates),
        ))
    # 並び順: 実際に試したもの → 料金を出せるもの → 期待値 → slug。
    #
    # score だけで並べると、reward_yen を書いていない案件同士が同点になり、
    # slug のアルファベット順という読者に無意味な並びになる（実際になった）。
    # 同点なら「読者に出せる情報が多い方」を上にするのが筋が通る。
    views.sort(key=lambda v: (not v.verified, not v.prices, -v.score, v.slug))
    return views


def build_compare_views(views: list[OfferView],
                        catalog: Catalog) -> list[CompareView]:
    by_slug = {v.slug: v for v in views}
    return [CompareView(left=by_slug[a], right=by_slug[b])
            for a, b in catalog.comparisons
            if a in by_slug and b in by_slug]


def build_update_views(changes: list[Change], catalog: Catalog,
                       limit: int) -> list[UpdateView]:
    out: list[UpdateView] = []
    for c in changes[:limit]:
        try:
            name = catalog.by_slug(c.slug).name
        except KeyError:
            # config から消した案件の履歴が残っているだけ。名前は出せない。
            name = c.slug
        out.append(UpdateView(
            date=c.when.strftime("%Y-%m-%d"),
            slug=c.slug,
            name=name,
            label=c.label,
            lines=tuple(c.diff_lines()),
        ))
    return out


def build_stats(views: list[OfferView], updates: list[Change],
                tracked_since: datetime | None, checked_at: str,
                stale: bool) -> Stats:
    return Stats(
        offers=len(views),
        verified=sum(1 for v in views if v.verified),
        monetized=sum(1 for v in views if v.is_monetized),
        updates=len(updates),
        tracked_since=(tracked_since.strftime("%Y-%m-%d")
                       if tracked_since else "—"),
        checked_at=(datetime.fromisoformat(checked_at).strftime("%Y-%m-%d")
                    if checked_at else "—"),
        stale=stale,
    )
