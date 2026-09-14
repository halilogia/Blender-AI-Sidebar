"""Duplicate mutator for safely cloning objects and datablocks via Blender Data API."""

from typing import Any, Dict, Optional, Sequence
import bpy
from mathutils import Euler, Vector

from adapter.readers.object_reader import ObjectNotFoundError
from adapter.mutators.undo_manager import push_undo_step


class DuplicateMutator:
    """Clones Blender objects directly via Data API without operator dependencies."""

    @classmethod
    def duplicate(
        cls,
        source_name: str,
        new_name: Optional[str] = None,
        location: Optional[Sequence[float]] = None,
        rotation: Optional[Sequence[float]] = None,
        scale: Optional[Sequence[float]] = None,
    ) -> Dict[str, Any]:
        """Duplicate an existing object with independent data block and optional transforms.

        Args:
            source_name: Name of the existing source object in bpy.data.objects.
            new_name: Optional custom name for the duplicated object. If provided and exists,
                      raises ValueError to prevent silent suffixing. If omitted, generates
                      a deterministic name '{source_name}_copy_{n}'.
            location: Optional [X, Y, Z] world position coordinates. Defaults to source location.
            rotation: Optional [rx, ry, rz] Euler angles in radians. Defaults to source rotation.
            scale: Optional [sx, sy, sz] scale factors. Defaults to source scale.

        Returns:
            Dict[str, Any]: Structured snapshot of duplication result for verification.

        Raises:
            ObjectNotFoundError: If source_name does not exist in bpy.data.objects.
            ValueError: If new_name collides with an existing object or if transforms are invalid.
        """
        if not source_name or not isinstance(source_name, str) or not source_name.strip():
            raise ValueError("Argument 'source_name' must be a non-empty string.")

        src_name = source_name.strip()
        source_obj = bpy.data.objects.get(src_name)
        if source_obj is None:
            raise ObjectNotFoundError(f"Source object '{src_name}' not found in scene.")

        # Capture source state before duplication for verification
        source_before = {
            "location": [round(float(v), 4) for v in source_obj.location],
            "rotation": [round(float(v), 4) for v in source_obj.rotation_euler],
            "scale": [round(float(v), 4) for v in source_obj.scale],
            "type": source_obj.type,
        }

        # Resolve target object name deterministically
        if new_name is not None and isinstance(new_name, str) and new_name.strip():
            target_name = new_name.strip()
            if target_name in bpy.data.objects:
                raise ValueError(f"Object with name '{target_name}' already exists in scene.")
        else:
            idx = 1
            while f"{src_name}_copy_{idx}" in bpy.data.objects:
                idx += 1
            target_name = f"{src_name}_copy_{idx}"

        # Validate transform inputs if provided
        loc_vec: Optional[Vector] = None
        if location is not None:
            if not isinstance(location, (list, tuple)) or len(location) != 3:
                raise ValueError(f"Argument 'location' must contain exactly 3 numbers, got {location!r}.")
            loc_vec = Vector((float(location[0]), float(location[1]), float(location[2])))

        rot_euler: Optional[Euler] = None
        if rotation is not None:
            if not isinstance(rotation, (list, tuple)) or len(rotation) != 3:
                raise ValueError(f"Argument 'rotation' must contain exactly 3 numbers, got {rotation!r}.")
            rot_euler = Euler((float(rotation[0]), float(rotation[1]), float(rotation[2])), "XYZ")

        scale_vec: Optional[Vector] = None
        if scale is not None:
            if not isinstance(scale, (list, tuple)) or len(scale) != 3:
                raise ValueError(f"Argument 'scale' must contain exactly 3 numbers, got {scale!r}.")
            scale_vec = Vector((float(scale[0]), float(scale[1]), float(scale[2])))

        # 1. Duplicate object datablock via Data API
        new_obj = source_obj.copy()
        new_obj.name = target_name

        # 2. Duplicate data datablock (e.g. mesh, camera, light) for true independent instances
        # while preserving material slot assignments and references
        if source_obj.data is not None:
            new_obj.data = source_obj.data.copy()
            new_obj.data.name = f"{target_name}_data"

        # 3. Link new object into the source object's collection or scene active collection
        if source_obj.users_collection:
            target_collection = source_obj.users_collection[0]
        else:
            target_collection = bpy.context.collection or bpy.context.scene.collection
        target_collection.objects.link(new_obj)

        # 4. Apply transforms if explicitly specified
        if loc_vec is not None:
            new_obj.location = loc_vec
        if rot_euler is not None:
            new_obj.rotation_euler = rot_euler
        if scale_vec is not None:
            new_obj.scale = scale_vec

        # 5. Ensure depsgraph & view layer are updated
        if hasattr(bpy.context, "view_layer") and bpy.context.view_layer:
            bpy.context.view_layer.update()

        # 6. Record atomic undo step
        push_undo_step(f"AI: Duplicate {src_name} -> {new_obj.name}")

        # 7. Collect material names for verification
        materials = []
        if hasattr(new_obj, "material_slots"):
            materials = [slot.material.name for slot in new_obj.material_slots if slot.material]

        # 8. Check source object preservation
        source_preserved = (
            bpy.data.objects.get(src_name) is not None
            and [round(float(v), 4) for v in source_obj.location] == source_before["location"]
            and [round(float(v), 4) for v in source_obj.rotation_euler] == source_before["rotation"]
            and [round(float(v), 4) for v in source_obj.scale] == source_before["scale"]
        )

        return {
            "duplicated": True,
            "source_name": src_name,
            "new_name": new_obj.name,
            "source_exists": bpy.data.objects.get(src_name) is not None,
            "new_exists": bpy.data.objects.get(new_obj.name) is not None,
            "type": new_obj.type,
            "location": [round(float(v), 4) for v in new_obj.location],
            "rotation": [round(float(v), 4) for v in new_obj.rotation_euler],
            "scale": [round(float(v), 4) for v in new_obj.scale],
            "source_location": [round(float(v), 4) for v in source_obj.location],
            "source_rotation": [round(float(v), 4) for v in source_obj.rotation_euler],
            "source_scale": [round(float(v), 4) for v in source_obj.scale],
            "materials": materials,
            "distinct_identity": bool(new_obj != source_obj),
            "distinct_data": bool(new_obj.data != source_obj.data) if (new_obj.data and source_obj.data) else True,
            "source_preserved": source_preserved,
            "before": source_before,
        }
