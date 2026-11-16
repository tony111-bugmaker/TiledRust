"""TMX GID 与翻转标志。

Tiled GID 是 uint32：低 28 位是图块编号，高 4 位含 H/V/D 翻转标志。
官方地图实测存在 84 个带高位标志的 GID，必须拆装后再渲染与写回。
"""
GID_MASK = 0x0FFFFFFF
FLAG_H = 0x80000000
FLAG_V = 0x40000000
FLAG_D = 0x20000000
FLAG_HEX = 0x10000000


def split_gid(raw: int) -> tuple[int, bool, bool, bool]:
    """返回 (纯 gid, 水平翻转, 垂直翻转, 对角翻转)。"""
    raw = int(raw) & 0xFFFFFFFF
    return (
        raw & GID_MASK,
        bool(raw & FLAG_H),
        bool(raw & FLAG_V),
        bool(raw & FLAG_D),
    )


def make_gid(gid: int, h: bool = False, v: bool = False, d: bool = False) -> int:
    raw = int(gid) & GID_MASK
    if h:
        raw |= FLAG_H
    if v:
        raw |= FLAG_V
    if d:
        raw |= FLAG_D
    return raw


def has_flags(raw: int) -> bool:
    return bool(int(raw) & 0xF0000000)
