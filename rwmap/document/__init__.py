"""文档模型层：不依赖 Qt，探针可独立运行。"""
from .map_document import MapDocument
from .layer import Layer, TileLayer, ObjectLayer
from .tileset import Tileset
from .map_object import MapObject
from .properties import PropertyBag
from .gid import split_gid, make_gid, GID_MASK
from .version_profile import VersionProfile

__all__ = [
    "MapDocument", "Layer", "TileLayer", "ObjectLayer", "Tileset", "MapObject",
    "PropertyBag", "split_gid", "make_gid", "GID_MASK", "VersionProfile",
]
