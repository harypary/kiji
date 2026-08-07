"""Jinja2 で docs/ に静的サイトを書き出す。

docs/ は毎回作り直す前提の生成物。ここに手で書いたものを置かないこと。
編集するのは templates/ と config/。
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from src.articles import CompareView, OfferView, Stats, UpdateView
from src.catalog import Site


def _env(templates: Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(templates)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
        # 変数名を打ち間違えたら空文字で通さず落とす。
        # 静かに空欄になったページを公開する方が困る。
        undefined=StrictUndefined,
    )
    env.filters["yen"] = lambda v: f"{v:,.0f}"
    return env


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def render_site(out: Path, templates: Path, static: Path, site: Site,
                offers: list[OfferView], compares: list[CompareView],
                updates: list[UpdateView], stats: Stats,
                now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    env = _env(templates)
    ctx = {
        "site": site,
        "stats": stats,
        "now_rfc822": format_datetime(now),
        "generated": now.strftime("%Y-%m-%d"),
    }

    written = 0

    def emit(rel: str, template: str, **extra) -> None:
        nonlocal written
        _write(out / rel, env.get_template(template).render(**ctx, **extra))
        written += 1

    emit("index.html", "index.html", offers=offers, updates=updates[:8])
    emit("updates/index.html", "updates.html", updates=updates)
    emit("disclosure/index.html", "disclosure.html")
    emit("methodology/index.html", "methodology.html", offers=offers)
    for o in offers:
        emit(f"offers/{o.slug}/index.html", "offer.html", offer=o)
    for c in compares:
        emit(f"compare/{c.slug}/index.html", "compare.html", compare=c)

    emit("sitemap.xml", "sitemap.xml", offers=offers, compares=compares)
    emit("robots.txt", "robots.txt")
    emit("feed.xml", "feed.xml", updates=updates[:50])

    if static.exists():
        shutil.copytree(static, out / "assets", dirs_exist_ok=True)
    # GitHub Pages に Jekyll の処理を挟ませない。
    _write(out / ".nojekyll", "")
    return written
