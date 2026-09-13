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
        elif op == "transform":
            return self._verify_transform(target, change_set)
        elif op == "delete":
            return self._verify_delete(target, change_set)
        else:
            return VerificationResult(
                status=VerificationStatus.FAIL,
                operation=op,
                target_name=target,
                mismatches=[
                    {
                        "property": "operation",
                        "expected": "One of ['create', 'transform', 'delete']",
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

    return None
