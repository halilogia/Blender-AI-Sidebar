"""Camera mutator for creating and modifying scene cameras via Blender Data API & RNA."""

from typing import Any, Dict, Optional, Sequence
import bpy
from mathutils import Euler, Vector

from adapter.mutators.undo_manager import push_undo_step


class CameraMutator:
    """Creates and modifies camera objects directly via Data API without operator dependencies."""

    @classmethod
    def create_or_modify(
        cls,
        name: Optional[str] = None,
        location: Optional[Sequence[float]] = None,
        rotation: Optional[Sequence[float]] = None,
        lens: Optional[float] = None,
        make_active: bool = True,
    ) -> Dict[str, Any]:
        """Create a new camera or modify an existing camera in the scene.

        Args:
            name: Optional camera name. If exists as a camera, modifies it; if exists as non-camera, raises ValueError.
            location: Optional [X, Y, Z] world coordinates.
            rotation: Optional [rx, ry, rz] Euler angles in radians.
            lens: Optional focal length in mm. Must be > 0. Default for new camera is 50.0.
            make_active: Whether to set as active scene camera. Default is True.

        Returns:
            Dict[str, Any]: Structured creation/modification result snapshot.
        """
        # Validate inputs
        if lens is not None:
            effective_lens = float(lens)
            if effective_lens <= 0:
                raise ValueError(f"Camera lens (focal length) must be strictly positive, got {effective_lens}.")
        else:
            effective_lens = 50.0

        if location is not None:
            if not isinstance(location, (list, tuple)) or len(location) != 3:
                raise ValueError(f"Camera location must be a sequence of 3 numbers [X, Y, Z], got {location!r}.")
            loc_vec = Vector((float(location[0]), float(location[1]), float(location[2])))
        else:
            loc_vec = None

        if rotation is not None:
            if not isinstance(rotation, (list, tuple)) or len(rotation) != 3:
                raise ValueError(f"Camera rotation must be a sequence of 3 numbers [rx, ry, rz], got {rotation!r}.")
            rot_euler = Euler((float(rotation[0]), float(rotation[1]), float(rotation[2])), "XYZ")
        else:
            rot_euler = None

        base_name = name.strip() if name and isinstance(name, str) and name.strip() else None

        existing_obj = bpy.data.objects.get(base_name) if base_name else None
        if existing_obj is not None:
            if existing_obj.type != "CAMERA":
                raise ValueError(
                    f"Object '{base_name}' already exists but is of type '{existing_obj.type}', not 'CAMERA'."
                )
            obj = existing_obj
            cam_data = obj.data
            is_new = False
        else:
            cam_name = base_name or "Camera"
            cam_data = bpy.data.cameras.new(name=f"{cam_name}_data")
            obj = bpy.data.objects.new(name=cam_name, object_data=cam_data)
            target_collection = bpy.context.collection or bpy.context.scene.collection
            target_collection.objects.link(obj)
            is_new = True

        # Apply lens
        if lens is not None or is_new:
            cam_data.lens = effective_lens

        # Apply transforms
        if loc_vec is not None:
            obj.location = loc_vec
        elif is_new:
            obj.location = Vector((0.0, 0.0, 0.0))

        if rot_euler is not None:
            obj.rotation_euler = rot_euler
        elif is_new:
            obj.rotation_euler = Euler((0.0, 0.0, 0.0), "XYZ")

        # Set active camera
        if make_active:
            if hasattr(bpy.context, "scene") and bpy.context.scene:
                bpy.context.scene.camera = obj

        # Update depsgraph / view layer
        if hasattr(bpy.context, "view_layer") and bpy.context.view_layer:
            bpy.context.view_layer.update()

        # Atomic undo point
        action_label = f"AI: {'Create' if is_new else 'Modify'} Camera ({obj.name})"
        push_undo_step(action_label)

        actual_obj = bpy.data.objects.get(obj.name)
        exists_in_scene = actual_obj is not None
        is_active = (bpy.context.scene.camera == obj) if hasattr(bpy.context, "scene") and bpy.context.scene else False

        return {
            "created": is_new,
            "exists": exists_in_scene,
            "object_name": obj.name,
            "type": "CAMERA",
            "location": [round(float(v), 4) for v in obj.location],
            "rotation": [round(float(v), 4) for v in obj.rotation_euler],
            "lens": round(float(cam_data.lens), 2),
            "is_active_camera": is_active,
        }
