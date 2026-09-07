"""Object reader for deep object datablock inspection."""

import math
from typing import Any, Dict, List, Optional
import bpy


class ObjectNotFoundError(Exception):
    """Raised when an object cannot be found in bpy.data.objects."""


def sanitize_float(val: float, precision: int = 4) -> float:
    """Sanitize float against NaN / Inf and round to specified precision."""
    if math.isnan(val) or math.isinf(val):
        return 0.0
    return round(float(val), precision)


def sanitize_vector(vec, precision: int = 4) -> List[float]:
    """Convert an iterable of numbers to a sanitized float list."""
    return [sanitize_float(v, precision) for v in vec]


def euler_to_degrees(euler, precision: int = 2) -> List[float]:
    """Convert Euler angles (radians) to degrees and sanitize."""
    return [sanitize_float(math.degrees(angle), precision) for angle in euler]


class ObjectReader:
    """Extracts a deep, deterministic inspection of a single Blender object."""

    @staticmethod
    def read(name: str, include_evaluated: bool = False) -> Dict[str, Any]:
        """Read and serialize deep properties of the target object.

        Args:
            name: The name of the object in bpy.data.objects.
            include_evaluated: Whether to pull depsgraph-evaluated data (False in M1).

        Returns:
            Dict conforming to the inspect_object grounding schema.

        Raises:
            ObjectNotFoundError: If the object does not exist.
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ObjectNotFoundError(f"Object '{name}' was not found in Blender datablocks.")

        is_linked = obj.library is not None
        library_name = obj.library.name if obj.library else None
        parent_name = obj.parent.name if obj.parent else None

        # Collections this object is linked into
        collections: List[str] = sorted([c.name for c in obj.users_collection])

        # Transform data
        location = sanitize_vector(obj.location, 4)
        rotation_euler_deg = euler_to_degrees(obj.rotation_euler, 2)
        scale = sanitize_vector(obj.scale, 4)
        dimensions = sanitize_vector(obj.dimensions, 4)

        # Materials assigned to slots
        materials: List[str] = [
            slot.material.name for slot in obj.material_slots if slot.material is not None
        ]

        # Modifiers summary
        modifiers: List[Dict[str, Any]] = []
        for mod in obj.modifiers:
            mod_data: Dict[str, Any] = {
                "name": mod.name,
                "show_viewport": mod.show_viewport,
                "type": mod.type,
            }
            if hasattr(mod, "levels"):
                mod_data["viewport_levels"] = mod.levels
            if hasattr(mod, "render_levels"):
                mod_data["render_levels"] = mod.render_levels
            modifiers.append(mod_data)

        return {
            "collections": collections,
            "dimensions": dimensions,
            "evaluated": None,
            "is_linked": is_linked,
            "library_name": library_name,
            "materials": materials,
            "modifiers": modifiers,
            "name": obj.name,
            "parent": parent_name,
            "transform": {
                "location": location,
                "rotation_euler_deg": rotation_euler_deg,
                "scale": scale,
            },
            "type": obj.type,
        }
