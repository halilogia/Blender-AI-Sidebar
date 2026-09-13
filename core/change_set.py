"""Data models for mutation ChangeSets and deterministic verification.

Zero Blender (bpy) dependencies. Pure Python standard library only.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class VerificationStatus(str, Enum):
    """Result status of verifying a mutation change set."""

    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class ChangeSet:
    """Immutable representation of state before, expected, and actual after a mutation."""

    operation: str
    target_name: str
    before: Optional[Dict[str, Any]] = None
    expected_after: Dict[str, Any] = field(default_factory=dict)
    actual_after: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize ChangeSet to a deterministic JSON-compatible dictionary."""
        return {
            "operation": self.operation,
            "target_name": self.target_name,
            "before": self.before,
            "expected_after": self.expected_after,
            "actual_after": self.actual_after,
        }


@dataclass(frozen=True)
class VerificationResult:
    """Deterministic outcome of evaluating a ChangeSet against verification rules."""

    status: VerificationStatus
    operation: str
    target_name: str
    mismatches: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""

    @property
    def passed(self) -> bool:
        """Convenience property indicating whether verification succeeded."""
        return self.status == VerificationStatus.PASS

    def to_dict(self) -> Dict[str, Any]:
        """Serialize VerificationResult to a deterministic JSON-compatible dictionary."""
        return {
            "status": self.status.value,
            "passed": self.passed,
            "operation": self.operation,
            "target_name": self.target_name,
            "mismatches": [dict(m) for m in self.mismatches],
            "summary": self.summary,
        }
