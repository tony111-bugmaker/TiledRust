"""版本与模组适配层。"""
from .version import load_profile, list_profiles, active_profile
from .layers import classify, LayerRule, known_layer_names
from .properties import normalize, is_known, aliases
from .units import UnitEntry, collect_units, load_mod_units
from .mod_reader import list_mods, read_mod_info, iter_mod_ini
from .thumbnail import make_thumbnail
from .exporter import export_map
from .rwmod_pack import pack_rwmod

__all__ = [
    "load_profile", "list_profiles", "active_profile", "classify", "LayerRule",
    "known_layer_names", "normalize", "is_known", "aliases", "UnitEntry",
    "collect_units", "load_mod_units", "list_mods", "read_mod_info", "iter_mod_ini",
    "make_thumbnail", "export_map", "pack_rwmod",
]
