"""Delete mutator for safe removal and unlinking of objects from Blender scenes."""

from typing import Any, Dict
import bpy

from adapter.readers.object_reader import ObjectNotFoundError
from adapter.mutators.undo_manager import push_undo_step


class DeleteMutator:
    """Safely unlinks and removes objects from the scene with atomic undo tracking."""

    @classmethod
    def delete(cls, name: str) -> Dict[str, Any]:
        """Delete an object by exact name.

        Args:
            name: Exact name of object in bpy.data.objects.

        Returns:
            Dict[str, Any]: Confirmation of deletion and last known state.

        Raises:
            ObjectNotFoundError: If target object does not exist.
            ValueError: If name is invalid.
        """
        if not name or not isinstance(name, str) or not name.strip():
            raise ValueError("Object 'name' must be a non-empty string.")

        target_name = name.strip()
        obj = bpy.data.objects.get(target_name)
        if obj is None:
            raise ObjectNotFoundError(f"Cannot delete: Object '{target_name}' not found in scene.")

        obj_type = obj.type
        previous_state = {
            "location": [round(float(v), 4) for v in obj.location],
            "rotation": [round(float(v), 4) for v in obj.rotation_euler],
            "scale": [round(float(v), 4) for v in obj.scale],
        }

        # Remove datablock and unlink from all collections
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.context.view_layer.update()

        # Record atomic undo step
        push_undo_step(f"AI: Delete ({target_name})")

        return {
            "deleted": True,
            "object_name": target_name,
            "type": obj_type,
            "previous_state": previous_state,
        }
