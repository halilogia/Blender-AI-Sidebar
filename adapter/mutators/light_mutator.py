"""Light mutator for creating and modifying scene lights via Blender Data API & RNA."""

from typing import Any, Dict, Optional, Sequence
import bpy
from mathutils import Euler, Vector

from adapter.mutators.undo_manager import push_undo_step


class LightMutator:
    """Creates and modifies light objects directly via Data API without operator dependencies."""

    VALID_LIGHT_TYPES = {"POINT", "SUN", "SPOT", "AREA"}

    @classmethod
    def create_or_modify(
        cls,
        name: Optional[str] = None,
        light_type: Optional[str] = None,
        location: Optional[Sequence[float]] = None,
        rotation: Optional[Sequence[float]] = None,
        energy: Optional[float] = None,
        color: Optional[Sequence[float]] = None,
    ) -> Dict[str, Any]:
        """Create a new light or modify an existing light in the scene.

        Args:
            name: Optional light name. If exists as a light, modifies it; if exists as non-light, raises ValueError.
            light_type: Optional light type ('POINT', 'SUN', 'SPOT', 'AREA'). Default for new is 'POINT'.
            location: Optional [X, Y, Z] world coordinates.
            rotation: Optional [rx, ry, rz] Euler angles in radians.
            energy: Optional light power / energy (>= 0). Default for new is 10.0.
            color: Optional [R, G, B] color values clamped to [0.0, 1.0]. Default for new is [1.0, 1.0, 1.0].

        Returns:
            Dict[str, Any]: Structured creation/modification result snapshot.
        """
        # 1. Validate light_type
        if light_type is not None:
            normalized_type = str(light_type).strip().upper()
            if normalized_type not in cls.VALID_LIGHT_TYPES:
                raise ValueError(
                    f"Invalid light_type '{light_type}'. Must be one of {sorted(cls.VALID_LIGHT_TYPES)}."
                )
            effective_type = normalized_type
        else:
            effective_type = None

        # 2. Validate energy
        if energy is not None:
            try:
                effective_energy = float(energy)
            except (ValueError, TypeError):
                raise ValueError(f"Light energy must be a valid number, got {energy!r}.")
            if effective_energy < 0.0:
                raise ValueError(f"Light energy must be non-negative, got {effective_energy}.")
        else:
            effective_energy = 10.0

        # 3. Validate color
        if color is not None:
            if not isinstance(color, (list, tuple)) or len(color) != 3:
                raise ValueError(f"Light color must be a sequence of 3 numbers [R, G, B], got {color!r}.")
            try:
                effective_color = [max(0.0, min(1.0, float(c))) for c in color]
            except (ValueError, TypeError):
                raise ValueError(f"Light color values must be numeric, got {color!r}.")
        else:
            effective_color = [1.0, 1.0, 1.0]

        # 4. Validate location
        if location is not None:
            if not isinstance(location, (list, tuple)) or len(location) != 3:
                raise ValueError(f"Light location must be a sequence of 3 numbers [X, Y, Z], got {location!r}.")
            try:
                loc_vec = Vector((float(location[0]), float(location[1]), float(location[2])))
            except (ValueError, TypeError):
                raise ValueError(f"Light location coordinates must be numeric, got {location!r}.")
        else:
            loc_vec = None

        # 5. Validate rotation
        if rotation is not None:
            if not isinstance(rotation, (list, tuple)) or len(rotation) != 3:
                raise ValueError(f"Light rotation must be a sequence of 3 numbers [rx, ry, rz], got {rotation!r}.")
            try:
                rot_euler = Euler((float(rotation[0]), float(rotation[1]), float(rotation[2])), "XYZ")
            except (ValueError, TypeError):
                raise ValueError(f"Light rotation angles must be numeric, got {rotation!r}.")
        else:
            rot_euler = None

        base_name = name.strip() if name and isinstance(name, str) and name.strip() else None

        # 6. Object lookup or creation
        existing_obj = bpy.data.objects.get(base_name) if base_name else None
        if existing_obj is not None:
            if existing_obj.type != "LIGHT":
                raise ValueError(
                    f"Object '{base_name}' already exists but is of type '{existing_obj.type}', not 'LIGHT'."
                )
            obj = existing_obj
            light_data = obj.data
            is_new = False
        else:
            light_name = base_name or "Light"
            initial_type = effective_type or "POINT"
            light_data = bpy.data.lights.new(name=f"{light_name}_data", type=initial_type)
            obj = bpy.data.objects.new(name=light_name, object_data=light_data)
            target_collection = bpy.context.collection or bpy.context.scene.collection
            target_collection.objects.link(obj)
            is_new = True

        # 7. Apply properties
        if effective_type is not None:
            light_data.type = effective_type

        if energy is not None or is_new:
            light_data.energy = effective_energy

        if color is not None or is_new:
            light_data.color = (effective_color[0], effective_color[1], effective_color[2])

        if loc_vec is not None:
            obj.location = loc_vec
        elif is_new:
            obj.location = Vector((0.0, 0.0, 0.0))

        if rot_euler is not None:
            obj.rotation_euler = rot_euler
        elif is_new:
            obj.rotation_euler = Euler((0.0, 0.0, 0.0), "XYZ")

        # 8. Update view layer
        if hasattr(bpy.context, "view_layer") and bpy.context.view_layer:
            bpy.context.view_layer.update()

        # 9. Atomic undo point
        action_label = f"AI: {'Create' if is_new else 'Modify'} Light ({obj.name})"
        push_undo_step(action_label)

        actual_obj = bpy.data.objects.get(obj.name)
        exists_in_scene = actual_obj is not None

        return {
            "created": is_new,
            "exists": exists_in_scene,
            "object_name": obj.name,
            "type": "LIGHT",
            "light_type": str(light_data.type),
            "location": [round(float(v), 4) for v in obj.location],
            "rotation": [round(float(v), 4) for v in obj.rotation_euler],
            "energy": round(float(light_data.energy), 2),
            "color": [round(float(c), 4) for c in light_data.color],
        }
