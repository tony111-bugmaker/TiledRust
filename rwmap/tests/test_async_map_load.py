"""异步地图加载测试：进度回调、信号桥成功/异常、MainWindow 异步入口。

全部在 offscreen 下运行；线程池在 offscreen 同样工作，不依赖真实 GL/屏幕。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402


def _sample_map():
    from rwmap.rw.version import active_profile
    return Path(active_profile().sample_map)


class ReadDocumentProgressTest(unittest.TestCase):
    def test_progress_is_monotonic_and_bounded(self):
        from rwmap.io.tmx_reader import read_document
        seen = []
        doc = read_document(_sample_map(), progress=lambda p, m: seen.append((p, m)))
        self.assertIsNotNone(doc)
        self.assertGreater(len(seen), 1)
        pcts = [p for p, _ in seen]
        self.assertEqual(pcts[0], 2)
        self.assertEqual(pcts[-1], 100)
        self.assertTrue(all(0 <= p <= 100 for p in pcts))
        self.assertTrue(all(pcts[i] <= pcts[i + 1] for i in range(len(pcts) - 1)),
                        f"进度必须单调不减: {pcts}")
        # 每条消息都应是字符串
        self.assertTrue(all(isinstance(m, str) and m for _, m in seen))

    def test_no_progress_callback_unchanged(self):
        from rwmap.io.tmx_reader import read_document
        doc = read_document(_sample_map())
        self.assertIsNotNone(doc)
        self.assertGreater(len(doc.layers), 0)


class MapLoaderSignalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _pump(self, cond, timeout=20.0):
        t0 = time.time()
        while not cond() and time.time() - t0 < timeout:
            self.app.processEvents()
            time.sleep(0.005)
        return cond()

    def test_success_emits_progress_and_doc(self):
        from rwmap.app.map_loader import MapLoadController, MapLoadSignals
        from rwmap.rw.version import active_profile

        sig = MapLoadSignals()
        ctl = MapLoadController(sig)
        got = {}
        sig.progress.connect(lambda p, m: got.setdefault("pcts", []).append(p))
        sig.succeeded.connect(lambda t, d: got.update(ok=True, token=t, doc=d))

        token = ctl.begin(_sample_map(), active_profile())
        self.assertTrue(self._pump(lambda: got.get("ok", False)))
        self.assertEqual(got["token"], token)
        self.assertIsNotNone(got["doc"])
        self.assertIn(100, got["pcts"])

    def test_failure_emits_error_not_exception(self):
        from rwmap.app.map_loader import MapLoadController, MapLoadSignals
        from rwmap.rw.version import active_profile

        sig = MapLoadSignals()
        ctl = MapLoadController(sig)
        got = {}
        sig.failed.connect(lambda t, e: got.update(failed=True, token=t, err=e))

        token = ctl.begin(Path("Z:/不存在/假的地图.tmx"), active_profile())
        self.assertTrue(self._pump(lambda: got.get("failed", False)))
        self.assertEqual(got["token"], token)
        self.assertTrue(got["err"])

    def test_cancel_all_invalidates_inflight(self):
        from rwmap.app.map_loader import MapLoadController
        ctl = MapLoadController()
        token = ctl.begin(_sample_map(), None)  # 不实际解析，只测状态机
        self.assertTrue(ctl.is_current(token))
        ctl.cancel_all()
        self.assertFalse(ctl.is_current(token))


class MainWindowAsyncOpenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_async_open_loads_same_doc_as_sync(self):
        from rwmap.app.main_window import MainWindow

        w = MainWindow(auto_open=False)
        w.show()
        path = _sample_map()
        w._open_async(path)
        t0 = time.time()
        while w._load_pending_token and time.time() - t0 < 25:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertEqual(w._load_pending_token, 0, "加载未结束")
        self.assertIsNotNone(w.state.doc)
        self.assertEqual(w.state.doc.path.name, path.name)
        self.assertFalse(w.load_progress.isVisible())
        w.close()

    def test_duplicate_open_is_ignored_while_loading(self):
        from rwmap.app.main_window import MainWindow

        w = MainWindow(auto_open=False)
        w.show()
        first = w._open_async(_sample_map())
        token_before = w._load_pending_token
        # 第二次请求应被忽略（token 不变）
        w._open_async(_sample_map())
        self.assertEqual(w._load_pending_token, token_before)
        t0 = time.time()
        while w._load_pending_token and time.time() - t0 < 25:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertIsNotNone(w.state.doc)
        w.close()


if __name__ == "__main__":
    unittest.main()
