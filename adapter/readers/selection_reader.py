"""Selection reader for extracting active selection state."""

from typing import Any, Dict, List
import bpy


class SelectionReader:
    """Extracts active selection and mode information from Blender context."""

    @staticmethod
    def read() -> Dict[str, Any]:
        """Read and serialize current selection state.

        Returns:
            Dict conforming to the inspect_selection grounding schema.
        """
        context = bpy.context
        mode = getattr(context, "mode", "OBJECT")

        active_obj_name = None
        if hasattr(context, "view_layer") and context.view_layer and context.view_layer.objects.active:
            active_obj_name = context.view_layer.objects.active.name

        selected_obj_names: List[str] = []
        if hasattr(context, "selected_objects") and context.selected_objects:
            selected_obj_names = sorted([obj.name for obj in context.selected_objects])

        return {
            "active_object": active_obj_name,
            "mode": mode,
            "selected_objects": selected_obj_names,
            "selection_count": len(selected_obj_names),
        }
