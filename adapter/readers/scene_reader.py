"""Scene reader for extracting compact scene overview."""

from typing import Any, Dict, List
import bpy


class SceneReader:
    """Extracts a deterministic, token-efficient summary of the active Blender scene."""

    @staticmethod
    def read() -> Dict[str, Any]:
        """Read and serialize the active scene summary.

        Returns:
            Dict conforming to the inspect_scene grounding schema.
        """
        context = bpy.context
        scene = context.scene
        if not scene:
            raise RuntimeError("No active scene found in Blender context.")

        scene_name = scene.name
        unit_system = getattr(scene.unit_settings, "system", "METRIC")

        # Active collection
        active_collection_name = None
        if hasattr(context, "view_layer") and context.view_layer.active_layer_collection:
            active_collection_name = context.view_layer.active_layer_collection.name

        # All collections in the scene (sorted)
        collections: List[str] = sorted([col.name for col in bpy.data.collections])
        if not collections and hasattr(scene, "collection"):
            collections = [scene.collection.name]

        # Active & selected objects
        active_obj_name = None
        if hasattr(context, "view_layer") and context.view_layer.objects.active:
            active_obj_name = context.view_layer.objects.active.name

        selected_obj_names: List[str] = []
        if hasattr(context, "selected_objects"):
            selected_obj_names = sorted([obj.name for obj in context.selected_objects])

        # Active camera
        active_camera_name = scene.camera.name if scene.camera else None

        # Objects summary & type breakdown
        counts: Dict[str, int] = {}
        objects_summary: List[Dict[str, Any]] = []

        for obj in scene.objects:
            obj_type = str(obj.type).lower()
            counts[obj_type] = counts.get(obj_type, 0) + 1
            objects_summary.append({
                "is_linked": obj.library is not None,
                "name": obj.name,
                "type": obj.type,
            })

        # Deterministic sorting
        objects_summary.sort(key=lambda item: item["name"])
        counts["total"] = len(scene.objects)
        sorted_counts = dict(sorted(counts.items()))

        return {
            "active_camera": active_camera_name,
            "active_collection": active_collection_name,
            "active_object": active_obj_name,
            "collections": collections,
            "counts": sorted_counts,
            "objects": objects_summary,
            "scene_name": scene_name,
            "selected_objects": selected_obj_names,
            "unit_system": unit_system,
        }
