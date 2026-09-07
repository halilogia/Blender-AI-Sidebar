"""Material reader for extracting material properties, Principled BSDF, and slot bindings."""

import math
from typing import Any, Dict, List, Optional
import bpy


class MaterialNotFoundError(Exception):
    """Raised when a material cannot be found in bpy.data.materials."""


class MaterialSlotError(Exception):
    """Raised when a material slot index is invalid or out of range."""


def sanitize_float(val: float, precision: int = 4) -> float:
    """Sanitize float against NaN / Inf and round to specified precision."""
    if math.isnan(val) or math.isinf(val):
        return 0.0
    return round(float(val), precision)


def get_socket_value(node: Any, socket_name: str) -> Any:
    """Safely retrieve a node input socket's default value by name.

    Handles Blender 4.x / 5.x naming variations by exact match followed by normalized fallback.
    """
    if not hasattr(node, "inputs"):
        return None

    # 1. Exact match
    sock = node.inputs.get(socket_name)

    # 2. Case-insensitive / normalized fallback
    if sock is None:
        normalized_target = socket_name.lower().replace(" ", "").replace("_", "")
        for input_sock in node.inputs:
            if input_sock.name.lower().replace(" ", "").replace("_", "") == normalized_target:
                sock = input_sock
                break

    if sock is None or not hasattr(sock, "default_value"):
        return None

    val = sock.default_value
    # Multi-value socket (RGBA or Vector)
    if hasattr(val, "__iter__"):
        return [sanitize_float(v, 4) for v in val]
    elif isinstance(val, (int, float)):
        return sanitize_float(val, 4)
    return val


class MaterialReader:
    """Extracts a deterministic, token-efficient summary of a Blender material."""

    @staticmethod
    def read(
        material_name: Optional[str] = None,
        object_name: Optional[str] = None,
        slot_index: Optional[int] = 0,
    ) -> Dict[str, Any]:
        """Read and serialize material properties using dual-entry contract.

        Args:
            material_name: Direct name of the material in bpy.data.materials.
            object_name: Name of the object from which to query a material slot.
            slot_index: Zero-indexed material slot on the object (default 0).

        Returns:
            Dict conforming to the inspect_material grounding schema.

        Raises:
            ValueError: If neither or both entry methods are provided.
            ObjectNotFoundError: If object_name is not found.
            MaterialSlotError: If slot_index is out of range.
            MaterialNotFoundError: If material_name is not found.
        """
        # 1. Resolve target material
        mat: Optional[bpy.types.Material] = None
        source_object_name: Optional[str] = None
        assigned_slot_index: Optional[int] = None

        if material_name is not None and object_name is not None:
            raise ValueError("Provide either 'material_name' or 'object_name', not both.")

        if material_name is None and object_name is None:
            raise ValueError("Must provide either 'material_name' or 'object_name'.")

        if object_name is not None:
            obj = bpy.data.objects.get(object_name)
            if not obj:
                from adapter.readers.object_reader import ObjectNotFoundError
                raise ObjectNotFoundError(f"Object '{object_name}' was not found in Blender datablocks.")

            if not isinstance(slot_index, int):
                raise MaterialSlotError(f"slot_index must be an integer, got '{type(slot_index).__name__}'.")

            if slot_index < 0 or slot_index >= len(obj.material_slots):
                total_slots = len(obj.material_slots)
                raise MaterialSlotError(
                    f"Slot index {slot_index} is out of range for object '{object_name}' (total slots: {total_slots})."
                )

            slot = obj.material_slots[slot_index]
            source_object_name = obj.name
            assigned_slot_index = slot_index
            mat = slot.material

            if mat is None:
                # Slot exists but is empty (no material assigned)
                return {
                    "assigned_objects": [obj.name],
                    "is_linked": False,
                    "library_name": None,
                    "material_name": None,
                    "node_summary": None,
                    "principled_bsdf": None,
                    "slot_binding": {
                        "object_name": source_object_name,
                        "slot_index": assigned_slot_index,
                    },
                    "use_nodes": False,
                }
        else:
            mat = bpy.data.materials.get(material_name)
            if not mat:
                raise MaterialNotFoundError(f"Material '{material_name}' was not found in bpy.data.materials.")

        # 2. Extract material datablock metadata
        is_linked = mat.library is not None
        library_name = mat.library.name if mat.library else None
        use_nodes = bool(mat.use_nodes)

        # 3. Extract assigned objects across the current scene/data
        assigned_objects: List[str] = sorted([
            o.name for o in bpy.data.objects
            if hasattr(o, "material_slots") and any(s.material == mat for s in o.material_slots)
        ])

        # 4. Extract node summary & Principled BSDF if use_nodes is True
        principled_data: Optional[Dict[str, Any]] = None
        node_summary: Optional[Dict[str, Any]] = None

        if use_nodes and mat.node_tree:
            nodes = mat.node_tree.nodes
            node_types = sorted(list(set(n.type for n in nodes)))
            node_summary = {
                "node_count": len(nodes),
                "node_types": node_types,
            }

            # Locate first Principled BSDF node if present
            bsdf_node = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
            if bsdf_node:
                principled_data = {
                    "alpha": get_socket_value(bsdf_node, "Alpha"),
                    "base_color": get_socket_value(bsdf_node, "Base Color"),
                    "emission_color": get_socket_value(bsdf_node, "Emission Color"),
                    "emission_strength": get_socket_value(bsdf_node, "Emission Strength"),
                    "ior": get_socket_value(bsdf_node, "IOR"),
                    "metallic": get_socket_value(bsdf_node, "Metallic"),
                    "roughness": get_socket_value(bsdf_node, "Roughness"),
                }

        slot_binding = None
        if source_object_name is not None:
            slot_binding = {
                "object_name": source_object_name,
                "slot_index": assigned_slot_index,
            }

        return {
            "assigned_objects": assigned_objects,
            "is_linked": is_linked,
            "library_name": library_name,
            "material_name": mat.name,
            "node_summary": node_summary,
            "principled_bsdf": principled_data,
            "slot_binding": slot_binding,
            "use_nodes": use_nodes,
        }
