"""节点图连线的 GPU 渲染器（可选加速层）。

设计约定
--------
* **默认关闭（实验特性）**：本模块 lazy import。QPainter 侧已把节点图连线合成
  优化到 ~29ms（从 353ms），交互已不卡；而本 GPU 路径在 Windows + PySide6 6.12
  + ANGLE 环境下与 QGraphicsView 叠加存在深层兼容问题，无法稳定让节点与连线
  同屏。故 ``probe_gl_environment()`` 默认返回 False，仅 ``RWMAP_GRAPH_GPU=force``
  时作为实验开启。详见 probe_gl_environment 文档与项目记忆。
* **只看得到边**：GPU 只画「点阵背景 + 所有连线 + 箭头」，节点本体 / 文字 /
  框选 / 临时连线仍由 QGraphicsView 的 QPainter 负责。
* **零 numpy**：发行版 spec 的 excludes 含 numpy，VBO 数据一律用 ``array('f')``。
* **纯函数可测**：探测、打包、数学函数在文件前半部分，不依赖 GL 上下文，
  offscreen/CI 可以直接单测。

PySide6 6.12 API 备忘（与旧版不同，踩过坑）
------------------------------------------
* ``QOpenGLWidget`` 在 ``PySide6.QtOpenGLWidgets``；``QOpenGLShader/Program/Buffer/
  VertexArrayObject`` 在 ``PySide6.QtOpenGL``；``QOpenGLContext`` 在 ``QtGui``。
* ``QOpenGLFunctions``（QtGui）**没有** instanced 方法；要用
  ``QtOpenGL.QOpenGLFunctions_3_3_Core`` 并 ``initializeOpenGLFunctions()``。
* ``QOpenGLContext`` 没有 ``versionFunctions()``；直接实例化版本化 functions 对象，
  在 makeCurrent 之后初始化即可。
"""
from __future__ import annotations

import math
import os
import sys
import ctypes
from array import array
from dataclasses import dataclass

# GL 相关全部 lazy import：模块导入本身绝不触碰 PySide6 的 GL 绑定，
# 这样 frozen 环境（缺 QtOpenGL 绑定）也能安全 import 本模块并回退。
_GL_IMPORT_ERROR: str | None = None


def _import_gl():
    """导入并返回 GL 相关类；失败时返回 None 并记录原因。"""
    global _GL_IMPORT_ERROR
    if _GL_IMPORT_ERROR is not None:
        return None
    try:
        from PySide6.QtOpenGLWidgets import QOpenGLWidget
        from PySide6.QtOpenGL import (
            QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram,
            QOpenGLVertexArrayObject, QOpenGLFunctions_3_3_Core,
            QOpenGLFunctions_4_5_Core,
        )
        from PySide6.QtGui import QOpenGLContext
        return dict(
            QOpenGLWidget=QOpenGLWidget, QOpenGLBuffer=QOpenGLBuffer,
            QOpenGLShader=QOpenGLShader, QOpenGLShaderProgram=QOpenGLShaderProgram,
            QOpenGLVertexArrayObject=QOpenGLVertexArrayObject,
            QOpenGLFunctions_3_3_Core=QOpenGLFunctions_3_3_Core,
            QOpenGLFunctions_4_5_Core=QOpenGLFunctions_4_5_Core,
            QOpenGLContext=QOpenGLContext,
        )
    except Exception as exc:                      # noqa: BLE001 - 任何导入失败都要回退
        _GL_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
        return None


# ---- 探测闸门 ---------------------------------------------------------------
_SOFTWARE_TOKENS = (
    "llvmpipe", "softpipe", "swiftshader", "software", "microsoft basic render",
    "mesa offscreen", "gallium", "warp", "angle (google",
)


def _renderer_is_software(renderer: str) -> bool:
    low = (renderer or "").lower()
    return any(tok in low for tok in _SOFTWARE_TOKENS)


def probe_gl_environment(force: bool | None = None) -> tuple[bool, str]:
    """判断是否启用 GPU 连线渲染。返回 ``(enabled, reason)``。

    【默认关闭 —— 重要】在 Windows + PySide6 6.12 + ANGLE 环境下，
    QOpenGLWidget 与 QGraphicsView 叠加/作为 viewport 时存在深层兼容问题：
    scene.drawBackground 不被调用、GL 与 QPainter 绘制不在同一 FBO、节点与连线
    无法稳定共存（详见项目记忆）。而 QPainter 侧已把 2000 边合成从 353ms 优化到
    ~29ms，节点图交互已不卡。因此 GPU 路径默认回退，仅当显式设置
    ``RWMAP_GRAPH_GPU=force`` 时才作为实验特性开启，供未来换环境/重打包时评估。
    ``RWMAP_GRAPH_GPU=0`` 同样是回退。其余闸门：offscreen / frozen / 模块缺失 /
    上下文无效 / 软件渲染。
    """
    env = os.environ.get("RWMAP_GRAPH_GPU", "").lower()
    if force is None:
        force = env == "force"
    if env == "0" and not force:
        return False, "环境变量 RWMAP_GRAPH_GPU=0 关闭"
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        return False, "offscreen 无 GL 上下文"
    if getattr(sys, "frozen", False) and not force:
        return False, "frozen 发行版缺 GL 绑定（默认回退）"
    # 硬件环境下：未显式 force 则默认关闭 GPU 路径（见函数文档的原因说明）。
    if not force:
        return False, "GPU 连线默认关闭（设 RWMAP_GRAPH_GPU=force 开启实验）"

    gl = _import_gl()
    if gl is None:
        return False, f"GL 模块不可用：{_GL_IMPORT_ERROR}"

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return False, "无 QApplication，无法创建 GL 探针"

    probe = gl["QOpenGLWidget"]()
    probe.resize(8, 8)
    probe.show()
    app.processEvents()
    try:
        ctx = probe.context()
        if ctx is None or not ctx.isValid():
            return False, "GL 上下文无效"
        surf = ctx.surface()
        if not ctx.makeCurrent(surf):
            return False, "GL makeCurrent 失败"
        renderer = ""
        try:
            f = gl["QOpenGLFunctions_3_3_Core"]()
            if f.initializeOpenGLFunctions():
                raw = f.glGetString(0x1F01)       # GL_RENDERER
                if raw:
                    renderer = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
        except Exception:                          # noqa: BLE001
            pass
        ctx.doneCurrent()
        if _renderer_is_software(renderer) and not force:
            return False, f"软件渲染回退：{renderer}"
        return True, f"GPU 就绪：{renderer or '未知渲染器'}"
    finally:
        probe.hide()
        probe.deleteLater()


# ---- 打包常量 ---------------------------------------------------------------
FLOATS_PER_INSTANCE = 18
VERTICES_PER_INSTANCE = 56        # K=8 段 × 6 顶点 + 3 箭头 = 51，向上对齐到 56
K_SEGMENTS = 8

# 交错布局（单位：float 下标，乘 4 得字节偏移）
IDX_CTRL0, IDX_CTRL1, IDX_CTRL2, IDX_CTRL3 = 0, 2, 4, 6
IDX_COLOR = 8                     # r,g,b,a
IDX_TIP = 12                      # 箭头 tip（场景坐标）
IDX_DIR = 14                      # 箭头方向（场景坐标单位向量）
IDX_FLAGS = 16                    # bit0=hasArrow, bit1=overview
IDX_SCALE = 17                    # 箭头缩放（1.0 或 1/max(zoom,0.05)）

FLAG_HAS_ARROW = 1
FLAG_OVERVIEW = 2


@dataclass
class EdgeInstance:
    """一条边的 GPU 绘制数据（场景坐标，打包前）。"""
    eid: int
    ctrl: tuple                   # 4 个 (x,y)：裁剪后的子曲线控制点
    color: tuple                  # (r,g,b,a) 0..1
    tip: tuple = (0.0, 0.0)
    direction: tuple = (1.0, 0.0)
    has_arrow: bool = True
    overview: bool = False
    scale: float = 1.0


def pack_edge_instances(instances: list[EdgeInstance]) -> array:
    """把实例列表打成交错 float 数组（stride=18 float）。

    纯函数，不碰 GL；先在 CPU 算好，paintGL 里只做 bufferSubData。
    """
    out = array("f")
    for e in instances:
        c = e.ctrl
        out.extend((
            c[0][0], c[0][1], c[1][0], c[1][1],
            c[2][0], c[2][1], c[3][0], c[3][1],
            e.color[0], e.color[1], e.color[2], e.color[3],
            e.tip[0], e.tip[1], e.direction[0], e.direction[1],
            (FLAG_HAS_ARROW if e.has_arrow else 0)
            | (FLAG_OVERVIEW if e.overview else 0),
            e.scale,
        ))
    return out


# ---- 纯数学函数（shader 在 CPU 侧的对账基准） -------------------------------
def bezier_at(c: tuple, t: float) -> tuple[float, float]:
    """三次贝塞尔取点，返回 (x, y)；与 dock._cubic_at 数值一致。"""
    u = 1.0 - t
    w0, w1, w2, w3 = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (w0 * c[0][0] + w1 * c[1][0] + w2 * c[2][0] + w3 * c[3][0],
            w0 * c[0][1] + w1 * c[1][1] + w2 * c[2][1] + w3 * c[3][1])


def sdf_segment_alpha(dist: float, half_width: float, pixel_width: float) -> float:
    """点到线段距离 -> 抗锯齿覆盖率；与 shader 的 smoothstep 同式。

    pixel_width 即 fwidth(d)，一个像素跨度内距离的变化量。
    """
    if pixel_width <= 0.0:
        return 1.0 if dist <= half_width else 0.0
    lo, hi = half_width - pixel_width, half_width + pixel_width
    x = min(1.0, max(0.0, (dist - lo) / (hi - lo)))
    return 1.0 - x * x * (3.0 - 2.0 * x)


def arrow_scale_for(zoom: float, overview: bool) -> float:
    """与 dock._arrow_scaled 完全一致的缩放系数。"""
    return 1.0 / max(zoom, 0.05) if overview else 1.0


def make_scene_to_px(m11: float, m22: float, dx: float, dy: float,
                     dpr: float) -> tuple[float, float, float, float]:
    """场景→设备像素的 2x3 仿射（本应用无旋转/剪切）。

    返回 ``(a, d, tx, ty)`` 使得 px = (a*scene_x + tx, d*scene_y + ty)。
    """
    return (m11 * dpr, m22 * dpr, dx * dpr, dy * dpr)


def arrow_screen_vertices(tip: tuple, direction: tuple, scale_px: float):
    """屏幕空间箭头三角形（与 dock._paint_arrow 的数值一致）。

    scale_px 是屏幕像素下的基础尺寸倍数：detail 为 zoom，overview 为 1。
    """
    size, half = 10.0 * scale_px, 4.5 * scale_px
    ux, uy = direction
    px, py = -uy, ux
    bx, by = tip[0] - ux * size, tip[1] - uy * size
    return ((tip[0], tip[1]),
            (bx + px * half, by + py * half),
            (bx - px * half, by - py * half))




# ---- Shader -----------------------------------------------------------------
# 两套 header + 一份 body：330 core（桌面 GL）与 300 es（GLES）。
# 约定：所有屏幕坐标一律 y 向下（与 QPainter/QWidget 一致），gl_FragCoord.y
# 在 FS 里先用 uViewportPx.y 翻转。scene->px 由 uSceneToPx=(a,d,tx,ty) 给出。
_HEADER_330 = "#version 330 core\n"
_HEADER_300ES = "#version 300 es\nprecision highp float;\nprecision highp int;\n"

_BG_VERT = """
uniform vec2 uViewportPx;
out vec2 vPx;
void main() {
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    vPx = p * uViewportPx;                 // y 向下的屏幕坐标
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

_BG_FRAG = """
uniform vec2 uViewportPx;
uniform vec3 uPxToScene;          // (1/a, 1/d, 0) —— scene=px*k+t
uniform vec2 uSceneOffset;        // (-tx/a, -ty/d)
uniform float uDotGap;            // 24.0
uniform vec3 uBgColor;
uniform vec3 uDotColor;
in vec2 vPx;
out vec4 outColor;
void main() {
    vec2 scene = vec2(vPx.x * uPxToScene.x + uSceneOffset.x,
                      vPx.y * uPxToScene.y + uSceneOffset.y);
    vec2 cell = fract(scene / uDotGap) - 0.5;
    float dist = length(cell) * uDotGap;   // 到格心距离（场景单位）
    float w = fwidth(dist);
    float dotA = 1.0 - smoothstep(1.0 - w, 1.0 + w, dist);
    outColor = vec4(mix(uBgColor, uDotColor, dotA), 1.0);
}
"""

# 每实例 51 顶点：K=8 段 × 6（两个三角形） + 3（箭头）。
_EDGE_VERT = """
uniform vec2 uViewportPx;
uniform vec4 uSceneToPx;          // a, d, tx, ty
uniform float uHalfWidthPx;       // 0.9
layout(location=0) in vec2 aCtrl0;
layout(location=1) in vec2 aCtrl1;
layout(location=2) in vec2 aCtrl2;
layout(location=3) in vec2 aCtrl3;
layout(location=4) in vec4 aColor;
layout(location=5) in vec2 aTip;
layout(location=6) in vec2 aDir;
layout(location=7) in float aFlags;
layout(location=8) in float aScale;

out vec2 vA;
out vec2 vB;
out vec4 vColor;
out float vPrim;                  // 0=线段, 1=箭头
flat out int vArrowOn;
out vec3 vBary;

const int K = 8;
const int LINE_VERTS = K * 6;

vec2 s2px(vec2 s) {
    return vec2(uSceneToPx.x * s.x + uSceneToPx.z,
                uSceneToPx.y * s.y + uSceneToPx.w);
}
vec2 bez(float t) {
    float u = 1.0 - t;
    return u*u*u*aCtrl0 + 3.0*u*u*t*aCtrl1 + 3.0*u*t*t*aCtrl2 + t*t*t*aCtrl3;
}
vec4 ndc(vec2 px) {
    return vec4(px.x / uViewportPx.x * 2.0 - 1.0,
                1.0 - px.y / uViewportPx.y * 2.0, 0.0, 1.0);
}

void main() {
    vColor = aColor;
    int vid = gl_VertexID;
    if (vid < LINE_VERTS) {
        int seg = vid / 6;
        int corner = vid % 6;
        vec2 s0 = s2px(bez(float(seg) / float(K)));
        vec2 s1 = s2px(bez(float(seg + 1) / float(K)));
        vec2 d = s1 - s0;
        float len = length(d);
        vec2 n = len > 1e-4 ? vec2(-d.y, d.x) / len : vec2(0.0, 1.0);
        float h = uHalfWidthPx + 1.5;
        vec2 p;
        if (corner == 0 || corner == 3) p = s0 + n * h;
        else if (corner == 1 || corner == 4) p = s0 - n * h;
        else if (corner == 2) p = s1 + n * h;
        else p = s1 - n * h;
        vA = s0; vB = s1; vPrim = 0.0; vArrowOn = 0; vBary = vec3(0.0);
        gl_Position = ndc(p);
    } else {
        int ci = vid - LINE_VERTS;
        vec2 tip = s2px(aTip);
        vec2 dln = s2px(aTip + aDir) - tip;
        float dl = length(dln);
        vec2 d = dl > 1e-4 ? dln / dl : vec2(1.0, 0.0);
        vec2 perp = vec2(-d.y, d.x);
        float zoom = uSceneToPx.x;          // 各向同性
        float size = 10.0 * aScale * zoom;
        float hw = 4.5 * aScale * zoom;
        vec2 base = tip - d * size;
        vec2 p = ci == 0 ? tip : (ci == 1 ? base + perp * hw : base - perp * hw);
        vBary = ci == 0 ? vec3(1.0, 0.0, 0.0)
              : (ci == 1 ? vec3(0.0, 1.0, 0.0) : vec3(0.0, 0.0, 1.0));
        vPrim = 1.0;
        vArrowOn = (int(aFlags) & 1);
        vA = vec2(0.0); vB = vec2(0.0);
        gl_Position = ndc(p);
        if (vArrowOn == 0) gl_Position = vec4(2.0, 2.0, 0.0, 1.0);  // 退化
    }
}
"""

_EDGE_FRAG = """
uniform float uHalfWidthPx;
uniform float uViewportPxH;
in vec2 vA;
in vec2 vB;
in vec4 vColor;
in float vPrim;
flat in int vArrowOn;
in vec3 vBary;
out vec4 outColor;

float dist_point_seg(vec2 p, vec2 a, vec2 b) {
    vec2 pa = p - a, ba = b - a;
    float h = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-6), 0.0, 1.0);
    return length(pa - ba * h);
}
void main() {
    float alpha;
    if (vPrim < 0.5) {
        // gl_FragCoord 原点左下，翻转成 y 向下与 vA/vB 同系
        vec2 frag = vec2(gl_FragCoord.x, uViewportPxH - gl_FragCoord.y);
        float d = dist_point_seg(frag, vA, vB);
        float w = fwidth(d);
        alpha = 1.0 - smoothstep(uHalfWidthPx - w, uHalfWidthPx + w, d);
    } else {
        float w = fwidth(vBary.x) + fwidth(vBary.y) + fwidth(vBary.z);
        float m = min(min(vBary.x, vBary.y), vBary.z);
        alpha = 1.0 - smoothstep(-w, w, -m);
    }
    if (alpha <= 0.003) discard;
    outColor = vec4(vColor.rgb, vColor.a * alpha);
}
"""


def clip_ctrl_for_view(ctrl: tuple, visible, cache_key, cache: dict):
    """把贝塞尔裁剪到视口，返回子曲线控制点（与 dock._clipped_path 同一算法）。

    * ``visible`` 为 None（测试/无 view）时返回原 ctrl；
    * 缓存命中直接返回（None 表示整条可见）。

    ``ctrl`` 与 ``visible`` 用 dock 侧的 QPointF/QRectF；这里只读 .x()/.y()，
    不引入 Qt 依赖到返回值，便于单测。
    """
    if visible is None:
        return ctrl
    if cache_key in cache:
        got = cache[cache_key]
        return ctrl if got is None else got

    from PySide6.QtCore import QPointF
    from rwmap.widgets.trigger_graph_dock import _visible_t_range, _sub_cubic

    t0, t1 = _visible_t_range(ctrl, visible)
    if t0 <= 0.0 and t1 >= 1.0:
        cache[cache_key] = None
        return ctrl
    sub = _sub_cubic(ctrl, t0, t1)
    result = tuple((p.x(), p.y()) for p in sub)
    cache[cache_key] = result
    return result


def _build_shaders(header: str, gl):
    """编译背景/连线两套 shader program，失败抛 RuntimeError。"""
    Shader, Program = gl["QOpenGLShader"], gl["QOpenGLShaderProgram"]

    def compile_one(kind, src):
        s = Shader(kind)
        if not s.compileSourceCode(src):
            raise RuntimeError("shader 编译失败: " + str(s.log()))
        return s

    # PySide6 6.12：shader 类型在 QOpenGLShader.ShaderTypeBit 枚举里
    VTX, FRG = Shader.ShaderTypeBit.Vertex, Shader.ShaderTypeBit.Fragment
    bg = Program()
    bg.addShader(compile_one(VTX, header + _BG_VERT))
    bg.addShader(compile_one(FRG, header + _BG_FRAG))
    if not bg.link():
        raise RuntimeError("bg shader 链接失败: " + str(bg.log()))

    edge = Program()
    edge.addShader(compile_one(VTX, header + _EDGE_VERT))
    edge.addShader(compile_one(FRG, header + _EDGE_FRAG))
    if not edge.link():
        raise RuntimeError("edge shader 链接失败: " + str(edge.log()))
    return bg, edge


# ---- GL canvas --------------------------------------------------------------
class GraphEdgeGLCanvas:
    """GL 绘制代理：由 dock 创建的 QOpenGLWidget 子类实例驱动。

    刻意不在这里继承 QOpenGLWidget —— 子类定义放在工厂函数里，避免本模块
    import 时就需要 GL 类（frozen 回退路径必须能安全 import）。
    """

    def __init__(self, widget, dock):
        self.widget = widget
        self.dock = dock
        self.gl = None
        self.f = None                       # 版本化 QOpenGLFunctions
        self.bg_program = None
        self.edge_program = None
        self.vao = None
        self.vbo = None
        self.ready = False
        self._instance_count = 0
        self._buffer = array("f")
        self._geometry_dirty = True
        self._buffer_allocated = False
        # 背景主题色（QColor -> 0..1 rgb）
        self.bg_color = (0.118, 0.118, 0.118)
        self.dot_color = (0.165, 0.180, 0.204)
        self.dot_gap = 24.0

    # ---- 生命周期 ----
    def initialize(self):
        """在 QOpenGLWidget.initializeGL 里调用。"""
        gl = _import_gl()
        if gl is None:
            return
        self.gl = gl
        from PySide6.QtOpenGL import QOpenGLFunctions_3_3_Core, QOpenGLFunctions_4_5_Core
        ctx = self.widget.context()
        is_es = ctx.isOpenGLES() if ctx else False
        header = _HEADER_300ES if is_es else _HEADER_330
        try:
            self.bg_program, self.edge_program = _build_shaders(header, gl)
        except Exception as exc:                 # noqa: BLE001
            self._fail("shader: " + str(exc))
            return

        # 选一个支持 instanced 的 functions 对象
        f = QOpenGLFunctions_4_5_Core()
        if not f.initializeOpenGLFunctions():
            f = QOpenGLFunctions_3_3_Core()
            if not f.initializeOpenGLFunctions():
                self._fail("QOpenGLFunctions 初始化失败")
                return
        self.f = f

        try:
            self.vao = gl["QOpenGLVertexArrayObject"](self.widget)
            self.vao.create()
            self.vbo = gl["QOpenGLBuffer"](gl["QOpenGLBuffer"].VertexBuffer)
            self.vbo.create()
        except Exception as exc:                 # noqa: BLE001
            self._fail(f"VAO/VBO 创建失败：{exc}")
            return

        # 配置 VAO 属性指针（divisor=1，逐实例）。
        #
        # 【关键坑，PySide6 6.12 + Windows ANGLE 实测】底层
        # ``glVertexAttribPointer(..., ctypes.c_void_p(offset))`` 在本环境**不会**
        # 真正把 VBO 数据喂给顶点属性：所有实例的属性恒读到 0，导致线全部退化、
        # edge pass 什么都画不出来（背景 pass 只用 gl_VertexID 故不受影响）。
        # 必须用 Qt 的高层 ``QOpenGLShaderProgram.setAttributeBuffer()``，它在
        # C++ 侧正确构造 attr 指针。已用最小实例测试确认：setAttributeBuffer +
        # glVertexAttribDivisor 能让每个实例读到各自的 aPos（红/绿分别落在正确位置）。
        self.vao.bind()
        self.vbo.bind()
        self.edge_program.bind()
        stride = FLOATS_PER_INSTANCE * 4
        # setAttributeBuffer(location, type, offsetBytes, tupleSize, strideBytes)
        # offset 是**字节**单位（已实测交错布局验证：offset=8 正确读到第 2 个 float）。
        layout = [
            (0, 2, IDX_CTRL0 * 4), (1, 2, IDX_CTRL1 * 4),
            (2, 2, IDX_CTRL2 * 4), (3, 2, IDX_CTRL3 * 4),
            (4, 4, IDX_COLOR * 4), (5, 2, IDX_TIP * 4),
            (6, 2, IDX_DIR * 4), (7, 1, IDX_FLAGS * 4),
            (8, 1, IDX_SCALE * 4),
        ]
        for loc, size, offset_bytes in layout:
            self.edge_program.enableAttributeArray(loc)
            self.edge_program.setAttributeBuffer(
                loc, 0x1406, offset_bytes, size, stride)  # 0x1406 = GL_FLOAT
            f.glVertexAttribDivisor(loc, 1)
        self.edge_program.release()
        self.vbo.release()
        self.vao.release()
        self.ready = True

    def _fail(self, reason: str):
        self.ready = False
        self.dock._on_gl_failed(reason)

    def resize(self, w: int, h: int):
        """paintGL 开头调用，设置 viewport（已乘 dpr）。"""
        if self.f is None:
            return
        dpr = self.widget.devicePixelRatioF()
        self.f.glViewport(0, 0, int(w * dpr), int(h * dpr))

    def mark_geometry_dirty(self):
        """标记需要 dock 在下一帧重新打包几何（节点移动/数据变化时调用）。"""
        self._geometry_dirty = True

    def set_geometry(self, data: array, count: int):
        """更新 VBO 数据（在 paintGL 之前由 dock 计算好）。

        buffer 容量不变时用 write 覆盖，避免每帧 allocate 造成 GPU 内存抖动。
        """
        self._buffer = data
        self._instance_count = count
        self._geometry_dirty = False

    def render(self):
        """上传数据 + 两次 draw call（由 drawBackground 的 native painting 块调用）。"""
        if not self.ready or self.f is None:
            return
        # 兜底：若几何尚未打包（首次绘制 / drawBackground 未先调 sync），在这里补一次，
        # 确保 VBO 一定有连线数据。dock._gpu_sync_geometry 内部有 dirty 判断，重复调用
        # 是无副作用的空操作。
        dock = self.dock
        if dock is not None and self._instance_count == 0 and self._geometry_dirty:
            try:
                dock._gpu_sync_geometry()
            except Exception:                # noqa: BLE001 - 同步失败就让本帧画背景
                pass
        f = self.f
        dpr = self.widget.devicePixelRatioF()
        vp_w = int(self.widget.width() * dpr)
        vp_h = int(self.widget.height() * dpr)

        f.glDisable(0x0BA1)         # GL_DEPTH_TEST
        f.glEnable(0x0BE2)          # GL_BLEND
        f.glBlendFunc(0x0302, 0x0303)   # SRC_ALPHA, ONE_MINUS_SRC_ALPHA
        f.glClearColor(*self.bg_color, 1.0)
        f.glClear(0x4000)           # GL_COLOR_BUFFER_BIT

        # PySide6 6.12 的 setUniformValue 绑定有 bug（float/int 都报类型错），
        # 直接用 GL 函数 + uniformLocation 设置，稳定可靠。
        def u1i(prog, name, v):
            f.glUniform1i(prog.uniformLocation(name), int(v))
        def u1f(prog, name, v):
            f.glUniform1f(prog.uniformLocation(name), float(v))
        def u2f(prog, name, x, y):
            f.glUniform2f(prog.uniformLocation(name), float(x), float(y))
        def u3f(prog, name, x, y, z):
            f.glUniform3f(prog.uniformLocation(name), float(x), float(y), float(z))
        def u4f(prog, name, x, y, z, w):
            f.glUniform4f(prog.uniformLocation(name), float(x), float(y), float(z), float(w))

        s2p = self.dock.graph_viewport_matrix()   # (a, d, tx, ty) 场景->px（含 dpr）
        a, d, tx, ty = s2p

        # ---- pass 1: 背景点阵 ----
        f.glUseProgram(self.bg_program.programId())
        u2f(self.bg_program, "uViewportPx", vp_w, vp_h)
        u3f(self.bg_program, "uPxToScene", 1.0 / a, 1.0 / d, 0.0)
        u2f(self.bg_program, "uSceneOffset", -tx / a, -ty / d)
        u1f(self.bg_program, "uDotGap", self.dot_gap)
        u3f(self.bg_program, "uBgColor", *self.bg_color)
        u3f(self.bg_program, "uDotColor", *self.dot_color)
        f.glDrawArrays(0x0004, 0, 3)            # GL_TRIANGLES

        if self._instance_count == 0 or len(self._buffer) == 0:
            f.glUseProgram(0)
            return

        # ---- pass 2: 连线 + 箭头 ----
        self.vao.bind()
        self.vbo.bind()
        blob = self._buffer.tobytes()
        if not self._buffer_allocated:
            self.vbo.allocate(blob, len(self._buffer) * self._buffer.itemsize)
            self._buffer_allocated = True
        elif len(blob) == self.vbo.size():
            self.vbo.write(0, blob, len(blob))
        else:
            self.vbo.allocate(blob, len(self._buffer) * self._buffer.itemsize)
        f.glUseProgram(self.edge_program.programId())
        u2f(self.edge_program, "uViewportPx", vp_w, vp_h)
        u4f(self.edge_program, "uSceneToPx", a, d, tx, ty)
        u1f(self.edge_program, "uHalfWidthPx", 0.9)
        u1f(self.edge_program, "uViewportPxH", float(vp_h))
        f.glDrawArraysInstanced(0x0004, 0, VERTICES_PER_INSTANCE,
                                self._instance_count)
        f.glUseProgram(0)
        self.vbo.release()
        self.vao.release()

    def cleanup(self):
        """context 销毁时调用；不要 deleteLater GL 资源，直接 destroy。"""
        for obj in (self.vbo, self.vao, self.edge_program, self.bg_program):
            try:
                if obj is not None:
                    obj.destroy()
            except Exception:                      # noqa: BLE001
                pass
        self.ready = False


def make_gl_widget_class():
    """工厂：返回一个 QOpenGLWidget 子类（只在需要时调用）。"""
    gl = _import_gl()
    if gl is None:
        return None
    QOpenGLWidget = gl["QOpenGLWidget"]

    class _GraphGLWidget(QOpenGLWidget):
        def __init__(self, dock, parent=None):
            super().__init__(parent)
            self._engine = GraphEdgeGLCanvas(self, dock)

        def initializeGL(self):
            self._engine.initialize()

        def resizeGL(self, w, h):
            self._engine.resize(w, h)

        def paintGL(self):
            # 方案 B：GL 内容全部由 GraphView.paintEvent 里的 QPainter.native painting
            # 块绘制（先 GL 点阵/连线，再 QPainter 节点），本回调刻意空转——
            # 若在这里 glClear/render 会清掉已画好的节点层。
            pass

        def aboutToBeDestroyed(self):
            pass   # 信号在 dock 侧连接

        def engine(self):
            return self._engine

    return _GraphGLWidget
