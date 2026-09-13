"""BlenderAdapter facade for centralized, thread-safe Blender API access.

Ensures main-thread execution, encapsulates readers, and normalizes
Blender exceptions into standardized ToolResult objects.
"""

import threading
from typing import Any, Dict, Optional

from core.types import ToolResult
from adapter.readers.scene_reader import SceneReader
from adapter.readers.selection_reader import SelectionReader
from adapter.readers.object_reader import ObjectReader, ObjectNotFoundError
from adapter.readers.material_reader import (
    MaterialReader,
    MaterialNotFoundError,
    MaterialSlotError,
)
from adapter.readers.mesh_reader import (
    MeshReader,
    InvalidMeshDataTypeError,
)
from adapter.mutators import (
    PrimitiveMutator,
    InvalidPrimitiveTypeError,
    TransformMutator,
    DeleteMutator,
)


class ThreadSafetyViolationError(RuntimeError):
    """Raised when BlenderAdapter is accessed from a background worker thread."""


def assert_main_thread() -> None:
    """Lightweight guard ensuring bpy access occurs solely on the main thread."""
    if threading.current_thread() is not threading.main_thread():
        raise ThreadSafetyViolationError(
            f"BlenderAdapter accessed from background thread '{threading.current_thread().name}'. "
            "All bpy interactions must occur on the main thread."
        )


class BlenderAdapter:
    """Centralized adapter for interacting with the Blender runtime."""

    def __init__(self):
        self._scene_reader = SceneReader()
        self._selection_reader = SelectionReader()
        self._object_reader = ObjectReader()
        self._material_reader = MaterialReader()
        self._mesh_reader = MeshReader()

    def inspect_scene(self) -> ToolResult:
        """Inspect the active scene summary.

        Returns:
            ToolResult conforming to inspect_scene contract.
        """
        assert_main_thread()
        tool_name = "inspect_scene"
        try:
            data = self._scene_reader.read()
            return ToolResult.ok(tool_name, data)
        except Exception as exc:
            return ToolResult.fail(
                tool=tool_name,
                error_type="CONTEXT_UNAVAILABLE",
                message=f"Failed to inspect scene: {str(exc)}",
                details={"exception": type(exc).__name__},
            )

    def inspect_selection(self) -> ToolResult:
        """Inspect the current selection state and mode.

        Returns:
            ToolResult conforming to inspect_selection contract.
        """
        assert_main_thread()
        tool_name = "inspect_selection"
        try:
            data = self._selection_reader.read()
            return ToolResult.ok(tool_name, data)
        except Exception as exc:
            return ToolResult.fail(
                tool=tool_name,
                error_type="CONTEXT_UNAVAILABLE",
                message=f"Failed to inspect selection: {str(exc)}",
                details={"exception": type(exc).__name__},
            )

    def inspect_object(self, name: str, include_evaluated: bool = False) -> ToolResult:
        """Inspect deep properties of an object by name.

        Args:
            name: Name of the object.
            include_evaluated: Whether to evaluate modifiers/depsgraph.

        Returns:
            ToolResult conforming to inspect_object contract.
        """
        assert_main_thread()
        tool_name = "inspect_object"

        if not name or not isinstance(name, str):
            return ToolResult.fail(
                tool=tool_name,
                error_type="INVALID_ARGUMENT",
                message="Argument 'name' must be a non-empty string.",
                details={"provided_name": str(name)},
            )

        try:
            data = self._object_reader.read(name, include_evaluated=include_evaluated)
            return ToolResult.ok(tool_name, data)
        except ObjectNotFoundError as not_found:
            return ToolResult.fail(
                tool=tool_name,
                error_type="OBJECT_NOT_FOUND",
                message=str(not_found),
                details={"queried_name": name},
            )
        except Exception as exc:
            return ToolResult.fail(
                tool=tool_name,
                error_type="ADAPTER_INTERNAL_ERROR",
                message=f"Unexpected error inspecting object '{name}': {str(exc)}",
                details={"exception": type(exc).__name__},
            )

    def inspect_material(
        self,
        material_name: Optional[str] = None,
        object_name: Optional[str] = None,
        slot_index: Optional[int] = 0,
    ) -> ToolResult:
        """Inspect material properties via direct name or object slot binding.

        Returns:
            ToolResult conforming to inspect_material contract.
        """
        assert_main_thread()
        tool_name = "inspect_material"

        try:
            data = self._material_reader.read(
                material_name=material_name,
                object_name=object_name,
                slot_index=slot_index,
            )
            return ToolResult.ok(tool_name, data)
        except ObjectNotFoundError as not_found:
            return ToolResult.fail(
                tool=tool_name,
                error_type="OBJECT_NOT_FOUND",
                message=str(not_found),
                details={"object_name": object_name},
            )
        except MaterialNotFoundError as not_found:
            return ToolResult.fail(
                tool=tool_name,
                error_type="MATERIAL_NOT_FOUND",
                message=str(not_found),
                details={"material_name": material_name},
            )
        except MaterialSlotError as slot_err:
            return ToolResult.fail(
                tool=tool_name,
                error_type="SLOT_INDEX_OUT_OF_RANGE",
                message=str(slot_err),
                details={"object_name": object_name, "slot_index": slot_index},
            )
        except ValueError as val_err:
            return ToolResult.fail(
                tool=tool_name,
                error_type="INVALID_ARGUMENT",
                message=str(val_err),
                details={},
            )
        except Exception as exc:
            return ToolResult.fail(
                tool=tool_name,
                error_type="ADAPTER_INTERNAL_ERROR",
                message=f"Unexpected error inspecting material: {str(exc)}",
                details={"exception": type(exc).__name__},
            )

    def inspect_mesh(self, object_name: str) -> ToolResult:
        """Inspect mesh geometry metrics, topology breakdown, and world bounding box.

        Returns:
            ToolResult conforming to inspect_mesh contract.
        """
        assert_main_thread()
        tool_name = "inspect_mesh"

        if not object_name or not isinstance(object_name, str):
            return ToolResult.fail(
                tool=tool_name,
                error_type="INVALID_ARGUMENT",
                message="Argument 'object_name' must be a non-empty string.",
                details={"provided": str(object_name)},
            )

        try:
            data = self._mesh_reader.read(object_name=object_name)
            return ToolResult.ok(tool_name, data)
        except ObjectNotFoundError as not_found:
            return ToolResult.fail(
                tool=tool_name,
                error_type="OBJECT_NOT_FOUND",
                message=str(not_found),
                details={"object_name": object_name},
            )
        except InvalidMeshDataTypeError as type_err:
            return ToolResult.fail(
                tool=tool_name,
                error_type="INVALID_DATA_TYPE",
                message=str(type_err),
                details={"object_name": object_name},
            )
        except Exception as exc:
            return ToolResult.fail(
                tool=tool_name,
                error_type="ADAPTER_INTERNAL_ERROR",
                message=f"Unexpected error inspecting mesh '{object_name}': {str(exc)}",
                details={"exception": type(exc).__name__},
            )

    def create_primitive(
        self,
        primitive_type: str,
        name: Optional[str] = None,
        location: Optional[Any] = None,
        rotation: Optional[Any] = None,
        scale: Optional[Any] = None,
        size: Optional[float] = None,
    ) -> ToolResult:
        """Create a new geometric primitive (CUBE, SPHERE, PLANE) in the scene.

        Returns:
            ToolResult conforming to create_primitive contract.
        """
        assert_main_thread()
        tool_name = "create_primitive"

        try:
            data = PrimitiveMutator.create(
                primitive_type=primitive_type,
                name=name,
                location=location,
                rotation=rotation,
                scale=scale,
                size=size,
            )
            return ToolResult.ok(tool_name, data)
        except InvalidPrimitiveTypeError as type_err:
            return ToolResult.fail(
                tool=tool_name,
                error_type="INVALID_PRIMITIVE_TYPE",
                message=str(type_err),
                details={"primitive_type": str(primitive_type)},
            )
        except ValueError as val_err:
            return ToolResult.fail(
                tool=tool_name,
                error_type="INVALID_ARGUMENT",
                message=str(val_err),
                details={"error": str(val_err)},
            )
        except Exception as exc:
            return ToolResult.fail(
                tool=tool_name,
                error_type="ADAPTER_INTERNAL_ERROR",
                message=f"Unexpected error creating primitive '{primitive_type}': {str(exc)}",
                details={"exception": type(exc).__name__},
            )

    def transform_object(
        self,
        name: str,
        location: Optional[Any] = None,
        rotation: Optional[Any] = None,
        scale: Optional[Any] = None,
        relative: bool = False,
    ) -> ToolResult:
        """Transform object location, rotation, or scale.

        Returns:
            ToolResult conforming to transform_object contract.
        """
        assert_main_thread()
        tool_name = "transform_object"

        try:
            data = TransformMutator.transform(
                name=name,
                location=location,
                rotation=rotation,
                scale=scale,
                relative=relative,
            )
            return ToolResult.ok(tool_name, data)
        except ObjectNotFoundError as not_found:
            return ToolResult.fail(
                tool=tool_name,
                error_type="OBJECT_NOT_FOUND",
                message=str(not_found),
                details={"object_name": name},
            )
        except ValueError as val_err:
            return ToolResult.fail(
                tool=tool_name,
                error_type="INVALID_ARGUMENT",
                message=str(val_err),
                details={"object_name": name},
            )
        except Exception as exc:
            return ToolResult.fail(
                tool=tool_name,
                error_type="ADAPTER_INTERNAL_ERROR",
                message=f"Unexpected error transforming object '{name}': {str(exc)}",
                details={"exception": type(exc).__name__},
            )

    def delete_object(self, name: str) -> ToolResult:
        """Delete object by exact name.

        Returns:
            ToolResult conforming to delete_object contract.
        """
        assert_main_thread()
        tool_name = "delete_object"

        try:
            data = DeleteMutator.delete(name=name)
            return ToolResult.ok(tool_name, data)
        except ObjectNotFoundError as not_found:
            return ToolResult.fail(
                tool=tool_name,
                error_type="OBJECT_NOT_FOUND",
                message=str(not_found),
                details={"object_name": name},
            )
        except ValueError as val_err:
            return ToolResult.fail(
                tool=tool_name,
                error_type="INVALID_ARGUMENT",
                message=str(val_err),
                details={"object_name": name},
            )
        except Exception as exc:
            return ToolResult.fail(
                tool=tool_name,
                error_type="ADAPTER_INTERNAL_ERROR",
                message=f"Unexpected error deleting object '{name}': {str(exc)}",
                details={"exception": type(exc).__name__},
            )
