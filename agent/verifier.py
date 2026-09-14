"""Deterministic, rule-based verification engine for mutation ChangeSets.

Zero Blender (bpy) dependencies. Pure Python standard library only.
"""

import math
from typing import Any, Dict, List, Optional, Sequence

from core.change_set import ChangeSet, VerificationResult, VerificationStatus


def normalize_angle(rad: float) -> float:
    """Normalize an angle in radians to [-pi, pi]."""
    return (float(rad) + math.pi) % (2.0 * math.pi) - math.pi


def angle_difference(actual: float, expected: float) -> float:
    """Calculate the shortest angular difference in radians in [-pi, pi]."""
    return normalize_angle(float(actual) - float(expected))


class ChangeVerifier:
    """Evaluates mutation ChangeSets deterministically without LLM reliance."""

    EPSILON: float = 1e-3
    VECTOR_PROPERTIES = {"location", "rotation", "scale"}

    def __init__(self, epsilon: float = EPSILON):
        self.epsilon = float(epsilon)

    def verify(self, change_set: ChangeSet) -> VerificationResult:
        """Verify that actual_after conforms to expected_after for the given operation.

        Args:
            change_set: The mutation ChangeSet containing before, expected, and actual states.

        Returns:
            VerificationResult with PASS/FAIL status and machine-readable mismatches.
        """
        op = str(change_set.operation).strip().lower()
        target = str(change_set.target_name).strip()

        if op == "create":
            return self._verify_create(target, change_set)
        elif op == "create_camera":
            return self._verify_create_camera(target, change_set)
        elif op == "create_light":
            return self._verify_create_light(target, change_set)
        elif op == "set_shading":
            return self._verify_set_shading(target, change_set)
        elif op == "add_modifier":
            return self._verify_add_modifier(target, change_set)
        elif op == "transform":
            return self._verify_transform(target, change_set)
        elif op == "delete":
            return self._verify_delete(target, change_set)
        elif op in ("set_material", "material"):
            return self._verify_set_material(target, change_set)
        elif op in ("assign_material", "assign"):
            return self._verify_assign_material(target, change_set)
        else:
            return VerificationResult(
                status=VerificationStatus.FAIL,
                operation=op,
                target_name=target,
                mismatches=[
                    {
                        "property": "operation",
                        "expected": "One of ['create', 'create_camera', 'create_light', 'set_shading', 'add_modifier', 'transform', 'delete', 'set_material', 'assign_material']",
                        "actual": op,
                        "diff": None,
                    }
                ],
                summary=f"Verification FAILED: Unsupported operation '{op}' for target '{target}'.",
            )

    def _compare_numeric_vector(
        self,
        prop_name: str,
        expected: Sequence[Any],
        actual: Sequence[Any],
        is_rotation: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Compare two 3D numeric vectors within epsilon tolerance.

        Returns mismatch dict if difference exceeds epsilon, else None.
        """
        if not isinstance(expected, (list, tuple)) or not isinstance(actual, (list, tuple)):
            return {
                "property": prop_name,
                "expected": expected,
                "actual": actual,
                "diff": None,
            }

        if len(expected) != len(actual):
            return {
                "property": prop_name,
                "expected": list(expected),
                "actual": list(actual),
                "diff": None,
            }

        diffs: List[float] = []
        has_mismatch = False

        for exp_val, act_val in zip(expected, actual):
            try:
                e_num = float(exp_val)
                a_num = float(act_val)
            except (ValueError, TypeError):
                return {
                    "property": prop_name,
                    "expected": list(expected),
                    "actual": list(actual),
                    "diff": None,
                }

            if is_rotation:
                delta = angle_difference(a_num, e_num)
            else:
                delta = a_num - e_num

            rounded_delta = round(delta, 6)
            diffs.append(rounded_delta)

            if abs(delta) > self.epsilon:
                has_mismatch = True

        if has_mismatch:
            return {
                "property": prop_name,
                "expected": [round(float(v), 4) for v in expected],
                "actual": [round(float(v), 4) for v in actual],
                "diff": diffs,
            }

        return None

    def _verify_create(self, target: str, change_set: ChangeSet) -> VerificationResult:
        """Verification rules for object creation."""
        mismatches: List[Dict[str, Any]] = []
        expected = change_set.expected_after or {}
        actual = change_set.actual_after or {}

        # 1. Existence check (must exist)
        actual_exists = actual.get("exists", actual.get("created", False))
        if not actual_exists:
            mismatches.append(
                {
                    "property": "exists",
                    "expected": True,
                    "actual": False,
                    "diff": None,
                }
            )
            return self._build_result("create", target, mismatches)

        # 2. Type check
        expected_type = expected.get("primitive_type") or expected.get("type")
        if expected_type is not None:
            actual_type = actual.get("primitive_type") or actual.get("type")
            if str(actual_type).strip().upper() != str(expected_type).strip().upper():
                mismatches.append(
                    {
                        "property": "type",
                        "expected": str(expected_type).strip().upper(),
                        "actual": str(actual_type).strip().upper() if actual_type else None,
                        "diff": None,
                    }
                )

        # 3. Geometry check (vertex_count > 0)
        expected_vcount = expected.get("vertex_count")
        actual_vcount = actual.get("vertex_count")
        if expected_vcount is not None:
            if actual_vcount is None or int(actual_vcount) != int(expected_vcount):
                mismatches.append(
                    {
                        "property": "vertex_count",
                        "expected": int(expected_vcount),
                        "actual": int(actual_vcount) if actual_vcount is not None else None,
                        "diff": (
                            int(actual_vcount) - int(expected_vcount)
                            if actual_vcount is not None
                            else None
                        ),
                    }
                )
        else:
            # Default geometry check: created object must have non-zero geometry
            if actual_vcount is not None and int(actual_vcount) <= 0:
                mismatches.append(
                    {
                        "property": "vertex_count",
                        "expected": "> 0",
                        "actual": int(actual_vcount),
                        "diff": None,
                    }
                )

        # 4. Numeric vectors: location, rotation, scale
        for prop in ["location", "rotation", "scale"]:
            if prop in expected:
                if prop not in actual or actual[prop] is None:
                    mismatches.append(
                        {
                            "property": prop,
                            "expected": expected[prop],
                            "actual": None,
                            "diff": None,
                        }
                    )
                else:
                    mismatch = self._compare_numeric_vector(
                        prop,
                        expected[prop],
                        actual[prop],
                        is_rotation=(prop == "rotation"),
                    )
                    if mismatch:
                        mismatches.append(mismatch)

        return self._build_result("create", target, mismatches)

    def _verify_create_camera(self, target: str, change_set: ChangeSet) -> VerificationResult:
        """Verification rules for camera creation or modification."""
        mismatches: List[Dict[str, Any]] = []
        expected = change_set.expected_after or {}
        actual = change_set.actual_after or {}

        # 1. Existence check
        if not actual.get("exists", False):
            mismatches.append(
                {
                    "property": "exists",
                    "expected": True,
                    "actual": False,
                    "diff": None,
                }
            )
            return self._build_result("create_camera", target, mismatches)

        # 2. Type check
        expected_type = expected.get("type", "CAMERA")
        actual_type = actual.get("type")
        if actual_type != expected_type:
            mismatches.append(
                {
                    "property": "type",
                    "expected": expected_type,
                    "actual": actual_type,
                    "diff": None,
                }
            )

        # 3. Numeric vectors: location, rotation
        for prop in ("location", "rotation"):
            if prop in expected and expected[prop] is not None:
                if prop not in actual or actual[prop] is None:
                    mismatches.append(
                        {
                            "property": prop,
                            "expected": expected[prop],
                            "actual": None,
                            "diff": None,
                        }
                    )
                else:
                    mismatch = self._compare_numeric_vector(
                        prop,
                        expected[prop],
                        actual[prop],
                        is_rotation=(prop == "rotation"),
                    )
                    if mismatch:
                        mismatches.append(mismatch)

        # 4. Lens check
        if "lens" in expected and expected["lens"] is not None:
            expected_lens = float(expected["lens"])
            actual_lens = float(actual.get("lens", 0.0))
            if abs(actual_lens - expected_lens) > self.epsilon:
                mismatches.append(
                    {
                        "property": "lens",
                        "expected": round(expected_lens, 2),
                        "actual": round(actual_lens, 2),
                        "diff": round(actual_lens - expected_lens, 4),
                    }
                )

        # 5. Active camera check
        if "is_active_camera" in expected and expected["is_active_camera"] is not None:
            expected_active = bool(expected["is_active_camera"])
            actual_active = bool(actual.get("is_active_camera", False))
            if actual_active != expected_active:
                mismatches.append(
                    {
                        "property": "is_active_camera",
                        "expected": expected_active,
                        "actual": actual_active,
                        "diff": None,
                    }
                )

        return self._build_result("create_camera", target, mismatches)

    def _verify_create_light(self, target: str, change_set: ChangeSet) -> VerificationResult:
        """Verification rules for light creation or modification."""
        mismatches: List[Dict[str, Any]] = []
        expected = change_set.expected_after or {}
        actual = change_set.actual_after or {}

        # 1. Existence check
        if not actual.get("exists", False):
            mismatches.append(
                {
                    "property": "exists",
                    "expected": True,
                    "actual": False,
                    "diff": None,
                }
            )
            return self._build_result("create_light", target, mismatches)

        # 2. Object Type check
        expected_type = expected.get("type", "LIGHT")
        actual_type = actual.get("type")
        if actual_type != expected_type:
            mismatches.append(
                {
                    "property": "type",
                    "expected": expected_type,
                    "actual": actual_type,
                    "diff": None,
                }
            )

        # 3. Light Type check (POINT, SUN, SPOT, AREA)
        if "light_type" in expected and expected["light_type"] is not None:
            exp_lt = str(expected["light_type"]).upper()
            act_lt = str(actual.get("light_type", "")).upper()
            if act_lt != exp_lt:
                mismatches.append(
                    {
                        "property": "light_type",
                        "expected": exp_lt,
                        "actual": act_lt,
                        "diff": None,
                    }
                )

        # 4. Numeric vectors: location, rotation
        for prop in ("location", "rotation"):
            if prop in expected and expected[prop] is not None:
                if prop not in actual or actual[prop] is None:
                    mismatches.append(
                        {
                            "property": prop,
                            "expected": expected[prop],
                            "actual": None,
                            "diff": None,
                        }
                    )
                else:
                    mismatch = self._compare_numeric_vector(
                        prop,
                        expected[prop],
                        actual[prop],
                        is_rotation=(prop == "rotation"),
                    )
                    if mismatch:
                        mismatches.append(mismatch)

        # 5. Energy check
        if "energy" in expected and expected["energy"] is not None:
            mismatch = self._compare_scalar("energy", expected["energy"], actual.get("energy"))
            if mismatch:
                mismatches.append(mismatch)

        # 6. Color check
        if "color" in expected and expected["color"] is not None:
            if "color" not in actual or actual["color"] is None:
                mismatches.append(
                    {
                        "property": "color",
                        "expected": expected["color"],
                        "actual": None,
                        "diff": None,
                    }
                )
            else:
                mismatch = self._compare_numeric_vector("color", expected["color"], actual["color"], is_rotation=False)
                if mismatch:
                    mismatches.append(mismatch)

        return self._build_result("create_light", target, mismatches)

    def _verify_set_shading(self, target: str, change_set: ChangeSet) -> VerificationResult:
        """Verification rules for polygon shading mutations."""
        mismatches: List[Dict[str, Any]] = []
        expected = change_set.expected_after or {}
        actual = change_set.actual_after or {}

        # 1. Existence check
        if not actual.get("exists", False):
            mismatches.append(
                {
                    "property": "exists",
                    "expected": True,
                    "actual": False,
                    "diff": None,
                }
            )
            return self._build_result("set_shading", target, mismatches)

        # 2. Shading check
        if "shading" in expected and expected["shading"] is not None:
            exp_sh = str(expected["shading"]).upper()
            act_sh = str(actual.get("shading", "")).upper()
            if act_sh != exp_sh:
                mismatches.append(
                    {
                        "property": "shading",
                        "expected": exp_sh,
                        "actual": act_sh,
                        "diff": None,
                    }
                )

        return self._build_result("set_shading", target, mismatches)

    def _verify_add_modifier(self, target: str, change_set: ChangeSet) -> VerificationResult:
        """Verification rules for geometry modifier mutations."""
        mismatches: List[Dict[str, Any]] = []
        expected = change_set.expected_after or {}
        actual = change_set.actual_after or {}

        # 1. Existence check
        if not actual.get("exists", False):
            mismatches.append(
                {
                    "property": "exists",
                    "expected": True,
                    "actual": False,
                    "diff": None,
                }
            )
            return self._build_result("add_modifier", target, mismatches)

        # 2. Modifier type check
        if "modifier_type" in expected and expected["modifier_type"] is not None:
            exp_mt = str(expected["modifier_type"]).upper()
            act_mt = str(actual.get("modifier_type", "")).upper()
            if act_mt != exp_mt:
                mismatches.append(
                    {
                        "property": "modifier_type",
                        "expected": exp_mt,
                        "actual": act_mt,
                        "diff": None,
                    }
                )

        # 3. Specific modifier properties
        mod_type = expected.get("modifier_type")
        if mod_type == "BEVEL":
            if "width" in expected and expected["width"] is not None:
                mismatch = self._compare_scalar("width", expected["width"], actual.get("width"))
                if mismatch:
                    mismatches.append(mismatch)
            if "segments" in expected and expected["segments"] is not None:
                exp_seg = int(expected["segments"])
                act_seg = int(actual.get("segments", 0))
                if exp_seg != act_seg:
                    mismatches.append(
                        {
                            "property": "segments",
                            "expected": exp_seg,
                            "actual": act_seg,
                            "diff": act_seg - exp_seg,
                        }
                    )

        elif mod_type == "SUBSURF":
            if "levels" in expected and expected["levels"] is not None:
                exp_lvl = int(expected["levels"])
                act_lvl = int(actual.get("levels", 0))
                if exp_lvl != act_lvl:
                    mismatches.append(
                        {
                            "property": "levels",
                            "expected": exp_lvl,
                            "actual": act_lvl,
                            "diff": act_lvl - exp_lvl,
                        }
                    )

        elif mod_type == "BOOLEAN":
            if "operation" in expected and expected["operation"] is not None:
                exp_op = str(expected["operation"]).upper()
                act_op = str(actual.get("operation", "")).upper()
                if exp_op != act_op:
                    mismatches.append(
                        {
                            "property": "operation",
                            "expected": exp_op,
                            "actual": act_op,
                            "diff": None,
                        }
                    )
            if "target_object" in expected and expected["target_object"] is not None:
                exp_target = str(expected["target_object"]).strip()
                act_target = str(actual.get("target_object", "")).strip()
                if exp_target != act_target:
                    mismatches.append(
                        {
                            "property": "target_object",
                            "expected": exp_target,
                            "actual": act_target,
                            "diff": None,
                        }
                    )

        return self._build_result("add_modifier", target, mismatches)

    def _verify_transform(self, target: str, change_set: ChangeSet) -> VerificationResult:
        """Verification rules for object transformation."""
        mismatches: List[Dict[str, Any]] = []
        expected = change_set.expected_after or {}
        actual = change_set.actual_after or {}

        # 1. Existence check (must still exist)
        if actual.get("exists") is False:
            mismatches.append(
                {
                    "property": "exists",
                    "expected": True,
                    "actual": False,
                    "diff": None,
                }
            )
            return self._build_result("transform", target, mismatches)

        # 2. Verify only the fields expected to have changed or specified in expected_after
        for prop, exp_val in expected.items():
            if prop in self.VECTOR_PROPERTIES:
                if prop not in actual or actual[prop] is None:
                    mismatches.append(
                        {
                            "property": prop,
                            "expected": exp_val,
                            "actual": None,
                            "diff": None,
                        }
                    )
                else:
                    mismatch = self._compare_numeric_vector(
                        prop,
                        exp_val,
                        actual[prop],
                        is_rotation=(prop == "rotation"),
                    )
                    if mismatch:
                        mismatches.append(mismatch)
            elif prop not in {"exists", "target_name", "relative"}:
                act_val = actual.get(prop)
                if act_val != exp_val:
                    mismatches.append(
                        {
                            "property": prop,
                            "expected": exp_val,
                            "actual": act_val,
                            "diff": None,
                        }
                    )

        return self._build_result("transform", target, mismatches)

    def _verify_delete(self, target: str, change_set: ChangeSet) -> VerificationResult:
        """Verification rules for object deletion."""
        mismatches: List[Dict[str, Any]] = []
        actual = change_set.actual_after or {}

        # 1. Target object must not exist in actual_after
        actual_exists = actual.get("exists")
        actual_deleted = actual.get("deleted")

        # If actual explicitly says exists is True, it's a mismatch
        if actual_exists is True:
            mismatches.append(
                {
                    "property": "exists",
                    "expected": False,
                    "actual": True,
                    "diff": None,
                }
            )
        elif actual_deleted is False:
            mismatches.append(
                {
                    "property": "deleted",
                    "expected": True,
                    "actual": False,
                    "diff": None,
                }
            )

        return self._build_result("delete", target, mismatches)

    def _compare_scalar(
        self,
        prop_name: str,
        expected: Any,
        actual: Any,
    ) -> Optional[Dict[str, Any]]:
        """Compare two scalar numbers within epsilon tolerance.

        Returns mismatch dict if difference exceeds epsilon, else None.
        """
        try:
            e_num = float(expected)
            a_num = float(actual)
        except (ValueError, TypeError):
            return {
                "property": prop_name,
                "expected": expected,
                "actual": actual,
                "diff": None,
            }

        delta = a_num - e_num
        if abs(delta) > self.epsilon:
            return {
                "property": prop_name,
                "expected": round(e_num, 4),
                "actual": round(a_num, 4),
                "diff": round(delta, 6),
            }
        return None

    def _verify_set_material(self, target: str, change_set: ChangeSet) -> VerificationResult:
        """Verification rules for Principled BSDF material mutations."""
        mismatches: List[Dict[str, Any]] = []
        expected = change_set.expected_after or {}
        actual = change_set.actual_after or {}

        # Material property container (can be nested under principled_bsdf or flat)
        bsdf_actual = (
            actual.get("principled_bsdf")
            if isinstance(actual.get("principled_bsdf"), dict)
            else actual
        )

        if not isinstance(bsdf_actual, dict):
            mismatches.append(
                {
                    "property": "principled_bsdf",
                    "expected": expected,
                    "actual": None,
                    "diff": None,
                }
            )
            return self._build_result("set_material", target, mismatches)

        # Verify only properties that were expected to change
        for prop, exp_val in expected.items():
            if prop in ("base_color", "emission_color"):
                if prop not in bsdf_actual or bsdf_actual[prop] is None:
                    mismatches.append(
                        {
                            "property": prop,
                            "expected": exp_val,
                            "actual": None,
                            "diff": None,
                        }
                    )
                else:
                    mismatch = self._compare_numeric_vector(
                        prop,
                        exp_val,
                        bsdf_actual[prop],
                        is_rotation=False,
                    )
                    if mismatch:
                        mismatches.append(mismatch)

            elif prop in ("metallic", "roughness", "emission_strength", "alpha"):
                if prop not in bsdf_actual or bsdf_actual[prop] is None:
                    mismatches.append(
                        {
                            "property": prop,
                            "expected": exp_val,
                            "actual": None,
                            "diff": None,
                        }
                    )
                else:
                    mismatch = self._compare_scalar(
                        prop,
                        exp_val,
                        bsdf_actual[prop],
                    )
                    if mismatch:
                        mismatches.append(mismatch)

            else:
                act_val = bsdf_actual.get(prop)
                if act_val != exp_val:
                    mismatches.append(
                        {
                            "property": prop,
                            "expected": exp_val,
                            "actual": act_val,
                            "diff": None,
                        }
                    )

        return self._build_result("set_material", target, mismatches)

    def _verify_assign_material(self, target: str, change_set: ChangeSet) -> VerificationResult:
        """Verification rules for material assignment to an object slot."""
        mismatches: List[Dict[str, Any]] = []
        expected = change_set.expected_after or {}
        actual = change_set.actual_after or {}

        # 1. Object name check
        exp_obj = expected.get("object_name")
        if exp_obj is not None:
            act_obj = actual.get("object_name")
            if act_obj is None or str(act_obj).strip() != str(exp_obj).strip():
                mismatches.append(
                    {
                        "property": "object_name",
                        "expected": exp_obj,
                        "actual": act_obj,
                        "diff": None,
                    }
                )

        # 2. Slot index check
        exp_slot = expected.get("slot_index")
        if exp_slot is not None:
            act_slot = actual.get("slot_index")
            if act_slot is None or int(act_slot) != int(exp_slot):
                mismatches.append(
                    {
                        "property": "slot_index",
                        "expected": int(exp_slot),
                        "actual": int(act_slot) if act_slot is not None else None,
                        "diff": (
                            int(act_slot) - int(exp_slot)
                            if act_slot is not None
                            else None
                        ),
                    }
                )

        # 3. Material name check
        exp_mat = expected.get("material_name")
        if exp_mat is not None:
            act_mat = actual.get("material_name")
            if act_mat is None or str(act_mat).strip() != str(exp_mat).strip():
                mismatches.append(
                    {
                        "property": "material_name",
                        "expected": str(exp_mat).strip(),
                        "actual": str(act_mat).strip() if act_mat is not None else None,
                        "diff": None,
                    }
                )

        return self._build_result("assign_material", target, mismatches)

    def _build_result(
        self,
        operation: str,
        target_name: str,
        mismatches: List[Dict[str, Any]],
    ) -> VerificationResult:
        """Construct a standardized VerificationResult from collected mismatches."""
        if not mismatches:
            return VerificationResult(
                status=VerificationStatus.PASS,
                operation=operation,
                target_name=target_name,
                mismatches=[],
                summary=f"Verification PASSED for {operation} on '{target_name}'.",
            )

        failed_props = ", ".join(m["property"] for m in mismatches)
        return VerificationResult(
            status=VerificationStatus.FAIL,
            operation=operation,
            target_name=target_name,
            mismatches=mismatches,
            summary=f"Verification FAILED for {operation} on '{target_name}': mismatch in [{failed_props}].",
        )


def build_change_set_from_result(
    tool_name: str,
    arguments: Dict[str, Any],
    result_data: Dict[str, Any],
) -> Optional[ChangeSet]:
    """Construct an M5 ChangeSet from a mutation tool invocation and execution result.

    Derives expected values strictly from tool arguments and operation semantics,
    and extracts actual values from the standardized mutation result snapshot.

    Args:
        tool_name: The name of the executed tool (e.g. 'create_primitive').
        arguments: The arguments dictionary passed to the tool call.
        result_data: The data dictionary returned in ToolResult.data.

    Returns:
        ChangeSet if tool is a verifiable mutation tool, else None.
    """
    if not isinstance(arguments, dict):
        arguments = {}
    if not isinstance(result_data, dict):
        result_data = {}

    name = str(tool_name).strip().lower()

    if name == "create_primitive":
        target_name = str(
            result_data.get("object_name")
            or arguments.get("name")
            or arguments.get("primitive_type")
            or "Primitive"
        )
        expected_after: Dict[str, Any] = {
            "exists": True,
            "primitive_type": str(arguments.get("primitive_type", "")).upper(),
        }
        for prop in ("location", "rotation", "scale"):
            if prop in arguments and arguments[prop] is not None:
                expected_after[prop] = arguments[prop]

        return ChangeSet(
            operation="create",
            target_name=target_name,
            before=None,
            expected_after=expected_after,
            actual_after=dict(result_data),
        )

    elif name == "create_camera":
        target_name = str(
            result_data.get("object_name")
            or arguments.get("name")
            or "Camera"
        )
        expected_after: Dict[str, Any] = {
            "exists": True,
            "type": "CAMERA",
        }
        for prop in ("location", "rotation"):
            if prop in arguments and arguments[prop] is not None:
                expected_after[prop] = arguments[prop]
        if "lens" in arguments and arguments["lens"] is not None:
            expected_after["lens"] = float(arguments["lens"])
        if "make_active" in arguments and arguments["make_active"] is not None:
            expected_after["is_active_camera"] = bool(arguments["make_active"])
        elif arguments.get("make_active") is None:
            expected_after["is_active_camera"] = True

        return ChangeSet(
            operation="create_camera",
            target_name=target_name,
            before=result_data.get("before"),
            expected_after=expected_after,
            actual_after=dict(result_data),
        )

    elif name == "create_light":
        target_name = str(
            result_data.get("object_name")
            or arguments.get("name")
            or "Light"
        )
        expected_after: Dict[str, Any] = {
            "exists": True,
            "type": "LIGHT",
        }
        if "light_type" in arguments and arguments["light_type"] is not None:
            expected_after["light_type"] = str(arguments["light_type"]).upper()
        elif "light_type" not in arguments and result_data.get("created", False):
            expected_after["light_type"] = "POINT"

        for prop in ("location", "rotation"):
            if prop in arguments and arguments[prop] is not None:
                expected_after[prop] = arguments[prop]

        if "energy" in arguments and arguments["energy"] is not None:
            expected_after["energy"] = float(arguments["energy"])

        if "color" in arguments and arguments["color"] is not None:
            raw_c = arguments["color"]
            if isinstance(raw_c, (list, tuple)) and len(raw_c) == 3:
                expected_after["color"] = [max(0.0, min(1.0, float(v))) for v in raw_c]

        return ChangeSet(
            operation="create_light",
            target_name=target_name,
            before=result_data.get("before"),
            expected_after=expected_after,
            actual_after=dict(result_data),
        )

    elif name == "set_shading":
        target_name = str(
            result_data.get("object_name")
            or arguments.get("name")
            or "Unknown"
        )
        expected_after: Dict[str, Any] = {
            "exists": True,
            "shading": str(arguments.get("shading", "")).upper(),
        }
        return ChangeSet(
            operation="set_shading",
            target_name=target_name,
            before=result_data.get("before"),
            expected_after=expected_after,
            actual_after=dict(result_data),
        )

    elif name == "add_modifier":
        target_name = str(
            result_data.get("object_name")
            or arguments.get("name")
            or "Unknown"
        )
        mod_type = str(arguments.get("modifier_type", "")).upper()
        expected_after: Dict[str, Any] = {
            "exists": True,
            "modifier_type": mod_type,
        }
        if mod_type == "BEVEL":
            expected_after["width"] = float(arguments.get("width", 0.05))
            expected_after["segments"] = int(arguments.get("segments", 2))
        elif mod_type == "SUBSURF":
            expected_after["levels"] = int(arguments.get("levels", 1))
        elif mod_type == "BOOLEAN":
            expected_after["operation"] = str(arguments.get("operation", "DIFFERENCE")).upper()
            if "target_object" in arguments and arguments["target_object"] is not None:
                expected_after["target_object"] = str(arguments["target_object"]).strip()

        return ChangeSet(
            operation="add_modifier",
            target_name=target_name,
            before=result_data.get("before"),
            expected_after=expected_after,
            actual_after=dict(result_data),
        )

    elif name == "transform_object":
        target_name = str(
            arguments.get("name")
            or result_data.get("object_name")
            or "Unknown"
        )
        before = result_data.get("before")
        actual_after = result_data.get("actual") or result_data.get("after") or {}
        relative = bool(arguments.get("relative", False) or result_data.get("relative", False))

        expected_after: Dict[str, Any] = {"exists": True}
        for prop in ("location", "rotation", "scale"):
            if prop in arguments and arguments[prop] is not None:
                arg_vec = arguments[prop]
                if relative and isinstance(before, dict) and prop in before and before[prop] is not None:
                    b_vec = before[prop]
                    if prop == "scale":
                        expected_after[prop] = [float(b) * float(d) for b, d in zip(b_vec, arg_vec)]
                    else:
                        expected_after[prop] = [float(b) + float(d) for b, d in zip(b_vec, arg_vec)]
                else:
                    expected_after[prop] = arg_vec

        return ChangeSet(
            operation="transform",
            target_name=target_name,
            before=before,
            expected_after=expected_after,
            actual_after=dict(actual_after) if isinstance(actual_after, dict) else {},
        )

    elif name == "delete_object":
        target_name = str(
            arguments.get("name")
            or result_data.get("object_name")
            or "Unknown"
        )
        return ChangeSet(
            operation="delete",
            target_name=target_name,
            before=result_data.get("previous_state"),
            expected_after={"exists": False, "deleted": True},
            actual_after={
                "exists": result_data.get("exists", False),
                "deleted": result_data.get("deleted", True),
            },
        )

    elif name == "set_material":
        target_name = str(
            result_data.get("material_name")
            or arguments.get("material_name")
            or arguments.get("object_name")
            or "Material"
        )
        expected_after: Dict[str, Any] = {}

        if "base_color" in arguments and arguments["base_color"] is not None:
            raw = arguments["base_color"]
            if isinstance(raw, (list, tuple)):
                if len(raw) == 3:
                    expected_after["base_color"] = [
                        max(0.0, min(1.0, float(raw[0]))),
                        max(0.0, min(1.0, float(raw[1]))),
                        max(0.0, min(1.0, float(raw[2]))),
                        1.0,
                    ]
                elif len(raw) == 4:
                    expected_after["base_color"] = [
                        max(0.0, min(1.0, float(v))) for v in raw
                    ]
                else:
                    expected_after["base_color"] = list(raw)

        if "metallic" in arguments and arguments["metallic"] is not None:
            try:
                expected_after["metallic"] = max(0.0, min(1.0, float(arguments["metallic"])))
            except (ValueError, TypeError):
                expected_after["metallic"] = arguments["metallic"]

        if "roughness" in arguments and arguments["roughness"] is not None:
            try:
                expected_after["roughness"] = max(0.0, min(1.0, float(arguments["roughness"])))
            except (ValueError, TypeError):
                expected_after["roughness"] = arguments["roughness"]

        if "emission_color" in arguments and arguments["emission_color"] is not None:
            raw = arguments["emission_color"]
            if isinstance(raw, (list, tuple)):
                if len(raw) == 3:
                    expected_after["emission_color"] = [
                        max(0.0, min(1.0, float(raw[0]))),
                        max(0.0, min(1.0, float(raw[1]))),
                        max(0.0, min(1.0, float(raw[2]))),
                        1.0,
                    ]
                elif len(raw) == 4:
                    expected_after["emission_color"] = [
                        max(0.0, min(1.0, float(v))) for v in raw
                    ]
                else:
                    expected_after["emission_color"] = list(raw)

        if "emission_strength" in arguments and arguments["emission_strength"] is not None:
            try:
                expected_after["emission_strength"] = max(0.0, float(arguments["emission_strength"]))
            except (ValueError, TypeError):
                expected_after["emission_strength"] = arguments["emission_strength"]

        if "alpha" in arguments and arguments["alpha"] is not None:
            try:
                expected_after["alpha"] = max(0.0, min(1.0, float(arguments["alpha"])))
            except (ValueError, TypeError):
                expected_after["alpha"] = arguments["alpha"]

        actual_snap = result_data.get("actual") or result_data.get("after") or result_data
        return ChangeSet(
            operation="set_material",
            target_name=target_name,
            before=result_data.get("before"),
            expected_after=expected_after,
            actual_after=dict(actual_snap) if isinstance(actual_snap, dict) else {},
        )

    elif name == "assign_material":
        target_name = str(
            arguments.get("object_name")
            or result_data.get("object_name")
            or "Object"
        )
        expected_after = {
            "object_name": arguments.get("object_name") or result_data.get("object_name"),
            "material_name": arguments.get("material_name") or result_data.get("material_name"),
            "slot_index": (
                arguments.get("slot_index")
                if arguments.get("slot_index") is not None
                else (result_data.get("slot_index", 0) if result_data.get("slot_index") is not None else 0)
            ),
        }
        actual_snap = result_data.get("actual") or result_data.get("after") or result_data
        return ChangeSet(
            operation="assign_material",
            target_name=target_name,
            before=result_data.get("before"),
            expected_after=expected_after,
            actual_after=dict(actual_snap) if isinstance(actual_snap, dict) else {},
        )

    return None
