"""可行性探针：把 trigger_graph.html 用 QWebEngineView 嵌进 PySide6，并验证 Python↔JS 双向通信。

只做验证，不接入主程序。运行：
    set QT_QPA_PLATFORM=offscreen
    .venv\\Scripts\\python.exe rwmap\\probe\\probe_webengine_embed.py

验证 4 件事：
1) QWebEngineView 能否加载本地 HTML（loadFinished ok）
2) 内置 qwebchannel.js 是否可注入
3) JS 环境是否正常（读到节点数 / 连线数 / 状态栏）
4) QWebChannel 双向通信：JS 调 Python 的 ping() 并拿到返回值
"""
import os
import sys
import json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-gpu --disable-software-rasterizer --disable-dev-shm-usage",
)

from PySide6.QtCore import QUrl, QTimer, QFile, QIODevice, QObject, Slot
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

HTML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "trigger_graph.html",
)

RESULT = {"load_ok": None, "qwebchannel_js": None, "js_env": None, "bridge_roundtrip": None}


class Bridge(QObject):
    """注册给 JS 的对象：window.py / channel.objects.bridge"""

    @Slot(str, result=str)
    def ping(self, msg):
        print("[python] 收到 JS 调用 ping():", msg)
        return json.dumps({"ok": True, "echo": msg, "from": "python"}, ensure_ascii=False)


def main():
    print("[probe] html =", HTML, "exists =", os.path.exists(HTML))
    app = QApplication(sys.argv)
    view = QWebEngineView()
    view.resize(1200, 700)
    view.show()  # offscreen 下也需要 show 才会应用视口尺寸（否则 fitView 拿到 0 宽度）
    page = view.page()

    channel = QWebChannel(page)
    bridge = Bridge()
    channel.registerObject("bridge", bridge)
    page.setWebChannel(channel)

    qjs_path = ":/qtwebchannel/qwebchannel.js"
    qf = QFile(qjs_path)
    RESULT["qwebchannel_js"] = bool(qf.exists())
    print("[probe] 内置 qwebchannel.js 可用 =", RESULT["qwebchannel_js"])
    if qf.exists():
        qf.open(QIODevice.ReadOnly)
        QWEBCHANNEL_JS = bytes(qf.readAll()).decode("utf-8")
        qf.close()
    else:
        QWEBCHANNEL_JS = ""

    def on_load(ok):
        RESULT["load_ok"] = ok
        print("[probe] loadFinished ok =", ok)
        if not ok:
            finish()
            return

        # JS 环境检查
        page.runJavaScript(
            "JSON.stringify({nodes:Object.keys(state.nodes).length,"
            "edges:state.edges.length,title:document.title,"
            "stZoom:document.getElementById('stZoom').textContent})",
            lambda r: (RESULT.__setitem__("js_env", r), print("[probe] JS 环境 =", r)),
        )

        # 注入 qwebchannel.js 并建立通道
        if QWEBCHANNEL_JS:
            page.runJavaScript(QWEBCHANNEL_JS)
            page.runJavaScript(
                "new QWebChannel(qt.webChannelTransport, function(ch){"
                "  window.py = ch.objects.bridge;"
                "});"
            )

        # 800ms 后由 JS 反向调用 Python（ping 返回 Promise，用 then 取回值）
        def call_py():
            page.runJavaScript(
                "window.py ? window.py.ping('js-says-hi:' + Object.keys(state.nodes).length)"
                "  .then(function(r){ window.__ping = r; }) : (window.__ping = 'NO_BRIDGE');"
            )
            QTimer.singleShot(400, read_ping)

        def read_ping():
            page.runJavaScript(
                "window.__ping || 'PENDING'",
                lambda r: (RESULT.__setitem__("bridge_roundtrip", r), print("[probe] JS→Python 往返 =", r)),
            )

        QTimer.singleShot(800, call_py)
        QTimer.singleShot(2200, finish)

    def finish():
        # 尝试截图（offscreen 下可能为黑，仅参考）
        try:
            img = view.grab()
            out = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "..", "logs", "probe_webengine_embed.png",
            )
            out = os.path.abspath(out)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            img.save(out)
            print("[probe] 截图 ->", out, img.width(), "x", img.height())
        except Exception as e:  # noqa: BLE001
            print("[probe] 截图失败(可忽略):", e)

        print("\n===== 结论 =====")
        for k, v in RESULT.items():
            print(f"  {k:18s} = {v}")
        ok = RESULT["load_ok"] and RESULT["js_env"] and RESULT["bridge_roundtrip"] not in (None, "NO_BRIDGE")
        print("  可嵌入 rwmap:", "YES" if ok else "NO")
        app.quit()

    view.loadFinished.connect(on_load)
    view.load(QUrl.fromLocalFile(HTML))
    QTimer.singleShot(15000, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
