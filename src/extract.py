"""監視ページの HTML から料金を取り出す。

【2段構え】完璧なパースは不可能だと最初から認めている
  相手のページは予告なく作り替わるし、SPA だと料金が HTML に載っていないこともある。

  1. signature (堅い)  … ページ上の金額トークン集合のハッシュ。
                         個々のプランを正しく取れなくても「何かが変わった」は
                         高い精度で検出できる。更新履歴の中核はこちら。
  2. values (best-effort) … ラベルの近傍から金額を拾う。外すことがある。
                         外したら confidence を下げ、表示側で伏せる。

  取れなかったものを推測で埋めない。料金で嘘の数字を出したら終わりなので、
  「分からない」を「分からない」のまま返すのがこのモジュールの責任。

【実測で踏んだ落とし穴】
  金額と「円」の間に改行とタブが入る。DMM英会話は "22,880 \\n 円"、
  ネイティブキャンプは "9,800 \\n     円"。\\s* を挟まないと全部取り逃す。
  取り逃しても例外は出ず、ただ料金が空欄になるだけなので気付きにくい。
"""

from __future__ import annotations

import hashlib
import html as html_mod
import re
from dataclasses import dataclass, field

# 金額と単位の間に改行・タブ・全角空白が入る。実測済み。
YEN_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s*円")
TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
STRIP_RE = re.compile(r"<[^>]+>")

# 「1,000円お得」「最大10,000円割引」のような値引き額はプランの料金ではない。
# 拾うと割引額を月額として掲載してしまう。
#
# 【隣接だけを見る】前後に広い窓を取ってはいけない。
#   "今なら3,000円お得 5,980円" で40文字の窓を使うと、値引き額(3,000)だけでなく
#   本来の料金(5,980)まで巻き添えで除外され、料金が丸ごと消える。
#   値引き語は必ず金額に隣接する（"3,000円お得" / "キャッシュバック10,000円"）ので、
#   隙間なく接している場合だけを値引きとみなす。
_DISCOUNT_WORDS = r"割引|お得|OFF|オフ|還元|キャッシュバック|プレゼント|相当|値引"
# 金額の直後（空白を挟まず）に値引き語が来る形: "3,000円お得"
DISCOUNT_AFTER_RE = re.compile(rf"^\s{{0,1}}({_DISCOUNT_WORDS})")
# 金額の直前に隙間なく値引き語が来る形: "キャッシュバック10,000円"
DISCOUNT_BEFORE_RE = re.compile(rf"({_DISCOUNT_WORDS})$")

# ラベルからこの文字数以内にある金額を、そのラベルの料金とみなす。
# 広げすぎると隣のプランの金額を拾う。
LABEL_WINDOW = 220

# これ未満の金額は月額料金とみなさない。実測で DMM英会話 から "1円" を
# 拾っていた（ページ内の別文脈の数字）。月額の下限として現実的な値で切る。
# offers.yaml の min_amount で案件ごとに上書きできる。
DEFAULT_MIN_AMOUNT = 500


@dataclass(frozen=True)
class Value:
    label: str
    amount: float
    confidence: str   # "high" | "low"

    @property
    def display(self) -> str:
        return f"{self.amount:,.0f}円"


@dataclass(frozen=True)
class Extraction:
    ok: bool
    signature: str
    values: tuple[Value, ...] = ()
    token_count: int = 0
    note: str = ""
    _seen: tuple[float, ...] = field(default=(), repr=False)

    def value(self, label: str) -> Value | None:
        for v in self.values:
            if v.label == label:
                return v
        return None


def to_text(html: str) -> str:
    """HTML をプレーンテキストにする。script/style の中身は金額ではないので先に落とす。"""
    body = TAG_RE.sub(" ", html)
    body = STRIP_RE.sub(" ", body)
    body = html_mod.unescape(body)
    # 金額と「円」の間の改行を潰す。ここが実測で一番効いた。
    return re.sub(r"[\s　]+", " ", body)


def _amounts(text: str) -> list[tuple[float, int]]:
    """(金額, 出現位置) の一覧。値引き表記の近くのものは除く。"""
    out: list[tuple[float, int]] = []
    for m in YEN_RE.finditer(text):
        # 値引き語が金額に隣接している場合だけ除く。窓を広げると
        # 隣の正規の料金まで落ちる。
        head = text[max(0, m.start() - 12):m.start()]
        tail = text[m.end():m.end() + 12]
        if DISCOUNT_BEFORE_RE.search(head) or DISCOUNT_AFTER_RE.search(tail):
            continue
        out.append((float(m.group(1).replace(",", "")), m.start()))
    return out


def extract(html: str, labels: list[str],
            min_amount: float = DEFAULT_MIN_AMOUNT) -> Extraction:
    """監視ページから料金を取り出す。

    ラベル近傍の抽出は当てにならない。実測では
      - ネイティブキャンプ: 通常月額 7,480円 ではなく年間割引の 6,800円 を拾った
      - DMM英会話: 別文脈の "1円" を拾った
    という結果だった。だから値は confidence 付きで返し、low のものは
    表示側で必ず伏せる。間違った料金を出すくらいなら空欄の方がよい。
    """
    text = to_text(html)
    found = _amounts(text)
    if not found:
        # 200 が返っているのに金額が1つも無い。SPA か、ページの作り替え。
        # 履歴を空で上書きしないよう、失敗として返す。
        return Extraction(ok=False, signature="", note="金額が1つも見つかりません")

    amounts = [a for a, _ in found]
    # 署名は金額の集合から作る。並び順で変わらないよう並べ替えてから。
    signature = hashlib.sha256(
        ",".join(f"{a:.0f}" for a in sorted(set(amounts))).encode()
    ).hexdigest()[:16]

    # ラベルの料金候補は下限以上のものだけ。下限未満は別文脈の数字。
    plausible = [(a, p) for a, p in found if a >= min_amount]

    values: list[Value] = []
    for label in labels:
        pos = text.find(label)
        if pos < 0:
            # ラベルがページから消えた。プラン名の改称か、記載の削除。
            continue
        near = [(a, p) for a, p in plausible if 0 <= p - pos <= LABEL_WINDOW]
        if not near:
            continue
        near.sort(key=lambda x: x[1] - pos)
        amount = near[0][0]
        # ラベルの近くに違う金額が複数あると、どれがそのプランの料金か
        # 判別できない（通常価格と割引価格が並んでいる形が典型）。
        # 断定できないものは low にして表示側で伏せる。
        distinct_near = {a for a, _ in near}
        confidence = ("high"
                      if len(distinct_near) == 1 and amounts.count(amount) <= 3
                      else "low")
        values.append(Value(label=label, amount=amount, confidence=confidence))

    return Extraction(
        ok=True,
        signature=signature,
        values=tuple(values),
        token_count=len(found),
        note="" if values or not labels else "ラベルに対応する金額が取れませんでした",
        _seen=tuple(sorted(set(amounts))),
    )
