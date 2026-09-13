"""Material mutator for manipulating Principled BSDF shaders and material slot bindings."""

import math
from typing import Any, Dict, List, Optional, Sequence
import bpy

from adapter.readers.object_reader import ObjectNotFoundError
from adapter.readers.material_reader import MaterialReader
from adapter.mutators.undo_manager import push_undo_step


def find_input_socket(node: Any, *socket_names: str) -> Optional[Any]:
    """Locate an input socket on a shader node by exact match or normalized name."""
    if not hasattr(node, "inputs"):
        return None
    for target in socket_names:
        # 1. Exact match on inputs dict
        sock = node.inputs.get(target)
        if sock is not None:
            return sock
        norm = target.lower().replace(" ", "").replace("_", "")
        # 2. Case/whitespace-insensitive match against name or identifier
        for s in node.inputs:
            s_name = getattr(s, "name", "").lower().replace(" ", "").replace("_", "")
            s_id = getattr(s, "identifier", "").lower().replace(" ", "").replace("_", "")
            if s_name == norm or s_id == norm:
                return s
    return None


def clamp_float(val: Any, min_v: float = 0.0, max_v: float = 1.0) -> float:
    """Clamp a numeric value to [min_v, max_v], safely sanitizing NaN / Inf."""
    try:
        f = float(val)
    except (TypeError, ValueError) as err:
        raise ValueError(f"Expected numeric value, got '{val}': {err}")
    if math.isnan(f) or math.isinf(f):
        return 0.0
    return max(min_v, min(max_v, f))


def normalize_color(color_seq: Any) -> List[float]:
    """Normalize RGB or RGBA sequence to 4-element RGBA clamped to [0.0, 1.0]."""
    if not isinstance(color_seq, (list, tuple)):
        raise ValueError(
            f"Color must be a sequence of 3 (RGB) or 4 (RGBA) numbers, got {type(color_seq).__name__}"
        )
    if len(color_seq) == 3:
        r, g, b = color_seq
        a = 1.0
    elif len(color_seq) == 4:
        r, g, b, a = color_seq
    else:
        raise ValueError(f"Color sequence must have 3 (RGB) or 4 (RGBA) elements, got {len(color_seq)}.")
    return [clamp_float(r), clamp_float(g), clamp_float(b), clamp_float(a)]


def normalize_emission_strength(val: Any) -> float:
    """Normalize emission strength to a non-negative float."""
    try:
        f = float(val)
    except (TypeError, ValueError) as err:
        raise ValueError(f"Emission strength must be numeric, got '{val}': {err}")
    if math.isnan(f) or math.isinf(f):
        return 0.0
    return max(0.0, f)


def ensure_principled_bsdf(mat: bpy.types.Material) -> Any:
    """Ensure material uses nodes and has a Principled BSDF connected to a Material Output.

    Never clears existing nodes; preserves intact node networks.
    """
    if not mat.use_nodes:
        mat.use_nodes = True

    if mat.node_tree is None:
        raise ValueError(f"Material '{mat.name}' has no active node_tree.")

    nodes = mat.node_tree.nodes
    bsdf_node = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf_node is None:
        bsdf_node = nodes.new(type="ShaderNodeBsdfPrincipled")
        bsdf_node.location = (0, 300)

    output_node = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None)
    if output_node is None:
        output_node = nodes.new(type="ShaderNodeOutputMaterial")
        output_node.location = (300, 300)

    surface_sock = output_node.inputs.get("Surface")
    if surface_sock and not surface_sock.is_linked:
        bsdf_out = bsdf_node.outputs.get("BSDF")
        if bsdf_out:
            mat.node_tree.links.new(bsdf_out, surface_sock)

    return bsdf_node


def assign_material_to_slot(obj: bpy.types.Object, mat: bpy.types.Material, slot_index: int = 0) -> None:
    """Assign material to an object at the specified slot index, appending slots if needed."""
    if not hasattr(obj, "material_slots"):
        raise ValueError(f"Object '{obj.name}' of type '{obj.type}' does not support material slots.")

    if isinstance(slot_index, bool) or not isinstance(slot_index, int) or slot_index < 0:
        raise ValueError(f"slot_index must be a non-negative integer, got '{slot_index}'.")

    # If slot_index is within existing slots
    if slot_index < len(obj.material_slots):
        obj.material_slots[slot_index].material = mat
        return

    # If slot_index requires expanding object material slots
    if hasattr(obj.data, "materials"):
        while len(obj.material_slots) < slot_index:
            obj.data.materials.append(None)
        obj.data.materials.append(mat)
    else:
        raise ValueError(f"Object '{obj.name}' geometry does not support material slot expansion.")


class MaterialMutator:
    """Modifies material properties and slot assignments with atomic undo."""

    @classmethod
    def set_material(
        cls,
        object_name: Optional[str] = None,
        material_name: Optional[str] = None,
        slot_index: int = 0,
        base_color: Optional[Sequence[float]] = None,
        metallic: Optional[float] = None,
        roughness: Optional[float] = None,
        emission_color: Optional[Sequence[float]] = None,
        emission_strength: Optional[float] = None,
        alpha: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Set Principled BSDF shader properties on an object slot or material.

        Args:
            object_name: Optional name of the target object.
            material_name: Optional name of the material in bpy.data.materials.
            slot_index: Zero-indexed material slot on the object (default 0).
            base_color: Optional RGB/RGBA sequence in [0, 1].
            metallic: Optional metallic factor in [0, 1].
            roughness: Optional roughness factor in [0, 1].
            emission_color: Optional emission RGB/RGBA sequence in [0, 1].
            emission_strength: Optional emission strength factor (>= 0.0).
            alpha: Optional alpha transparency factor in [0, 1].

        Returns:
            Dict[str, Any]: Execution result containing before/after/actual material snapshots.
        """
        # 1. Validation: must provide at least one material property
        has_property = any(
            v is not None for v in [base_color, metallic, roughness, emission_color, emission_strength, alpha]
        )
        if not has_property:
            raise ValueError(
                "At least one material property ('base_color', 'metallic', 'roughness', "
                "'emission_color', 'emission_strength', 'alpha') must be provided."
            )

        # 2. Validation: must provide at least one target (object_name or material_name)
        if not object_name and not material_name:
            raise ValueError("Must provide at least one of 'object_name' or 'material_name'.")

        effective_slot_index = 0 if slot_index is None else slot_index
        if isinstance(effective_slot_index, bool) or not isinstance(effective_slot_index, int) or effective_slot_index < 0:
            raise ValueError(f"slot_index must be a non-negative integer, got '{effective_slot_index}'.")

        # 3. Resolve target object and material
        obj: Optional[bpy.types.Object] = None
        mat: Optional[bpy.types.Material] = None
        created_material: bool = False

        if object_name is not None:
            obj = bpy.data.objects.get(object_name)
            if obj is None:
                raise ObjectNotFoundError(f"Object '{object_name}' not found in scene.")

        if material_name is not None:
            mat = bpy.data.materials.get(material_name)
            if mat is None:
                mat = bpy.data.materials.new(name=material_name)
                created_material = True
            if obj is not None:
                assign_material_to_slot(obj, mat, effective_slot_index)
        else:
            # object_name is provided, material_name is None
            if effective_slot_index < len(obj.material_slots) and obj.material_slots[effective_slot_index].material is not None:
                mat = obj.material_slots[effective_slot_index].material
            else:
                mat_name = f"{obj.name}_Material"
                mat = bpy.data.materials.new(name=mat_name)
                created_material = True
                assign_material_to_slot(obj, mat, effective_slot_index)

        # 4. Capture before snapshot
        before_snap: Optional[Dict[str, Any]] = None
        if not created_material:
            try:
                before_snap = MaterialReader.read(material_name=mat.name)
            except Exception:
                before_snap = None

        # 5. Ensure Principled BSDF node and output connection
        bsdf_node = ensure_principled_bsdf(mat)

        # 6. Apply mutations and collect changed field names
        changed_fields: List[str] = []

        if base_color is not None:
            sock = find_input_socket(bsdf_node, "Base Color", "BaseColor", "Color")
            if sock is None:
                raise ValueError(f"Socket 'Base Color' not found on Principled BSDF of material '{mat.name}'.")
            norm_col = normalize_color(base_color)
            sock.default_value = norm_col
            changed_fields.append("base_color")

        if metallic is not None:
            sock = find_input_socket(bsdf_node, "Metallic")
            if sock is None:
                raise ValueError(f"Socket 'Metallic' not found on Principled BSDF of material '{mat.name}'.")
            sock.default_value = clamp_float(metallic)
            changed_fields.append("metallic")

        if roughness is not None:
            sock = find_input_socket(bsdf_node, "Roughness")
            if sock is None:
                raise ValueError(f"Socket 'Roughness' not found on Principled BSDF of material '{mat.name}'.")
            sock.default_value = clamp_float(roughness)
            changed_fields.append("roughness")

        if emission_color is not None:
            sock = find_input_socket(bsdf_node, "Emission Color", "Emission", "EmissionColor")
            if sock is None:
                raise ValueError(f"Socket 'Emission Color' not found on Principled BSDF of material '{mat.name}'.")
            norm_em = normalize_color(emission_color)
            sock.default_value = norm_em
            changed_fields.append("emission_color")

        if emission_strength is not None:
            sock = find_input_socket(bsdf_node, "Emission Strength", "EmissionStrength")
            if sock is None:
                raise ValueError(f"Socket 'Emission Strength' not found on Principled BSDF of material '{mat.name}'.")
            sock.default_value = normalize_emission_strength(emission_strength)
            changed_fields.append("emission_strength")

        if alpha is not None:
            sock = find_input_socket(bsdf_node, "Alpha")
            if sock is None:
                raise ValueError(f"Socket 'Alpha' not found on Principled BSDF of material '{mat.name}'.")
            sock.default_value = clamp_float(alpha)
            changed_fields.append("alpha")

        # 7. Update view layer
        if bpy.context and hasattr(bpy.context, "view_layer") and bpy.context.view_layer:
            bpy.context.view_layer.update()

        # 8. Capture after snapshot
        after_snap = MaterialReader.read(material_name=mat.name)

        # 9. Record atomic undo step
        push_undo_step(f"AI: Set Material ({mat.name})")

        return {
            "material_name": mat.name,
            "object_name": obj.name if obj else None,
            "slot_index": effective_slot_index if obj else None,
            "changed": changed_fields,
            "before": before_snap,
            "after": after_snap,
            "actual": after_snap,
        }

    @classmethod
    def assign_material(
        cls,
        object_name: str,
        material_name: str,
        slot_index: int = 0,
    ) -> Dict[str, Any]:
        """Assign an existing or new material to an object at the specified slot index.

        Args:
            object_name: Target object name in bpy.data.objects.
            material_name: Target material name in bpy.data.materials.
            slot_index: Zero-indexed material slot on the object (default 0).

        Returns:
            Dict[str, Any]: Structured assignment result.
        """
        if not object_name or not isinstance(object_name, str):
            raise ValueError("Argument 'object_name' must be a non-empty string.")
        if not material_name or not isinstance(material_name, str):
            raise ValueError("Argument 'material_name' must be a non-empty string.")

        effective_slot_index = 0 if slot_index is None else slot_index
        if isinstance(effective_slot_index, bool) or not isinstance(effective_slot_index, int) or effective_slot_index < 0:
            raise ValueError(f"slot_index must be a non-negative integer, got '{effective_slot_index}'.")

        # 1. Resolve object
        obj = bpy.data.objects.get(object_name)
        if obj is None:
            raise ObjectNotFoundError(f"Object '{object_name}' not found in scene.")

        # 2. Resolve or create material
        mat = bpy.data.materials.get(material_name)
        if mat is None:
            mat = bpy.data.materials.new(name=material_name)
            ensure_principled_bsdf(mat)

        # 3. Capture before state
        prev_mat_name: Optional[str] = None
        if hasattr(obj, "material_slots") and effective_slot_index < len(obj.material_slots):
            if obj.material_slots[effective_slot_index].material:
                prev_mat_name = obj.material_slots[effective_slot_index].material.name
        before = {"material_name": prev_mat_name}

        # 4. Assign to slot
        assign_material_to_slot(obj, mat, effective_slot_index)

        # 5. Update view layer
        if bpy.context and hasattr(bpy.context, "view_layer") and bpy.context.view_layer:
            bpy.context.view_layer.update()

        # 6. Capture after state
        assigned_mat = (
            obj.material_slots[effective_slot_index].material
            if effective_slot_index < len(obj.material_slots) and obj.material_slots[effective_slot_index].material
            else None
        )
        after_mat_name = assigned_mat.name if assigned_mat else None
        after = {"material_name": after_mat_name}

        # 7. Record atomic undo step
        push_undo_step(f"AI: Assign Material ({mat.name} -> {obj.name})")

        return {
            "assigned": True,
            "object_name": obj.name,
            "material_name": mat.name,
            "slot_index": effective_slot_index,
            "before": before,
            "after": after,
            "actual": after,
        }
