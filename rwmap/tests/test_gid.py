"""GID 翻转标志测试（官方图实测有 84 个高位 GID）。"""
import unittest

from rwmap.document.gid import FLAG_D, FLAG_H, FLAG_V, make_gid, split_gid


class GidTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(split_gid(42), (42, False, False, False))

    def test_all_flags(self):
        raw = make_gid(1234, h=True, v=True, d=True)
        self.assertEqual(raw, 1234 | FLAG_H | FLAG_V | FLAG_D)
        self.assertEqual(split_gid(raw), (1234, True, True, True))

    def test_single_flags(self):
        for kwargs, expected in [
            ({"h": True}, (7, True, False, False)),
            ({"v": True}, (7, False, True, False)),
            ({"d": True}, (7, False, False, True)),
        ]:
            self.assertEqual(split_gid(make_gid(7, **kwargs)), expected)

    def test_mask(self):
        self.assertEqual(split_gid(0xF0000005)[0], 5)


if __name__ == "__main__":
    unittest.main()
