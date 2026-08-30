"""生成した docs/ を検査する。CI がデプロイの直前に実行する。

壊れたページを公開してしまうと、気付くのは検索順位が落ちた後になる。
ここで落とせるものは全部ここで落とす。
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from src.catalog import Site

REQUIRED = ["index.html", "updates/index.html", "methodology/index.html",
            "disclosure/index.html",
            "sitemap.xml", "robots.txt", "feed.xml", "assets/style.css"]

# <a> タグを1つの単位として見る。href と rel を別々に全文検索して
# 近さで対応付けると、隣のリンクの rel を誤って拾う。実際それで
# 「footer の外部リンクに rel が無い」を全ページで見逃していた。
ANCHOR_RE = re.compile(r"<a\s([^>]*)>", re.IGNORECASE | re.DOTALL)
ATTR_HREF_RE = re.compile(r'href="([^"]*)"', re.IGNORECASE)
ATTR_REL_RE = re.compile(r'rel="([^"]*)"', re.IGNORECASE)

# 広告主・ASP へ出す外部リンクは全て収益に直結しうるので、
# sponsored か nofollow が要る。景表法のステマ規制と
# Google のリンクスパム対策の両方の要求。
# 自分の連絡先(GitHub 等)だけは例外にする。
EDITORIAL_HOSTS = ("github.com",)
# StrictUndefined を使っていても、テンプレートに文字列として書いた
# "None" や "{{" が残ることはある
LEFTOVER_RE = re.compile(r"\{\{|\}\}|\bNone\b|Undefined")

# rel 属性が無かったときの受け皿（group(1) が空文字になる）
_EMPTY = re.match(r"()", "")


def verify_site(docs: Path, site: Site) -> list[str]:
    problems: list[str] = []
    if not docs.exists():
        return [f"{docs} がありません。先に生成してください"]

    for rel in REQUIRED:
        if not (docs / rel).exists():
            problems.append(f"必須ファイルがありません: {rel}")

    html_files = sorted(docs.rglob("*.html"))
    if not html_files:
        problems.append("HTML が1枚もありません")

    host = urlsplit(site.base_url).netloc
    for path in html_files:
        rel = path.relative_to(docs)
        text = path.read_text(encoding="utf-8")

        if len(text) < 400:
            problems.append(f"{rel}: 中身が短すぎます（{len(text)}バイト）")

        leftover = LEFTOVER_RE.search(text)
        if leftover:
            problems.append(
                f"{rel}: テンプレートの未展開が残っています: {leftover.group(0)!r}")

        if "<title>" not in text:
            problems.append(f"{rel}: <title> がありません")
        if 'name="description"' not in text:
            problems.append(f"{rel}: description がありません")

        # A8 のプログラム詳細や広告主への外部リンクに rel を付け忘れると、
        # サイト全体の評価に影響する
        for attrs in ANCHOR_RE.findall(text):
            href_m = ATTR_HREF_RE.search(attrs)
            if href_m is None:
                continue
            href = href_m.group(1)
            if not href.startswith("http"):
                continue
            netloc = urlsplit(href).netloc
            if netloc == host:
                continue
            rel_value = (ATTR_REL_RE.search(attrs) or _EMPTY).group(1)
            tokens = set(rel_value.lower().split())

            # このサイトの外部リンクは、自分の連絡先を除いて全て広告主宛。
            # 27.A8 と違って「収益リンクかどうか」を判定するのではなく、
            # 例外リスト以外は全部収益リンクとして扱う方が安全側に倒れる。
            # 提携が承認されてリンクを差し替えたときに付け忘れても、ここで落ちる。
            editorial = any(netloc == h or netloc.endswith("." + h)
                            for h in EDITORIAL_HOSTS)
            if editorial:
                if "noopener" not in tokens:
                    problems.append(
                        f"{rel}: 外部リンクに rel がありません: {href}")
            elif not tokens & {"sponsored", "nofollow"}:
                problems.append(
                    f"{rel}: 広告主へのリンクに sponsored/nofollow がありません: {href}")

        # 自サイト内へのリンクが実ファイルに対応しているか。
        # sitemap 側だけ見ていたので、sitemap に載らない導線
        # （更新履歴から案件ページへのリンク等）の 404 を見逃していた。
        for attrs in ANCHOR_RE.findall(text):
            href_m = ATTR_HREF_RE.search(attrs)
            if href_m is None:
                continue
            href = href_m.group(1)
            if not href.startswith(site.base_url):
                continue
            path_only = urlsplit(href).path
            base_path = urlsplit(site.base_url).path
            inner = path_only[len(base_path):]
            target = docs / (inner + "index.html"
                             if inner.endswith("/") or not inner else inner)
            if not target.exists():
                problems.append(f"{rel}: 実体のないページへのリンク: {href}")

    # sitemap の URL が実ファイルと対応しているか
    sitemap = docs / "sitemap.xml"
    if sitemap.exists():
        for loc in re.findall(r"<loc>([^<]+)</loc>",
                              sitemap.read_text(encoding="utf-8")):
            if not loc.startswith(site.base_url):
                problems.append(f"sitemap.xml: base_url と違う URL: {loc}")
                continue
            rel = loc[len(site.base_url):]
            target = docs / (rel + "index.html" if rel.endswith("/") or not rel
                             else rel)
            if not target.exists():
                problems.append(f"sitemap.xml: 実体のない URL: {loc}")

    return problems
