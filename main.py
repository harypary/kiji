"""記事型アフィリエイトサイトの自動更新パイプライン。

    python main.py            # 本番: 巡回して履歴に記録し、サイトを再生成
    python main.py --check    # 抽出が壊れていないか点検する（履歴は汚さない）
    python main.py --render   # 巡回せず、既存の履歴からサイトだけ作り直す
    python main.py --verify   # 生成済みの docs/ を検査する。CIがデプロイ前に実行

【毎日自動で更新されるもの】
  各サービスの料金ページを1日1回確認し、変わっていれば履歴に記録して
  サイトの数字を入れ替える。/updates/ に日付つきで残る。

【自動化していないもの】
  「実際に使ってみた評価」(offers.yaml の verdict)。ここは書けない。
  使ってもいないものに感想を書くのが2026年3月のコアアップデートで
  最も強く叩かれた形なので、空なら「未検証」と明示して順位も下げる。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from src.articles import build_compare_views, build_offer_views, build_stats, build_update_views
from src.catalog import ConfigError, load_catalog, load_site
from src.extract import extract
from src.fetch import Fetcher
from src.render import render_site
from src.track import (
    append,
    build_changes,
    is_stale,
    load_history,
    load_latest,
    save_latest,
    should_record,
    to_snapshot,
    tracked_since,
)
from src.verify import verify_site

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"
DOCS = ROOT / "docs"
HISTORY = DATA / "prices.jsonl"
LATEST = DATA / "latest.json"

log = logging.getLogger("kiji")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="記事型アフィリエイトサイト")
    p.add_argument("--check", action="store_true",
                   help="抽出の健全性を点検する（履歴に書かない）")
    p.add_argument("--render", action="store_true",
                   help="巡回せず既存の履歴からサイトだけ作り直す")
    p.add_argument("--verify", action="store_true",
                   help="生成済みの docs/ を検査する")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def crawl(catalog, site) -> list:
    """監視対象を巡回してスナップショットを作る。"""
    fetcher = Fetcher(site.user_agent, site.request_interval_sec,
                      site.timeout_sec)
    now = datetime.now(UTC)
    snaps = []
    for offer in catalog.watched:
        res = fetcher.get(offer.watch.url)
        if not res.ok:
            if res.blocked_by_robots:
                log.error("%s: robots.txt が禁止しています。config から外してください",
                          offer.slug)
            elif res.unreachable:
                log.error("%s: ホストに到達できません。URLを確認してください: %s",
                          offer.slug, offer.watch.url)
            else:
                log.warning("%s: 取得できませんでした (%s)", offer.slug, res.error)
            continue
        ex = extract(res.html, list(offer.watch.labels), offer.watch.min_amount)
        snap = to_snapshot(offer.slug, ex, now)
        snaps.append(snap)
        shown = len(snap.shown)
        log.info("%s: 金額トークン%d件 / 掲載可%d件 / 伏せる%d件 %s",
                 offer.slug, ex.token_count, shown,
                 len(snap.values) - shown, ex.note)
    return snaps


def render(site, catalog, history, latest, checked_at) -> int:
    stale = is_stale(checked_at, site.stale_after_days)
    offers = build_offer_views(catalog, history, latest, site, stale)
    compares = build_compare_views(offers, catalog)
    changes = build_changes(history)
    updates = build_update_views(changes, catalog, site.recent_updates)
    stats = build_stats(offers, changes, tracked_since(history), checked_at,
                        stale)
    return render_site(DOCS, ROOT / "templates", ROOT / "static", site,
                       offers, compares, updates, stats)


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s")

    try:
        site = load_site(CONFIG / "site.yaml")
        catalog = load_catalog(CONFIG / "offers.yaml")
    except ConfigError as e:
        log.error("設定が不正です: %s", e)
        return 2

    if args.verify:
        problems = verify_site(DOCS, site)
        for p in problems:
            log.error("%s", p)
        if problems:
            return 1
        log.info("docs/ の検査を通過しました")
        return 0

    if args.render:
        history = load_history(HISTORY)
        latest, checked_at = load_latest(LATEST)
        written = render(site, catalog, history, latest, checked_at)
        log.info("%d ページを再生成しました", written)
        return 0

    now = datetime.now(UTC)
    snaps = crawl(catalog, site)

    if not snaps and catalog.watched:
        # 全部落ちた日に履歴を空で上書きすると資産が壊れる。何もせず落ちる。
        log.error("1件も取得できませんでした。履歴は変更しません")
        return 1

    if args.check:
        # 点検のみ。履歴には書かない。
        #
        # 「ラベルに対応する金額を断定できなかった」は異常ではない。
        # 料金は offers.yaml の prices に人が書き写す設計なので、
        # 自動抽出が伏せられるのは想定どおり。ここで失敗にすると
        # 毎回鳴る警報になって誰も見なくなる。
        # 失敗にするのは「ページから金額が1つも取れない」= 監視自体が
        # 死んでいる場合だけ。
        for s in snaps:
            log.info("%s: 金額トークン%d件 / 掲載可%d件 / 伏せる%d件 %s",
                     s.slug, s.token_count, len(s.shown),
                     len(s.values) - len(s.shown), s.note)
        dead = [s for s in snaps if not s.ok]
        if dead:
            log.error("金額を1つも取得できないページが %d 件あります。"
                      "ページの作り替えか、JavaScript描画への変更を疑ってください: %s",
                      len(dead), ", ".join(s.slug for s in dead))
            return 1
        return 0

    history = load_history(HISTORY)
    fresh = [s for s in snaps
             if should_record((history.get(s.slug) or [None])[-1], s)]
    if fresh:
        append(HISTORY, fresh)
        for s in fresh:
            history.setdefault(s.slug, []).append(s)
        log.info("履歴に %d 件を追加しました（変化のなかった %d 件は記録しません）",
                 len(fresh), len(snaps) - len(fresh))
    else:
        log.info("料金に変化はありませんでした（%d 件を確認）", len(snaps))

    save_latest(LATEST, snaps, now)
    written = render(site, catalog, history,
                     {s.slug: s for s in snaps}, now.isoformat())
    log.info("%d ページを生成しました", written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
