"""Primitive mutator for creating geometric meshes via Blender Data API & BMesh."""

from typing import Any, Dict, Optional, Sequence
import bmesh
import bpy
from mathutils import Euler, Vector

from adapter.mutators.undo_manager import push_undo_step


class InvalidPrimitiveTypeError(ValueError):
    """Raised when an unsupported primitive type is requested."""


class PrimitiveMutator:
    """Creates geometric primitives directly via Data API without operator context dependencies."""

    SUPPORTED_TYPES = {"CUBE", "SPHERE", "PLANE"}

    @classmethod
    def create(
        cls,
        primitive_type: str,
        name: Optional[str] = None,
        location: Optional[Sequence[float]] = None,
        rotation: Optional[Sequence[float]] = None,
        scale: Optional[Sequence[float]] = None,
        size: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Create a new geometric primitive object in the active collection.

        Args:
            primitive_type: One of 'CUBE', 'SPHERE', 'PLANE' (case-insensitive).
            name: Optional custom name. If not provided, defaults to primitive type title.
            location: Optional [X, Y, Z] world coordinates. Default is [0.0, 0.0, 0.0].
            rotation: Optional [rx, ry, rz] Euler angles in radians. Default is [0.0, 0.0, 0.0].
            scale: Optional [sx, sy, sz] scaling factors. Default is [1.0, 1.0, 1.0].
            size: Base dimension/extent in meters. Default is 2.0.

        Returns:
            Dict[str, Any]: Structured creation result including geometry metrics.
        """
        p_type = str(primitive_type).strip().upper()
        if p_type not in cls.SUPPORTED_TYPES:
            raise InvalidPrimitiveTypeError(
                f"Unsupported primitive type '{primitive_type}'. Supported types: {sorted(cls.SUPPORTED_TYPES)}"
            )

        effective_size = float(size) if size is not None else 2.0
        if effective_size <= 0:
            raise ValueError(f"Primitive size must be strictly positive, got {effective_size}")

        # Resolve unique datablock names
        base_name = name.strip() if name and name.strip() else p_type.capitalize()
        mesh = bpy.data.meshes.new(name=f"{base_name}_mesh")

        bm = bmesh.new()
        try:
            if p_type == "CUBE":
                bmesh.ops.create_cube(bm, size=effective_size)
            elif p_type == "SPHERE":
                # Sphere radius = size / 2.0
                radius = effective_size / 2.0
                bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=radius)
            elif p_type == "PLANE":
                # In bmesh create_grid, size is half-extent (radius), so size/2 produces extent=size
                bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=effective_size / 2.0)

            bm.to_mesh(mesh)
        finally:
            bm.free()

        # Link mesh into a new object and add to active collection
        obj = bpy.data.objects.new(name=base_name, object_data=mesh)
        target_collection = bpy.context.collection or bpy.context.scene.collection
        target_collection.objects.link(obj)

        # Set transforms
        if location:
            obj.location = Vector((float(location[0]), float(location[1]), float(location[2])))
        if rotation:
            obj.rotation_euler = Euler((float(rotation[0]), float(rotation[1]), float(rotation[2])), "XYZ")
        if scale:
            obj.scale = Vector((float(scale[0]), float(scale[1]), float(scale[2])))

        # Update depsgraph / view layer
        bpy.context.view_layer.update()

        # Record atomic undo step
        push_undo_step(f"AI: Create {p_type} ({obj.name})")

        # Live scene lookup for snapshot verification
        actual_obj = bpy.data.objects.get(obj.name)
        exists_in_scene = actual_obj is not None

        return {
            "created": True,
            "exists": exists_in_scene,
            "object_name": obj.name,
            "primitive_type": p_type,
            "type": obj.type,
            "location": [round(v, 4) for v in obj.location],
            "rotation": [round(v, 4) for v in obj.rotation_euler],
            "scale": [round(v, 4) for v in obj.scale],
            "vertex_count": len(mesh.vertices),
            "face_count": len(mesh.polygons),
        }
