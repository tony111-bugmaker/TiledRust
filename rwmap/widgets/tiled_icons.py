"""Tiled 原生图层面板图标（深色主题适配版，自包含）。

来源：本机 <Tiled目录>/tilededitor.dll 内嵌 Qt 资源 PNG，按提取编号见
rwmap/probe/extract_tiled_icons.py 的 ICON_IDS；深色化只改像素亮度/色相，
不改形状，面板整体保持深色。图标数据内嵌为 base64，打包 exe 无需附带文件。
"""
from __future__ import annotations

import base64

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QIcon, QPixmap

_DATA: dict[str, str] = {
    "visible": (
        "iVBORw0KGgoAAAANSUhEUgAAAA4AAAAOCAYAAAAfSC3RAAABZ0lEQVR4nK1SMUvDUBC+u0geGLTg"
        "Tyg66ergYBEs2NGlDm5VoUr8CS4O7g4ttCotODg4OYhtaIu0xUX/ggqCW0EQGqHVvJN7mJJiu4gf"
        "HO+SfF/u3XcH8J9Ip9O212gvSEg+ioPRh2q9NYmIBwDgMvO0ISC+M3MeAI5SycTHL2Gl1pwlIg8A"
        "4o7jsOM45pvv++z7PhLRaxAEK6lk4mkg9BrtOADcK6Vm3GzGvLu4vDI/3NxYN2e+WIZer/cGAItr"
        "q8vPVLu9kyvdKKVioeg4d2IEnU7H5AI3mwGllHCvK7XmFGmtS4g462YzE2GlIAigcHZuTomwunAQ"
        "cQ4RT4XMiDhk0k9vMA7CJyLaBoCXfLHMYU+WZQ1Ikkf6/GTmR631TtScB9u2Y/u7W9Yoc3KF0le/"
        "3xdzlsScoXFYluUxs4wDJMJxdLtduZpUSg2NI7oARHQIAHtaa6MctwAjEa5ctd6aH7dyf8Y3YwGk"
        "UuxgZKUAAAAASUVORK5CYII="
    ),
    "hidden": (
        "iVBORw0KGgoAAAANSUhEUgAAAA4AAAAOCAYAAAAfSC3RAAABKElEQVR4nO2RTStEcRTGf+d/U8rb"
        "N7CyGmQnaoaY0WA+AMXG5q6nJmV5y1K+wMXKS5miLOROc8fURWm+gewoygYLpTT36H+bhWiUvWd1"
        "Ouf3nNPTgX+1lVRqFxuqOtBsNpcL+am39iicVupdjuPsi8itUdUdIOc4zoHneaadyfM8YxkgC2wb"
        "EUkBD8D8WHq6Ua1f9X43BWHUN57J1oBCix2UIIz6ReRdVc+AYeAV2AIaSRaRUVV1gW7gCUgn/dbG"
        "TeAFqANVoPPb0UcRsZHugVQ+mykmmY7Le2uzuYl1YAkoiciCiNwAvh2r6pCqLqrqydHh7qr1JEbf"
        "9z9am++uL88t3KOqZWNMCRiZm5l8BlbiOI6/sD8VhFE+CCOb19ZF13U7fnvTn/QJmFppsbYRAfwA"
        "AAAASUVORK5CYII="
    ),
    "locked": (
        "iVBORw0KGgoAAAANSUhEUgAAAA4AAAAOCAYAAAAfSC3RAAAA50lEQVR4nOWSvWoCQRSFv7uzYCCN"
        "D5AUgoVtGpNqq0WWpF7yAilSuPokgq2voKQSdA0Lylbp7HwOX2C8Yc2om8QVUucUc+6Z4Zufy8AZ"
        "JUlSm72v6lyQlEOa5TfACOgAPrABulEYLH+C3qGI49gAb8AjsAZmQAuYplneqARfXnt3QBuYRGFw"
        "H4XBE9AHroHnStDtjogsAHVzc+fNSlBVfef22ACRfS0i5mxz0ix/AMYi4qnqDtg5oDBfVYsbDKIw"
        "GB7A/SkicqWqt1/rJ+n3XK9645/k/QPQLwZr7Ycx5te3Kstauy3nT6l4P0yoYK8fAAAAAElFTkSu"
        "QmCC"
    ),
    "unlocked": (
        "iVBORw0KGgoAAAANSUhEUgAAAA4AAAAOCAYAAAAfSC3RAAAAuElEQVR4nOWQPRLBYBCGn42MTs8o"
        "KZwiUsTgAGm0CpU7UDgBjUKtSKkQIxjJOVTGCdSsScYYmk8albfZ3Xfnmf2BH0vCKK6FUVx5Gd+I"
        "MIobwAqoZ4BIoqq+nWPa7AktgYqqusDEMhG+7xcBV0T2Hc/pLeZTD7gAbSPYHwzLQEFVz2kdBMEN"
        "OAFVIygiWV9E9M2+Z1aabXaJq6qHL7eOO54zWm+PJcuy7DzP+VC31bym0bgqBv0D+AAusS7mDWXk"
        "+gAAAABJRU5ErkJggg=="
    ),
    "tile": (
        "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAUElEQVR4nGP8//8/AyWAiSLdDAwM"
        "LCDCNWUpyc7YPSeaEcz4//8/g0vykv+XnvyHY2L4IH0gzESpF5goNYAR5IzRMPg/Ggb/KQsDcEKi"
        "BAAAoKS5Yicfdc8AAAAASUVORK5CYII="
    ),
    "object": (
        "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAACL0lEQVR4nI2STWgTQRTH38zO7s7G"
        "XVHTILbGNLGlCgYVBJUWIVYRWgoKeit6EAK9VgXFXKSF5OpJ7KESUOhBweJBPAgtWsWmYENsglqa"
        "nkSoFbxkd7JfMptmTdOo/cPA7Jt9v/eJXNcFrvH4RJtMadoyrWHbtqhnBACqKCVD1++kCslpaCFU"
        "B2SOP0rHujpH+wcTkqrWHvnT6soPeD71Qrcsc1+qkPzVDMD1i0CEa72J076zR0cA0YNtEImGLQAY"
        "aJUBqV9c15Wo4me+SVShGsY4mz42OelFFTBDCE1VGbvnAzYgPO4WgEAIXBg6L3Yfjoj8u8pc+m72"
        "w/XPxS/anxIQmi8vr9Ya0iDbASgvl6Ej3A6yDN7RdiI403+KmNXqFT8D3TDGXr+c6VvKl9RgKMjL"
        "B8d1ofx1BaLdMdgd9IL7UgIAjuOIPiBVSL4f6bobgm+wdijeowV4PxCCk70nYNeeTZVubeJ4fELj"
        "Xe5QIopABHN/uB1CexX4lwwdAGNsobEjDy8SIj45EA3bgR0BtL62LqmaKp0bTEDjSJud52YX7KV8"
        "8RmRKc0MXR4I8HlzMQaQm8tD9sFjqFQqLQEYY1OUpKfMMG4TZhg9nbGaMxfvct/Zo95ppfuZrF5l"
        "LHYrd/W7B4ONjduuUNPPGCFkM2N7znwnbNMS+C75AEmWp9/OLDj/g5gmwPybT0Ak8WOqkPxZt/Me"
        "3Cjli/ZibvGS4zh/HThCyJEpfWXo+s1G+288WMNK7HStMwAAAABJRU5ErkJggg=="
    ),
    "new": (
        "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAABsUlEQVR4nJ2TO2hUQRSGvzP3wk2y"
        "kagpJCwSLKNpRKPga7FZ8IFgZSFoqUJKWwtbO7UwIhYhiKQShBTZ2LlEUHzhNes2JoWJrkTYCPt2"
        "58i9q2Gzm8HHDzPM45zv/DPDCMDrd/kzInJHVXfQJhHB1B7ZoYGXK1v7cl98v7iG9Kxg+mfxt2eb"
        "1f7P/q/YifPnzg7RofDVDai89ai+GKa5NExDwdtWBj2AJqa93mAqBqhqV/Kzp7fg22nQGmAZTe9p"
        "QTPv+2gWd9NYukYi/fU3oDMfTz+C/mA0PbJhfR00l/dAUwaHDMUI7dqOrwiRXcblQNWHnqOEmVzX"
        "XphZgN6xBuIHTgc1mwRJxl66a3tgBipgVp0Ojhy/TsXubwXHVfNxa8kDCRbAZJ2ASGOpe1SCm4SZ"
        "RUhchMQFwieLFMqXFBs8AB47j7AOOXab78EMJXuSsj0Fg3Msl65oubRlBsg5n7Fdh1LjG+b3Jx/q"
        "h9WrhX3JkeofHWymqGC9XrfR2PyNAxfkvwHt8THAGLP8LwARKcxnZ6NPgkTd/PM3J4wxd621Ozer"
        "0pH8SVUvHz64N3oFfgJdMrJ60QZbOAAAAABJRU5ErkJggg=="
    ),
    "up": (
        "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAABy0lEQVR4nJ2Sz2sTQRTHv+/tNibN"
        "uqE2mjY2JTG0ZvwRf1UIghRqb5711KH/hjev/g9eCj1I59aDF0Es4g9Q6KEevEiNgl6EiMTY7CY7"
        "U7JZi5bdWPwyM4+Zee/DvDcPxhgkzdrKhjvqfjAZCRJSXQXwWUi1hBHihOAKmJ5eO1/Ksc2bQqrG"
        "kQFCqkkwPW9cKbtnq1O41Zh3yKInQqr6PwFCqgwxPbtYKxaKUyd5u9kG2SncXKg6YNoSUs0lAiq3"
        "H1jEtFku5efOlItjn77tIdAaO80fSGcydL1ezoHppZBqNhaQzlcfnsq7Ny6I2fSXloe+NsBgGOBd"
        "sw3XzXK9NnMighT+Agip7ueOj99ZuFzNghjjqd9cCte8OwaLGTPTE9Z8ZboAphdCqonQo7aysQpg"
        "DYAmhknZFi0tXuKv33109ny0f3o4V3LwdvtDr9P12OiQaQF4A2CZu63dRwAyALJGw/H8QOuh04H6"
        "AfDL87XROA3AifwXu63djv3x8T3/0E8gGOQfamh7gQ67DoD3fv1ud2QfDNQLhoEU1aAf7ePEcYcH"
        "L4iM3z+U0x+ykwBO2oJNNlKWDvshKt6RAOb1q50432NxhxQV57+1D3+DxOCdd6jJAAAAAElFTkSu"
        "QmCC"
    ),
    "down": (
        "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAB0klEQVR4nKVTy07bQBQ9c8kDqkKB"
        "8FCQkGg31Cukik3YIITEB7SqV7UqIf6gP8CPdFF5VbebfkBZgqqCSlWEkQIE1CBQCU6wYztx7BmU"
        "OCkQbFgw0mhGZ+49c8+5M0wIgceMRDcgKZoHIBkTP6SrcuVeAgBMeZ0D9RAIAUjwFqh+26x7jXD/"
        "EAECLvDn7yVs24Np17E4M4I4pRRJ0Ilm4dLw432iSIIgTBAIVz8Q4DElUBR4LTUswQvuau+MhKRo"
        "ywA+doBUksA5v6W54XP0ppMJ2/HKkqJ14F8AFljzHUjvv6w+63/yITc7/ZQSBMv1YTo+HLcBs1rD"
        "6EAK40NpEGM4Kpb8fOGsCC5mdVW+aEnQP71dNS3n68/f+07d8+HUgvYlYRkly0PV9XF0chHkC2fn"
        "4GKumXzLA7d0sGIY1vrO7rHb30cgdt2F7HAvjEqV5w9Py+3k0/8uiRtiJUXrY8R+ZCcy0y+mJlL/"
        "KnWkiaPmuCJ/eHIJLnK6Ku/dNJF1/wVJ0TIgtj05OZYdH8v0lA0LB4WiCS7mdVXe7u7CHYI2yXMQ"
        "2xodGR48Lxk2uFjSVXkjqo2RBG2SVwDWALzRVfl7ZFDLZyFi58t3nwfuO2+eXQFOWwgSkYhFegAA"
        "AABJRU5ErkJggg=="
    ),
    "duplicate": (
        "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAnklEQVR4nGM8cuJcPQMDQwMDftBg"
        "Y2HUiE2C8ciJc//Tk2Px6p45dzGDjYURIzY5Fhhj1botWDWHBfngNZyFWIUEDVhFpgsYsYUByLD3"
        "798zEBOwLNhcANJMRMCCYq4RaxiAQh3dUGSArJYFlwvQFeICLNRwQQPUP3gV4nSBDSSJoiRTUMyQ"
        "4gIGXICkMMAG0F2AzUCauQBnwCKrAbEBj5BMfJeYRTwAAAAASUVORK5CYII="
    ),
    "delete": (
        "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAB8klEQVR4nH1TTWsaURQ98xxHBb8i"
        "otSFi26Lf6DQdtMPaGwmWUxhuumi6+66KmnaJpCSH1FKKY2kFqLUFNpmE0J2buvOunChIqLiByio"
        "5bw6w2hNDry57z3uPffec98ocCCTza8BuAmggNW4A6BqGnrFulAcwQy8iEQiCIVCePbUXIj8mvuO"
        "Xq+HcrnMY9o09BObIJPN39Y07Wzn5Qscf/uBarWKTqcDnrff7CORSIDEXq9XkhWLRZrrrETME8hg"
        "YuvRA0SjUfh8Prx6+w5CCJm53+9jMBhgM30fqVSKrn/4UVc1+uTxprS5wk+MRiOMx2O43W5J9vno"
        "GB6Px/ZVcQWYzYn3HzOYTCaykmWC8w+fjm4Fg0FMp1MYW+u2Q/7kF+r1OjRNk5W0223ZDoA9fsTc"
        "b5fqctHh8EvOJtDX70k9KGqtVpMVEKah7yyPcUbxWq0WZrOZrITY3922yV7vHSCZTKJSqZBA+U+D"
        "eDwuV6PRQDgcllkZxNa4p6WQToiFgxBklvPm3Kk29wzkFKxJXEpgQVEUbDy8C7/fL/eqqkq7CmLl"
        "7RWgPk4saECFOTY+HidcLhdisZispNvtXkqQLpVKhUAgIJ0J9m69/+FwiGazab0B/pX/2l0uMZPN"
        "bwB4DuAGgGvz6xqA3wAOTEM/dfr/BSvBuRVbNI8HAAAAAElFTkSuQmCC"
    ),
    "merge": (
        "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAACN0lEQVR4nJ1SX0hTURz+zr03WQNr"
        "U1wqbRD0tl4SrDcjMCTzT2Xki/2buPUPMqjAtx4iCKKEoqSHghWBVuoqjTJJgtIRU8RCfEtIuG0M"
        "oWK57Z574nfyzt2tpz44nO/8zu/7znfuPUwIgUIcH3Mumtz05dcUhSWWF83qSFfayK9rRWoAwhTV"
        "0xe22mo7b34r2+hdoX67QTQ2t8Q59wghcmYMTM7dM+XgIotbNT+opobck78/RGf+9jBmqKqa0Djn"
        "FcFAhy1J7Q2lKBVjCkKdR2yH37v/qEIRQqzDf4JSa9ai66Efse8Ryd3OzasNppzfzj3G7U/HUHN9"
        "Xq63ldcjfOKr5Ipl0FTWC/f6KhzdcQVvTjoQnr0IE1zuLZijGA5k0bN7UPa0uO/kUigWad23B+e2"
        "TGJifgA9I7uwifnhUfxyzwEXnsSuoW/qFEJV73Bof2OxAaGlsR4B73Pouo7+L5dRqwRRp13CBsOL"
        "vqnTCFZO4PDBJuRDscjQi9c5kwedC6jk23E3GsTyr4QUn/V9RHtbs+wZGHyZM9DS6bQk8XgcT4dH"
        "oaoqDjQ34GrHEPrf9yL8+TzGz5RiHA14FnkFzjmSyaTUkFaznnLBP5Zor+u2rdta99rWhmGARUbG"
        "hMfjWYukachkMlCUtc9DnAYJrB7ilJqusKLruoNEjLFcs8VpppSmadpGSUkJstnsT3pI8jFRgUAC"
        "GoWwzOh0MiAIIUo1p9O5lEqlfEKIYtU/sHo1QalcLtf0H9g55S8yhL+RAAAAAElFTkSuQmCC"
    ),
    "lock_button": (
        "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAsElEQVR4nGNgoBAwogvs3HvY5P//"
        "/60MDAzWUKHjjIyMde7OtscJGrBz72HD////H2VgYOBEU/eLgYHB3sPF7gS6AUxo/Fqo5q2MjIwy"
        "DAwMsgwMDNsYGBjYGBgYmrC5gAWZ8///fwsoM8Pd2fYpiLFr35GMf//+PWJgYLDEZgATGl8SRHi4"
        "2D2BCbg52TyGMnmIMYBkwAhj7Nhz6D8xGjxc7Bip6gKmUQMYBj4MKAYAtIsoTeTTtMoAAAAASUVO"
        "RK5CYII="
    ),
    "unlock_button": (
        "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAm0lEQVR4nGNgGGjASKzCnXsPp/3/"
        "/z+fgYFBkoGB4SEjI2Ovu7PtEqIM2LHnkB8DA8NGLFI+TEQ6oBBKNzAwMEgxMjJOhPLLiDVAB0Qw"
        "MzP3e7jYPWdkZGyGimsTa4AIiHB1tP4Eot2cbN5CxYWJNQAnYIQxduw59J8YDR4udmA9O/ceFgTR"
        "LOTa7O5s+x5EMzFQCJhGDWCgOAwAcv4dY7Y0tmIAAAAASUVORK5CYII="
    ),
    "show_others": (
        "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAABmklEQVR4nM2Sy0tCQRjFzzf37qIo"
        "/QcuRETrHlALa2FhyyCpaFELe2Bgf0tWoL0gQiIkIojoZUXRoqJoGxXdpQuVpJV4Z74YSTF7Leu3"
        "PMx3Zs75BvhrqFLw+/1GYDLUD2AcQDMAh5nvACwsR2c34/G4/NZg7+isHsAagPYvbyO6ZOYhn9fz"
        "/MlgP3HeAuAQQK3b7cbIsP/D8GosjnQ6rU1elVJdPq/ntmSwnzhvAHBtmmZNKBgoaLGNLWSzWUgp"
        "4XK5MDzQVzCamV9kKWVWKdXa2935KA5PLmoAbAshqsuHU6kUguOjYGYkk8mCppmeGiN9Vgixs3tw"
        "WiuUUsvM3GRZlig+N5PJwHEczEVWkM/nS1oRy7IMZm40DCMqWF/xDblcDr8hDMMICCEebNsuiTpz"
        "JbrYIrZtSyK6l1JOlJd4Y5pmVSgYKETRmYvPrijRkVLqTbTpEqlijQcA6n5aI4A0M/d8WCPe0R+J"
        "iNaZufWrvER0xcyD5R+p1LzG5/U8LUXC7UQ0SETHRPRCRDpHAsDAUiTcUT78P3gDmie9WYVYSQsA"
        "AAAASUVORK5CYII="
    ),
}


def tiled_pixmap(name: str) -> QPixmap:
    """按语义名取一张 14/16 px 图标；未知名字返回空 QPixmap。"""
    value = _DATA.get(name)
    pix = QPixmap()
    if value:
        pix.loadFromData(QByteArray(base64.b64decode(value)))
    return pix


def tiled_icon(name: str) -> QIcon:
    return QIcon(tiled_pixmap(name))


ICON_NAMES = tuple(_DATA)
