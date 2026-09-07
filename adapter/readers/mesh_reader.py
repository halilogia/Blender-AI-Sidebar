"""Mesh reader for extracting geometry metrics, polygon breakdown, and world bounding box."""

import math
from typing import Any, Dict, List
import bpy
import mathutils

from adapter.readers.object_reader import ObjectNotFoundError


class InvalidMeshDataTypeError(Exception):
    """Raised when attempting to inspect a mesh on a non-mesh object."""


def sanitize_float(val: float, precision: int = 4) -> float:
    """Sanitize float against NaN / Inf and round to specified precision."""
    if math.isnan(val) or math.isinf(val):
        return 0.0
    return round(float(val), precision)


def calculate_world_bounding_box(obj: bpy.types.Object) -> Dict[str, List[float]]:
    """Compute the world-space axis-aligned bounding box from object's bound_box corners."""
    matrix = obj.matrix_world
    world_corners = [matrix @ mathutils.Vector(corner) for corner in obj.bound_box]

    xs = [c.x for c in world_corners]
    ys = [c.y for c in world_corners]
    zs = [c.z for c in world_corners]

    min_pt = [sanitize_float(min(xs), 4), sanitize_float(min(ys), 4), sanitize_float(min(zs), 4)]
    max_pt = [sanitize_float(max(xs), 4), sanitize_float(max(ys), 4), sanitize_float(max(zs), 4)]
    center_pt = [
        sanitize_float((min_pt[0] + max_pt[0]) / 2, 4),
        sanitize_float((min_pt[1] + max_pt[1]) / 2, 4),
        sanitize_float((min_pt[2] + max_pt[2]) / 2, 4),
    ]

    return {
        "center": center_pt,
        "max": max_pt,
        "min": min_pt,
    }


class MeshReader:
    """Extracts aggregate geometry metrics, topology breakdown, and world bounds."""

    @staticmethod
    def read(object_name: str) -> Dict[str, Any]:
        """Read and serialize mesh geometry metrics for the specified object.

        Args:
            object_name: Name of the object whose mesh to inspect.

        Returns:
            Dict conforming to the inspect_mesh grounding schema.

        Raises:
            ObjectNotFoundError: If object does not exist.
            InvalidMeshDataTypeError: If object is not a MESH datablock.
        """
        obj = bpy.data.objects.get(object_name)
        if not obj:
            raise ObjectNotFoundError(f"Object '{object_name}' was not found in Blender datablocks.")

        if obj.type != "MESH":
            raise InvalidMeshDataTypeError(
                f"Object '{object_name}' is not of type 'MESH' (actual type: '{obj.type}')."
            )

        mesh = obj.data
        if not mesh:
            raise InvalidMeshDataTypeError(f"Object '{object_name}' has no mesh datablock attached.")

        # Topology aggregate counts
        vertex_count = len(mesh.vertices)
        edge_count = len(mesh.edges)
        polygon_count = len(mesh.polygons)

        # Polygon breakdown: triangles, quads, ngons
        triangles = 0
        quads = 0
        ngons = 0

        for poly in mesh.polygons:
            v_len = len(poly.vertices)
            if v_len == 3:
                triangles += 1
            elif v_len == 4:
                quads += 1
            elif v_len > 4:
                ngons += 1

        # UV layers
        uv_layers = sorted([uv.name for uv in mesh.uv_layers])
        has_uv = len(uv_layers) > 0

        # World-space bounding box
        bounding_box = calculate_world_bounding_box(obj)

        return {
            "bounding_box": bounding_box,
            "counts": {
                "edges": edge_count,
                "polygons": polygon_count,
                "vertices": vertex_count,
            },
            "has_uv": has_uv,
            "mesh_name": mesh.name,
            "object_name": obj.name,
            "polygon_breakdown": {
                "ngons": ngons,
                "quads": quads,
                "triangles": triangles,
            },
            "uv_layers": uv_layers,
        }
