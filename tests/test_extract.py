"""料金抽出のテスト。

ケースは実際の公式サイトから採取した形。ここが静かに壊れると
「料金が空欄のサイト」か、もっと悪い「間違った料金を載せたサイト」になる。
間違った料金を載せる方がはるかに悪いので、迷ったら伏せる側に倒してある。
"""

from __future__ import annotations

from src.extract import extract, to_text


class TestText:
    def test_金額と円の間の改行を潰す(self):
        # 実測: DMM英会話 "22,880 \n 円" / ネイティブキャンプ "9,800 \n    円"
        # ここを潰さないと金額を1件も拾えず、料金が全部空欄になる。
        html = "<p>スタンダードプラン<br>\n  6,980\n\t円</p>"
        assert extract(html, ["スタンダードプラン"]).values[0].amount == 6980

    def test_scriptの中身は金額ではない(self):
        html = ('<script>var price = "99,999円";</script>'
                '<p>プランA 3,000円</p>')
        assert "99,999" not in to_text(html)

    def test_タグをまたいだ金額も読む(self):
        html = "<p>プランA <span class='num'>3,000</span><span>円</span></p>"
        assert extract(html, ["プランA"]).values[0].amount == 3000


class TestSignature:
    def test_同じ金額集合なら同じ署名(self):
        a = extract("<p>1,000円 2,000円</p>", [])
        b = extract("<p>1,000円 2,000円</p>", [])
        assert a.signature == b.signature

    def test_並び順が変わっても同じ署名(self):
        # 相手のページで並びが入れ替わっただけで「値上げ」にしてはいけない。
        a = extract("<p>1,000円 2,000円</p>", [])
        b = extract("<p>2,000円 1,000円</p>", [])
        assert a.signature == b.signature

    def test_金額が変われば署名も変わる(self):
        a = extract("<p>1,000円</p>", [])
        b = extract("<p>1,200円</p>", [])
        assert a.signature != b.signature

    def test_金額が無ければ失敗として返す(self):
        # 200 が返っているのに金額ゼロ。SPA かページの作り替え。
        # 履歴を空で上書きしないよう ok=False にする。
        ex = extract("<p>お問い合わせください</p>", ["プランA"])
        assert ex.ok is False
        assert ex.signature == ""


class TestConfidence:
    def test_通常価格と割引価格が並ぶと伏せる(self):
        # 実測: ネイティブキャンプは通常7,480円と年間割引6,800円が併記されていて、
        # どちらがそのプランの料金か機械では断定できない。
        html = "<p>プレミアムプラン 通常 7,480円 年間割引 6,800円</p>"
        v = extract(html, ["プレミアムプラン"]).values[0]
        assert v.confidence == "low"

    def test_金額が1つだけなら掲載してよい(self):
        html = "<p>プレミアムプラン 7,480円</p>"
        v = extract(html, ["プレミアムプラン"]).values[0]
        assert v.confidence == "high"

    def test_下限未満の数字は料金とみなさない(self):
        # 実測: DMM英会話から "1円" を拾っていた。月額として非現実的。
        html = "<p>スタンダードプラン ポイントは1円から使えます 6,980円</p>"
        v = extract(html, ["スタンダードプラン"]).values[0]
        assert v.amount == 6980

    def test_ラベルが消えたら値を返さない(self):
        # プラン名が改称されたケース。推測で別の金額を当てない。
        ex = extract("<p>新プラン 5,000円</p>", ["旧プラン"])
        assert ex.values == ()
        assert ex.ok is True   # ページ自体は読めているので署名は取れる


class TestDiscount:
    def test_値引き額はプランの料金ではない(self):
        # 拾うと割引額を月額として掲載してしまう。
        html = "<p>プランA 今なら3,000円お得 5,980円</p>"
        assert extract(html, ["プランA"]).values[0].amount == 5980

    def test_キャッシュバック額も除く(self):
        html = "<p>プランB 10,000円キャッシュバック 4,400円</p>"
        assert extract(html, ["プランB"]).values[0].amount == 4400
