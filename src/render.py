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


def _clean(out: Path) -> None:
    """出力先を作り直す。前回の生成物を残さない。

    docs/ は .gitignore 済みで、CI は毎回まっさらな checkout から
    作り直す。一方ローカルは上書きしかしないので、config から外した
    案件のページが残り続ける。その状態だと --verify が「リンク先は
    存在する」と判定してしまい、本番だけ 404 になる食い違いが起きる。
    実際 octopus-energy でそうなった。

    誤って別のディレクトリを消さないよう、自分が作った跡
    (.nojekyll) があるときだけ消す。
    """
    if not out.exists():
        return
    if not (out / ".nojekyll").exists():
        raise RuntimeError(
            f"{out} は生成物ディレクトリに見えません（.nojekyll が無い）。"
            "消す前に中身を確認してください")
    shutil.rmtree(out)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def render_site(out: Path, templates: Path, static: Path, site: Site,
                offers: list[OfferView], compares: list[CompareView],
                updates: list[UpdateView], stats: Stats,
                now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    _clean(out)
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
    # 取得できない原因の切り分け用。詳細は templates/sitemap_index.xml。
    emit("sitemap_index.xml", "sitemap_index.xml")
    emit("robots.txt", "robots.txt")
    emit("feed.xml", "feed.xml", updates=updates[:50])

    if static.exists():
        shutil.copytree(static, out / "assets", dirs_exist_ok=True)
    # GitHub Pages に Jekyll の処理を挟ませない。
    _write(out / ".nojekyll", "")
    return written
