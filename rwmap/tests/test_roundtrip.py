"""保真写回测试（复用探针）。"""
import unittest

from rwmap.probe.probe_roundtrip import run


class RoundtripTest(unittest.TestCase):
    def test_roundtrip(self):
        result = run()
        self.assertTrue(result["ok"], result.get("errors"))


if __name__ == "__main__":
    unittest.main()
