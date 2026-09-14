"""Modifier mutator for adding non-destructive geometry modifiers via Blender Data API."""

from typing import Any, Dict, Optional
import bpy

from adapter.mutators.undo_manager import push_undo_step


class ModifierMutator:
    """Manages non-destructive geometry modifiers (BEVEL, SUBSURF, BOOLEAN) via Data API."""

    SUPPORTED_TYPES = {"BEVEL", "SUBSURF", "BOOLEAN"}
    SUPPORTED_BOOLEAN_OPS = {"DIFFERENCE", "UNION"}

    @classmethod
    def add_modifier(
        cls,
        name: str,
        modifier_type: str,
        modifier_name: Optional[str] = None,
        width: Optional[float] = None,
        segments: Optional[int] = None,
        levels: Optional[int] = None,
        operation: Optional[str] = None,
        target_object: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add or update a modifier on a target mesh object.

        Args:
            name: Name of target mesh object.
            modifier_type: 'BEVEL', 'SUBSURF', or 'BOOLEAN'.
            modifier_name: Optional name for modifier in stack.
            width: For BEVEL: Bevel width in meters (>= 0). Default 0.05.
            segments: For BEVEL: Number of segments (>= 1). Default 2.
            levels: For SUBSURF: Subdivision levels in viewport (>= 0). Default 1.
            operation: For BOOLEAN: 'DIFFERENCE' or 'UNION'. Default 'DIFFERENCE'.
            target_object: For BOOLEAN: Target mesh object to cut/union with.

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
            raise ValueError(f"Object '{target_name}' is of type '{obj.type}'. Only MESH objects support geometry modifiers.")

        if not modifier_type or not isinstance(modifier_type, str):
            raise ValueError(f"Modifier type must be one of {sorted(cls.SUPPORTED_TYPES)}.")

        mod_type_upper = modifier_type.strip().upper()
        if mod_type_upper not in cls.SUPPORTED_TYPES:
            raise ValueError(f"Invalid modifier_type '{modifier_type}'. Must be one of {sorted(cls.SUPPORTED_TYPES)}.")

        # Type-specific validation
        if mod_type_upper == "BEVEL":
            eff_width = float(width) if width is not None else 0.05
            if eff_width < 0.0:
                raise ValueError(f"Bevel width must be non-negative, got {eff_width}.")
            eff_segments = int(segments) if segments is not None else 2
            if eff_segments < 1:
                raise ValueError(f"Bevel segments must be at least 1, got {eff_segments}.")

        elif mod_type_upper == "SUBSURF":
            eff_levels = int(levels) if levels is not None else 1
            if eff_levels < 0:
                raise ValueError(f"Subsurf levels must be non-negative, got {eff_levels}.")

        elif mod_type_upper == "BOOLEAN":
            eff_op = str(operation).strip().upper() if operation is not None else "DIFFERENCE"
            if eff_op not in cls.SUPPORTED_BOOLEAN_OPS:
                raise ValueError(f"Boolean operation must be one of {sorted(cls.SUPPORTED_BOOLEAN_OPS)}, got {operation!r}.")

            if not target_object or not isinstance(target_object, str) or not target_object.strip():
                raise ValueError("Boolean modifier requires a valid 'target_object' name.")

            target_clean = target_object.strip()
            if target_clean == target_name:
                raise ValueError("Boolean modifier cannot use the same object as its target.")

            target_obj = bpy.data.objects.get(target_clean)
            if target_obj is None:
                raise ValueError(f"Boolean target object '{target_clean}' not found in scene.")

            if target_obj.type != "MESH":
                raise ValueError(f"Boolean target object '{target_clean}' must be of type 'MESH', got '{target_obj.type}'.")

        # Lookup or create modifier
        custom_name = modifier_name.strip() if modifier_name and isinstance(modifier_name, str) and modifier_name.strip() else None
        target_mod_name = custom_name or mod_type_upper.capitalize()

        existing_mod = obj.modifiers.get(target_mod_name)
        if existing_mod is not None and existing_mod.type == mod_type_upper:
            mod = existing_mod
        else:
            mod = obj.modifiers.new(name=target_mod_name, type=mod_type_upper)

        # Apply properties
        if mod_type_upper == "BEVEL":
            mod.width = eff_width
            mod.segments = eff_segments
        elif mod_type_upper == "SUBSURF":
            mod.levels = eff_levels
            mod.render_levels = eff_levels
        elif mod_type_upper == "BOOLEAN":
            mod.operation = eff_op
            mod.object = target_obj

        if hasattr(bpy.context, "view_layer") and bpy.context.view_layer:
            bpy.context.view_layer.update()

        # Atomic undo point
        push_undo_step(f"AI: Add {mod_type_upper} Modifier on {obj.name}")

        snapshot: Dict[str, Any] = {
            "exists": True,
            "object_name": obj.name,
            "modifier_name": mod.name,
            "modifier_type": mod.type,
        }

        if mod.type == "BEVEL":
            snapshot["width"] = round(float(mod.width), 4)
            snapshot["segments"] = int(mod.segments)
        elif mod.type == "SUBSURF":
            snapshot["levels"] = int(mod.levels)
        elif mod.type == "BOOLEAN":
            snapshot["operation"] = str(mod.operation)
            snapshot["target_object"] = mod.object.name if mod.object else None

        return snapshot
