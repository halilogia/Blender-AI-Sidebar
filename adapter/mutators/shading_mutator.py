"""Shading mutator for setting polygon shading (SMOOTH/FLAT) via Blender Data API."""

from typing import Any, Dict
import bpy

from adapter.mutators.undo_manager import push_undo_step


class ShadingMutator:
    """Modifies mesh polygon shading directly via Data API without operator dependencies."""

    SUPPORTED_SHADING = {"SMOOTH", "FLAT"}

    @classmethod
    def set_shading(cls, name: str, shading: str) -> Dict[str, Any]:
        """Set smooth or flat shading on all polygons of a mesh object.

        Args:
            name: Name of the target mesh object.
            shading: Shading mode, either 'SMOOTH' or 'FLAT' (case-insensitive).

        Returns:
            Dict[str, Any]: Structured execution snapshot.
        """
        if not name or not isinstance(name, str) or not name.strip():
            raise ValueError("Target object name must be a non-empty string.")

        target_name = name.strip()
        obj = bpy.data.objects.get(target_name)
        if obj is None:
            raise ValueError(f"Object '{target_name}' not found in scene.")

        if obj.type != "MESH":
            raise ValueError(f"Object '{target_name}' is of type '{obj.type}'. Only MESH objects support polygon shading.")

        if not shading or not isinstance(shading, str):
            raise ValueError(f"Shading must be one of {sorted(cls.SUPPORTED_SHADING)}.")

        shading_upper = shading.strip().upper()
        if shading_upper not in cls.SUPPORTED_SHADING:
            raise ValueError(f"Invalid shading '{shading}'. Must be one of {sorted(cls.SUPPORTED_SHADING)}.")

        mesh = obj.data
        is_smooth = (shading_upper == "SMOOTH")

        # Apply to all polygons via Data API
        for poly in mesh.polygons:
            poly.use_smooth = is_smooth

        mesh.update()
        if hasattr(bpy.context, "view_layer") and bpy.context.view_layer:
            bpy.context.view_layer.update()

        # Atomic undo point
        push_undo_step(f"AI: Set Shading ({shading_upper}) on {obj.name}")

        return {
            "exists": True,
            "object_name": obj.name,
            "type": "MESH",
            "shading": shading_upper,
            "polygon_count": len(mesh.polygons),
        }
