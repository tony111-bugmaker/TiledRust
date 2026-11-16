"""层数据编解码测试。"""
import unittest

from lxml import etree

from rwmap.io.layer_codec import decode, encode

CELLS = [0, 1, 35, 0x80000005, 0xFFFFFFFF, 42]


class LayerCodecTest(unittest.TestCase):
    def _roundtrip(self, compression):
        node = etree.Element("data", encoding="base64")
        if compression:
            node.set("compression", compression)
        node.text = encode(CELLS, "base64", compression)
        decoded, _, _ = decode(node, len(CELLS))
        self.assertEqual(decoded, CELLS)

    def test_gzip(self):
        self._roundtrip("gzip")

    def test_zlib(self):
        self._roundtrip("zlib")

    def test_none(self):
        self._roundtrip(None)

    def test_csv(self):
        node = etree.Element("data", encoding="csv")
        node.text = encode(CELLS, "csv", None)
        decoded, _, _ = decode(node, len(CELLS))
        self.assertEqual(decoded, CELLS)

    def test_magic_fallback(self):
        node = etree.Element("data", encoding="base64", compression="gzip")
        node.text = encode(CELLS, "base64", "zlib")   # 声明与载荷不一致
        decoded, _, _ = decode(node, len(CELLS))
        self.assertEqual(decoded, CELLS)


if __name__ == "__main__":
    unittest.main()
