"""Transform mutator for manipulating object position, rotation, and scale."""

from typing import Any, Dict, List, Optional, Sequence
import bpy
from mathutils import Euler, Vector

from adapter.readers.object_reader import ObjectNotFoundError
from adapter.mutators.undo_manager import push_undo_step


class TransformMutator:
    """Modifies spatial transform properties on Blender objects with atomic undo."""

    @classmethod
    def transform(
        cls,
        name: str,
        location: Optional[Sequence[float]] = None,
        rotation: Optional[Sequence[float]] = None,
        scale: Optional[Sequence[float]] = None,
        relative: bool = False,
    ) -> Dict[str, Any]:
        """Apply transform updates to an object by name.

        Args:
            name: Target object name in bpy.data.objects.
            location: Optional [X, Y, Z] world location or offset.
            rotation: Optional [rx, ry, rz] Euler angles in radians or delta.
            scale: Optional [sx, sy, sz] scaling factor or multiplier.
            relative: If True, applies transforms relative to current values. Default False.

        Returns:
            Dict[str, Any]: Detailed before/after diff and list of changed fields.

        Raises:
            ObjectNotFoundError: If target object is missing.
            ValueError: If no transform fields provided or values invalid.
        """
        if not name or not isinstance(name, str):
            raise ValueError("Object 'name' must be a non-empty string.")

        obj = bpy.data.objects.get(name)
        if obj is None:
            raise ObjectNotFoundError(f"Object '{name}' not found in scene.")

        if location is None and rotation is None and scale is None:
            raise ValueError("At least one transform field ('location', 'rotation', 'scale') must be provided.")

        changed_fields: List[str] = []

        before = {
            "location": [round(float(v), 4) for v in obj.location],
            "rotation": [round(float(v), 4) for v in obj.rotation_euler],
            "scale": [round(float(v), 4) for v in obj.scale],
        }

        # 1. Location
        if location is not None:
            if len(location) != 3:
                raise ValueError(f"Argument 'location' must contain exactly 3 numbers, got {len(location)}")
            vec = Vector((float(location[0]), float(location[1]), float(location[2])))
            if relative:
                obj.location += vec
            else:
                obj.location = vec
            changed_fields.append("location")

        # 2. Rotation (Euler 'XYZ')
        if rotation is not None:
            if len(rotation) != 3:
                raise ValueError(f"Argument 'rotation' must contain exactly 3 numbers, got {len(rotation)}")
            if relative:
                obj.rotation_euler.x += float(rotation[0])
                obj.rotation_euler.y += float(rotation[1])
                obj.rotation_euler.z += float(rotation[2])
            else:
                obj.rotation_euler = Euler((float(rotation[0]), float(rotation[1]), float(rotation[2])), "XYZ")
            changed_fields.append("rotation")

        # 3. Scale
        if scale is not None:
            if len(scale) != 3:
                raise ValueError(f"Argument 'scale' must contain exactly 3 numbers, got {len(scale)}")
            if relative:
                obj.scale.x *= float(scale[0])
                obj.scale.y *= float(scale[1])
                obj.scale.z *= float(scale[2])
            else:
                obj.scale = Vector((float(scale[0]), float(scale[1]), float(scale[2])))
            changed_fields.append("scale")

        # Update view layer and depsgraph
        bpy.context.view_layer.update()

        after = {
            "location": [round(float(v), 4) for v in obj.location],
            "rotation": [round(float(v), 4) for v in obj.rotation_euler],
            "scale": [round(float(v), 4) for v in obj.scale],
        }

        # Record atomic undo step
        push_undo_step(f"AI: Transform ({name})")

        return {
            "object_name": name,
            "relative": bool(relative),
            "before": before,
            "after": after,
            "changed": changed_fields,
        }
