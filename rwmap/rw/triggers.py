"""Triggers 语义解释（首版桩：只做只读展示，不写入推测值）。"""
from __future__ import annotations


def find_map_info(doc):
    for layer in doc.layers:
        if layer.kind != "object":
            continue
        for obj in layer.objects:
            if obj.name == "map_info":
                return obj
    return None


def describe_map_info(doc) -> dict:
    obj = find_map_info(doc)
    if obj is None:
        return {}
    return dict(obj.properties.items())


def list_trigger_names(doc) -> list[str]:
    out = []
    for layer in doc.layers:
        if layer.kind == "object":
            out.extend(obj.label for obj in layer.objects)
    return out
