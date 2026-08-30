"""設定の読み込みと検証。壊れた設定は起動時に落とす。

巡回の途中や描画の直前で気付くと、その日の履歴が中途半端な状態で残る。
履歴はこのサイトの資産なので、汚すくらいなら1日走らない方がいい。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 成果タイプ。記事の書き方とランキングの並びに効く。
KINDS = ("download", "signup", "trial", "purchase", "click")

KIND_LABEL = {
    "download": "アプリDL",
    "signup": "無料登録",
    "trial": "無料体験",
    "purchase": "購入",
    "click": "クリック",
}

# 成果の重さ。読者にとってのハードルの説明に使う。
KIND_EFFORT = {
    "download": "インストールするだけ",
    "signup": "登録するだけ（無料）",
    "trial": "無料で試せる",
    "purchase": "購入が必要",
    "click": "ページを見るだけ",
}


class ConfigError(Exception):
    """設定が不正。起動時に落とすために使う。"""


@dataclass(frozen=True)
class Watch:
    url: str
    labels: tuple[str, ...]
    min_amount: float

    @property
    def enabled(self) -> bool:
        """空なら取得しに行かない。

        Cambly のように料金が JavaScript で描画されるサイトは HTML に
        金額が載っていない。取りに行っても意味がないので、明示的に無効にできる。
        """
        return bool(self.url)


@dataclass(frozen=True)
class ListedPrice:
    """人が公式ページを見て書き写した料金。

    自動抽出はプランごとの金額を断定できない（通常価格と年間割引が併記されて
    いると区別がつかない。実測で全件 low になった）。そこで分業にしてある。

      人   … 料金を1度だけ正確に書き写す
      機械 … 毎日ページを見て「変わったこと」を検出し、要確認の印を立てる

    機械に断定させて間違った料金を載せるより、この形の方が正確で速い。
    """

    label: str
    amount: float
    note: str = ""

    @property
    def display(self) -> str:
        return f"{self.amount:,.0f}円"


@dataclass(frozen=True)
class Offer:
    slug: str
    name: str
    vendor: str
    kind: str
    category: str
    landing_url: str
    affiliate_url: str
    # A8素材付属の1pxインプレッション計測タグ。報酬の発生には関係しないが、
    # 管理画面の「クリック率」の分母になる。無いと素材の良し悪しを比べられない。
    impression_url: str
    watch: Watch
    pitch: str
    points: tuple[str, ...]
    verdict: str
    # 「どんな人に向くか」の判断。公式の掲載条件から読み取れることだけを書く。
    # verdict と混ぜないのは、試したかどうかを読者が区別できなくなるため。
    # ここを埋めても verified にはならず、順位も上がらない。
    fit_note: str = ""
    # 料金の扱い。公式が金額を出していない案件で、理由を取り違えないための指定。
    #   undisclosed … 料金はあるが公式に出ていない（相談で提示される）
    #   free        … 利用者は無料。料金という概念が無い（転職エージェント等）
    # 金額を拾える案件では使わない。
    pricing: str = "undisclosed"
    listed_prices: tuple[ListedPrice, ...] = ()
    # 上の料金を人が確認した日。これ以降にページの変化を検出したら
    # 「要確認」を出す。
    prices_checked: str = ""
    reward_yen: float = 0.0

    @property
    def is_monetized(self) -> bool:
        return bool(self.affiliate_url)

    @property
    def outbound_url(self) -> str:
        """読者を送る先。未提携なら公式サイトへの通常リンク。"""
        return self.affiliate_url or self.landing_url

    @property
    def rel(self) -> str:
        # 報酬が発生するリンクには必ず sponsored を付ける。
        # 景表法のステマ規制と Google のリンクスパム対策の両方の要求。
        return "sponsored noopener" if self.is_monetized else "nofollow noopener"

    @property
    def verified(self) -> bool:
        """実際に試したうえでの評価が書かれているか。

        空のまま「おすすめ」と書くのが、2026年3月のコアアップデートで
        最も強く叩かれた形。空なら記事に「未検証」と明示して順位も下げる。
        """
        return bool(self.verdict.strip())

    @property
    def kind_label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)

    @property
    def effort(self) -> str:
        return KIND_EFFORT.get(self.kind, "")


@dataclass(frozen=True)
class Site:
    lang: str
    title: str
    tagline: str
    description: str
    author: str
    contact_url: str
    base_url: str
    request_interval_sec: float
    timeout_sec: int
    stale_after_days: int
    recent_updates: int
    history_rows: int
    # Search Console の所有権確認用トークン。空なら meta タグを出さない。
    search_console: str = field(default="")
    user_agent: str = field(default="")


@dataclass(frozen=True)
class Catalog:
    offers: tuple[Offer, ...]
    comparisons: tuple[tuple[str, str], ...]
    conversion_rates: dict

    def by_slug(self, slug: str) -> Offer:
        for o in self.offers:
            if o.slug == slug:
                return o
        raise KeyError(slug)

    @property
    def watched(self) -> tuple[Offer, ...]:
        return tuple(o for o in self.offers if o.watch.enabled)


def _require(cfg: dict, key: str, where: str):
    if key not in cfg:
        raise ConfigError(f"{where}: '{key}' がありません")
    return cfg[key]


def _require_https(url: str, where: str) -> str:
    if not url.startswith("https://"):
        raise ConfigError(f"{where}: https:// で始まる URL が必要です: {url!r}")
    return url


def load_catalog(path: Path) -> Catalog:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path}: マッピングではありません")

    raw = _require(cfg, "offers", str(path))
    if not raw:
        raise ConfigError(f"{path}: offers が空です")

    offers: list[Offer] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        where = f"{path} offers[{i}]"
        slug = str(_require(entry, "slug", where))
        if slug in seen:
            raise ConfigError(f"{where}: slug が重複しています: {slug}")
        seen.add(slug)

        kind = str(_require(entry, "kind", where))
        if kind not in KINDS:
            raise ConfigError(
                f"{where}: kind は {'/'.join(KINDS)} のいずれかです: {kind!r}")

        landing = _require_https(_require(entry, "landing_url", where), where)
        affiliate = str(entry.get("affiliate_url", "") or "")
        if affiliate:
            _require_https(affiliate, f"{where}.affiliate_url")
        impression = str(entry.get("impression_url", "") or "")
        if impression:
            _require_https(impression, f"{where}.impression_url")

        w = entry.get("watch") or {}
        watch_url = str(w.get("url", "") or "")
        if watch_url:
            _require_https(watch_url, f"{where}.watch.url")

        offers.append(Offer(
            slug=slug,
            name=str(_require(entry, "name", where)),
            vendor=str(entry.get("vendor", "")),
            kind=kind,
            category=str(entry.get("category", "")),
            landing_url=landing,
            affiliate_url=affiliate,
            impression_url=impression,
            watch=Watch(url=watch_url,
                        labels=tuple(w.get("labels") or ()),
                        min_amount=float(w.get("min_amount", 500))),
            pitch=str(entry.get("pitch", "")),
            points=tuple(entry.get("points") or ()),
            verdict=str(entry.get("verdict", "") or ""),
            fit_note=str(entry.get("fit_note", "") or ""),
            pricing=str(entry.get("pricing", "undisclosed")),
            listed_prices=tuple(
                ListedPrice(label=str(_require(p, "label", where)),
                            amount=float(_require(p, "amount", where)),
                            note=str(p.get("note", "") or ""))
                for p in (entry.get("prices") or [])),
            prices_checked=str(entry.get("prices_checked", "") or ""),
            reward_yen=float(entry.get("reward_yen", 0) or 0),
        ))

    comparisons: list[tuple[str, str]] = []
    for pair in cfg.get("comparisons") or []:
        if len(pair) != 2:
            raise ConfigError(f"{path}: comparisons は2件ずつ書いてください: {pair}")
        for slug in pair:
            if slug not in seen:
                raise ConfigError(
                    f"{path}: comparisons の {slug} が offers にありません")
        comparisons.append((pair[0], pair[1]))

    return Catalog(offers=tuple(offers), comparisons=tuple(comparisons),
                   conversion_rates=cfg.get("conversion_rates") or {})


def load_site(path: Path) -> Site:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    site = _require(cfg, "site", str(path))
    gen = _require(cfg, "generation", str(path))

    contact = _require_https(_require(site, "contact_url", str(path)), str(path))
    base_url = _require_https(_require(site, "base_url", str(path)), str(path))
    interval = float(gen.get("request_interval_sec", 2.0))
    if interval < 1.0:
        raise ConfigError(
            f"{path}: request_interval_sec は 1.0 以上にしてください: {interval}")

    return Site(
        lang=str(site.get("lang", "ja")),
        title=str(_require(site, "title", str(path))),
        tagline=str(site.get("tagline", "")),
        description=str(site.get("description", "")),
        author=str(site.get("author", "")),
        contact_url=contact,
        base_url=base_url.rstrip("/") + "/",
        request_interval_sec=interval,
        timeout_sec=int(gen.get("timeout_sec", 20)),
        stale_after_days=int(gen.get("stale_after_days", 14)),
        recent_updates=int(gen.get("recent_updates", 60)),
        history_rows=int(gen.get("history_rows", 24)),
        search_console=str(site.get("search_console", "")),
        # 苦情の窓口が無いクローラは問答無用でブロックされる。必ず名乗る。
        user_agent=f"kiji-price-tracker/1.0 (+{contact})",
    )
