"""Deterministic execution gating and approval policy for tool execution.

Zero Blender (bpy) dependencies. Pure Python.
"""

from dataclasses import dataclass
from enum import Enum
import time
from typing import Any, Dict, Optional, Union
import uuid

from core.types import RiskLevel
from agent.models import ToolCall
from tools.base import BaseTool


class ApprovalDecision(str, Enum):
    """Outcome of evaluating a tool call against the approval policy."""

    AUTO_APPROVE = "AUTO_APPROVE"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class InvalidApprovalError(ValueError):
    """Raised when an approval decision or ID is invalid or mismatched."""

    pass


class NoPendingApprovalError(RuntimeError):
    """Raised when an approve/reject action is invoked without an active pending approval."""

    pass


@dataclass(frozen=True)
class PendingApproval:
    """Immutable record of a tool call awaiting explicit user approval.

    Freezes the tool call and arguments at the moment of request to guarantee
    parameters cannot be tampered with or modified prior to dispatch.
    """

    approval_id: str
    turn_id: str
    tool_call: ToolCall
    tool_name: str
    risk_level: RiskLevel
    human_readable_description: str
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        """Serialize pending approval to a deterministic dictionary."""
        return {
            "approval_id": self.approval_id,
            "created_at": round(self.created_at, 4),
            "human_readable_description": self.human_readable_description,
            "risk_level": self.risk_level.value,
            "tool_call": self.tool_call.to_dict(),
            "tool_name": self.tool_name,
            "turn_id": self.turn_id,
        }


class ApprovalPolicy:
    """Deterministic security gate evaluating tool execution risk.

    Policy Rules:
    - READ_ONLY -> AUTO_APPROVE
    - LOW       -> AUTO_APPROVE
    - MEDIUM    -> REQUIRE_APPROVAL
    - HIGH      -> REQUIRE_APPROVAL
    - CRITICAL  -> REQUIRE_APPROVAL
    """

    _GATED_RISK_LEVELS = {
        RiskLevel.MEDIUM,
        RiskLevel.HIGH,
        RiskLevel.CRITICAL,
    }

    def evaluate(
        self,
        tool: Optional[BaseTool],
        tool_call: ToolCall,
    ) -> ApprovalDecision:
        """Evaluate whether a tool call requires explicit user approval.

        Args:
            tool: BaseTool instance from ToolRegistry, or None if unregistered.
            tool_call: Requested ToolCall from LLM provider.

        Returns:
            ApprovalDecision.REQUIRE_APPROVAL or ApprovalDecision.AUTO_APPROVE.
        """
        # If tool is missing from registry, treat as high risk
        if tool is None:
            return ApprovalDecision.REQUIRE_APPROVAL

        if tool is not None and hasattr(tool, "get_risk_level"):
            try:
                risk = tool.get_risk_level(tool_call.arguments or {})
            except Exception:
                risk = getattr(tool, "risk_level", RiskLevel.HIGH)
        else:
            risk = getattr(tool, "risk_level", RiskLevel.HIGH)
        if isinstance(risk, str):
            try:
                risk = RiskLevel(risk)
            except ValueError:
                risk = RiskLevel.HIGH

        if risk in self._GATED_RISK_LEVELS:
            return ApprovalDecision.REQUIRE_APPROVAL

        return ApprovalDecision.AUTO_APPROVE

    def create_human_description(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> str:
        """Generate a concise, human-readable action description for UI display."""
        args = arguments or {}

        if tool_name == "delete_object":
            obj_name = args.get("name", "").strip()
            return f'Delete "{obj_name}"' if obj_name else "Delete object"

        if tool_name == "create_primitive":
            prim_type = args.get("primitive_type", "object").capitalize()
            return f"Create {prim_type}"

        if tool_name == "create_camera":
            cam_name = args.get("name", "").strip()
            return f'Create camera "{cam_name}"' if cam_name else "Create camera"

        if tool_name == "create_light":
            l_name = args.get("name", "").strip()
            l_type = args.get("light_type", "light").lower()
            return f'Create {l_type} "{l_name}"' if l_name else f"Create {l_type}"

        if tool_name == "set_shading":
            obj_name = args.get("name", "").strip()
            shading = args.get("shading", "smooth").lower()
            return f'Set {shading} shading on "{obj_name}"' if obj_name else f"Set {shading} shading"

        if tool_name == "add_modifier":
            obj_name = args.get("name", "").strip()
            mod_type = args.get("modifier_type", "modifier").capitalize()
            return f'Add {mod_type} modifier to "{obj_name}"' if obj_name else f"Add {mod_type} modifier"

        if tool_name == "transform_object":
            obj_name = args.get("name", "").strip()
            return f'Transform "{obj_name}"' if obj_name else "Transform object"

        if tool_name == "propose_plan":
            title = args.get("title", "").strip()
            return f'Propose plan "{title}"' if title else "Propose plan"

        formatted_args = ", ".join(f"{k}={v}" for k, v in sorted(args.items()))
        return f"{tool_name}({formatted_args})" if formatted_args else tool_name

    def create_pending_approval(
        self,
        turn_id: str,
        tool: Optional[BaseTool],
        tool_call: ToolCall,
    ) -> PendingApproval:
        """Construct a secure PendingApproval container for a gated tool call."""
        approval_id = f"appr_{uuid.uuid4().hex[:10]}"
        tool_name = tool_call.tool_name
        if tool is not None and hasattr(tool, "get_risk_level"):
            try:
                risk = tool.get_risk_level(tool_call.arguments or {})
            except Exception:
                risk = getattr(tool, "risk_level", RiskLevel.HIGH)
        else:
            risk = getattr(tool, "risk_level", RiskLevel.HIGH) if tool else RiskLevel.HIGH
        if isinstance(risk, str):
            try:
                risk = RiskLevel(risk)
            except ValueError:
                risk = RiskLevel.HIGH

        desc = self.create_human_description(tool_name, tool_call.arguments)

        # Deep freeze tool call to guarantee parameter integrity
        frozen_call = ToolCall(
            call_id=tool_call.call_id,
            tool_name=tool_call.tool_name,
            arguments=dict(tool_call.arguments) if tool_call.arguments else {},
        )

        return PendingApproval(
            approval_id=approval_id,
            turn_id=turn_id,
            tool_call=frozen_call,
            tool_name=tool_name,
            risk_level=risk,
            human_readable_description=desc,
            created_at=time.time(),
        )
