"""Deterministic visual scene verification engine.

Uses active 3D Viewport screenshots and multimodal AI providers to evaluate
high-level visual expectations without overriding deterministic RNA semantic verification.

Zero Blender (bpy) dependencies. Pure Python standard library only.
"""

from dataclasses import dataclass
from enum import Enum
import json
import re
import threading
from typing import Any, Callable, Dict, Optional

from agent.context_builder import ImageResolutionError, ProviderRequestContext
from agent.models import (
    ChatMessage,
    ProviderError,
    ProviderErrorType,
    ProviderStreamEvent,
    Role,
    TextDelta,
)
from agent.provider import BaseProvider
from core.change_set import VerificationResult


class VisualVerificationStatus(str, Enum):
    """Deterministic outcome states for visual verification."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class VisualVerificationResult:
    """Structured result of a visual verification evaluation.

    Immutable container holding verification status, concise rationale,
    associated image reference, and optional diagnostics.
    Does NOT store raw image bytes or base64 data URIs.
    """

    status: VisualVerificationStatus
    reason: str
    expected_description: str
    image_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    @property
    def passed(self) -> bool:
        """True only if visually verified as PASS."""
        return self.status == VisualVerificationStatus.PASS

    def to_dict(self) -> Dict[str, Any]:
        """Serialize verification result to a deterministic, hygiene-safe dictionary."""
        d: Dict[str, Any] = {
            "status": self.status.value,
            "passed": self.passed,
            "reason": self.reason,
            "expected_description": self.expected_description,
        }
        if self.image_id:
            d["image_id"] = self.image_id
        if self.details:
            d["details"] = dict(self.details)
        return d


class VisualResultParser:
    """Safely extracts and validates structured visual verification JSON from model text."""

    @classmethod
    def parse(
        cls,
        raw_text: str,
        expected_description: str,
        image_id: Optional[str] = None,
    ) -> VisualVerificationResult:
        """Parse raw model output into a valid VisualVerificationResult.

        Safely handles malformed JSON, markdown fences, partial output,
        and unrecognized status strings without throwing unhandled exceptions.
        """
        if not raw_text or not raw_text.strip():
            return VisualVerificationResult(
                status=VisualVerificationStatus.UNCERTAIN,
                reason="Visual verification model returned an empty response.",
                expected_description=expected_description,
                image_id=image_id,
                details={"error_type": "EMPTY_MODEL_OUTPUT"},
            )

        cleaned = raw_text.strip()

        # 1. Strip markdown code fences if wrapped in ```json ... ``` or ``` ... ```
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if fence_match:
            candidate_str = fence_match.group(1).strip()
        else:
            # Look for outermost curly braces
            start_idx = cleaned.find("{")
            end_idx = cleaned.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                candidate_str = cleaned[start_idx : end_idx + 1]
            else:
                candidate_str = cleaned

        # 2. Attempt JSON deserialization
        try:
            parsed = json.loads(candidate_str)
        except Exception as exc:
            return VisualVerificationResult(
                status=VisualVerificationStatus.UNCERTAIN,
                reason=f"Failed to parse visual verification response as JSON: {exc}",
                expected_description=expected_description,
                image_id=image_id,
                details={"error_type": "MALFORMED_JSON", "raw_output": raw_text[:200]},
            )

        if not isinstance(parsed, dict):
            return VisualVerificationResult(
                status=VisualVerificationStatus.UNCERTAIN,
                reason="Model output did not parse into a JSON object dictionary.",
                expected_description=expected_description,
                image_id=image_id,
                details={"error_type": "INVALID_JSON_SHAPE"},
            )

        # 3. Validate status field
        raw_status = str(parsed.get("status", "")).strip().upper()
        if raw_status == "PASS":
            status = VisualVerificationStatus.PASS
        elif raw_status == "FAIL":
            status = VisualVerificationStatus.FAIL
        elif raw_status == "UNCERTAIN":
            status = VisualVerificationStatus.UNCERTAIN
        else:
            return VisualVerificationResult(
                status=VisualVerificationStatus.UNCERTAIN,
                reason=f"Model returned unrecognized verification status '{raw_status}'. Expected PASS, FAIL, or UNCERTAIN.",
                expected_description=expected_description,
                image_id=image_id,
                details={"error_type": "INVALID_STATUS_VALUE", "parsed_json": parsed},
            )

        # 4. Extract reason string
        reason = str(parsed.get("reason", "")).strip() or "No rationale provided by model."

        return VisualVerificationResult(
            status=status,
            reason=reason,
            expected_description=expected_description,
            image_id=image_id,
        )


VISUAL_VERIFIER_SYSTEM_PROMPT = (
    "You are an automated visual scene verifier for a 3D Blender workspace.\n"
    "Your objective is to inspect the attached viewport screenshot and determine "
    "whether the visible 3D scene conforms to the user's expected visual description.\n\n"
    "Respond STRICTLY in valid JSON format with the following structure:\n"
    "{\n"
    '  "status": "PASS" | "FAIL" | "UNCERTAIN",\n'
    '  "reason": "<short concise factual explanation>"\n'
    "}\n\n"
    "Decision Rules:\n"
    "- \"PASS\": The expected objects, materials, colors, transformations, or features are clearly apparent in the viewport.\n"
    "- \"FAIL\": The viewport image clearly contradicts the expected description (e.g. object completely missing, wrong color, clipping or occlusion error).\n"
    "- \"UNCERTAIN\": The camera angle, lighting, distance, or occlusion makes it ambiguous or impossible to verify with high certainty.\n\n"
    "Do NOT output any markdown commentary, greetings, or text outside of the JSON object."
)


class VisualVerifier:
    """Evaluates rendered Blender viewport state against natural language expectations."""

    def __init__(
        self,
        provider: Optional[BaseProvider] = None,
        adapter: Optional[Any] = None,
        image_resolver: Optional[Callable[[str], Optional[bytes]]] = None,
    ):
        self.provider = provider
        self.adapter = adapter
        self._image_resolver = image_resolver

    def _resolve_png_bytes(self, image_id: str) -> Optional[bytes]:
        """Resolve PNG bytes via configured image resolver or adapter."""
        if self._image_resolver:
            try:
                return self._image_resolver(image_id)
            except Exception:
                return None
        if self.adapter and hasattr(self.adapter, "get_viewport_screenshot"):
            try:
                return self.adapter.get_viewport_screenshot(image_id)
            except Exception:
                return None
        return None

    @classmethod
    def build_verification_context(
        cls,
        expected_description: str,
        image_id: str,
        png_bytes: bytes,
    ) -> ProviderRequestContext:
        """Construct a normalized ProviderRequestContext for visual verification query."""
        user_msg = ChatMessage(
            role=Role.USER,
            content=(
                f"Expected visual description:\n{expected_description}\n\n"
                "Evaluate the viewport screenshot against this expectation and return the JSON decision."
            ),
            image_id=image_id,
        )
        return ProviderRequestContext(
            messages=[user_msg],
            tools=[],
            system_prompt=VISUAL_VERIFIER_SYSTEM_PROMPT,
            images={image_id: png_bytes},
        )

    def verify(
        self,
        expected_description: str,
        image_id: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        turn_id: str = "visual_verify",
    ) -> VisualVerificationResult:
        """Execute visual verification for an expected visual state.

        Args:
            expected_description: Target visual description to evaluate.
            image_id: Optional reference to an existing viewport screenshot. If omitted,
                      captures the active 3D Viewport on the main thread.
            cancel_event: Optional threading.Event to abort in-flight queries.
            turn_id: Traceable turn identifier.

        Returns:
            VisualVerificationResult with PASS, FAIL, or UNCERTAIN status and concise reason.

        Raises:
            ImageResolutionError: If a specific image_id was provided but cannot be resolved.
        """
        desc = expected_description.strip() if expected_description else ""
        if not desc:
            return VisualVerificationResult(
                status=VisualVerificationStatus.UNCERTAIN,
                reason="Visual verification skipped: Expected visual description is empty.",
                expected_description="",
                details={"error_type": "INVALID_ARGUMENTS"},
            )

        # 1. Provider capability verification
        if self.provider is None:
            return VisualVerificationResult(
                status=VisualVerificationStatus.UNCERTAIN,
                reason="Visual verification skipped: No AI provider is configured.",
                expected_description=desc,
                details={"error_type": "NO_PROVIDER"},
            )

        supports_multimodal = bool(getattr(self.provider, "supports_multimodal", False))
        if not supports_multimodal:
            return VisualVerificationResult(
                status=VisualVerificationStatus.UNCERTAIN,
                reason="Visual verification skipped: AI provider does not support multimodal vision.",
                expected_description=desc,
                details={"error_type": "PROVIDER_UNSUPPORTED"},
            )

        # 2. Viewport screenshot acquisition
        target_image_id = image_id
        if not target_image_id:
            if not self.adapter or not hasattr(self.adapter, "capture_viewport"):
                return VisualVerificationResult(
                    status=VisualVerificationStatus.UNCERTAIN,
                    reason="Visual verification failed: Adapter unavailable for viewport capture.",
                    expected_description=desc,
                    details={"error_type": "VIEWPORT_UNAVAILABLE"},
                )
            try:
                cap_res = self.adapter.capture_viewport()
            except Exception as exc:
                return VisualVerificationResult(
                    status=VisualVerificationStatus.UNCERTAIN,
                    reason=f"Visual verification failed: Viewport capture raised exception: {exc}",
                    expected_description=desc,
                    details={"error_type": "VIEWPORT_UNAVAILABLE"},
                )

            if not cap_res.success:
                err_msg = cap_res.error.message if cap_res.error else "Unknown capture failure"
                return VisualVerificationResult(
                    status=VisualVerificationStatus.UNCERTAIN,
                    reason=f"Visual verification failed: Viewport capture failed ({err_msg}).",
                    expected_description=desc,
                    details={"error_type": "VIEWPORT_UNAVAILABLE"},
                )
            target_image_id = cap_res.data.get("image_id")

        if not target_image_id:
            return VisualVerificationResult(
                status=VisualVerificationStatus.UNCERTAIN,
                reason="Visual verification failed: No image_id produced by capture_viewport.",
                expected_description=desc,
                details={"error_type": "VIEWPORT_UNAVAILABLE"},
            )

        # 3. Resolve raw PNG bytes
        png_bytes = self._resolve_png_bytes(target_image_id)
        if not png_bytes:
            raise ImageResolutionError(
                image_id=target_image_id,
                message=f"Visual verification failed: Image '{target_image_id}' not found in in-memory cache.",
            )

        # 4. Construct verification request context
        req_context = self.build_verification_context(
            expected_description=desc,
            image_id=target_image_id,
            png_bytes=png_bytes,
        )

        # 5. Query multimodal provider
        text_parts = []
        try:
            for event in self.provider.stream_chat(
                context=req_context,
                turn_id=turn_id,
                cancel_event=cancel_event,
            ):
                if cancel_event and cancel_event.is_set():
                    return VisualVerificationResult(
                        status=VisualVerificationStatus.UNCERTAIN,
                        reason="Visual verification cancelled by user.",
                        expected_description=desc,
                        image_id=target_image_id,
                        details={"error_type": "CANCELLED"},
                    )

                if isinstance(event, TextDelta):
                    text_parts.append(event.text)
                elif isinstance(event, ProviderError):
                    if event.type == ProviderErrorType.PROVIDER_UNSUPPORTED:
                        return VisualVerificationResult(
                            status=VisualVerificationStatus.UNCERTAIN,
                            reason="AI provider does not support multimodal vision.",
                            expected_description=desc,
                            image_id=target_image_id,
                            details={"error_type": "PROVIDER_UNSUPPORTED"},
                        )
                    elif event.type == ProviderErrorType.IMAGE_NOT_FOUND:
                        raise ImageResolutionError(
                            image_id=target_image_id,
                            message=event.message,
                        )
                    else:
                        return VisualVerificationResult(
                            status=VisualVerificationStatus.UNCERTAIN,
                            reason=f"Provider stream error during visual verification: {event.message}",
                            expected_description=desc,
                            image_id=target_image_id,
                            details={
                                "error_type": event.type.value if hasattr(event.type, "value") else str(event.type),
                                "details": event.details,
                            },
                        )
        except ImageResolutionError:
            raise
        except Exception as exc:
            return VisualVerificationResult(
                status=VisualVerificationStatus.UNCERTAIN,
                reason=f"Unexpected error communicating with provider: {exc}",
                expected_description=desc,
                image_id=target_image_id,
                details={"error_type": "PROVIDER_EXCEPTION"},
            )

        # 6. Parse structured decision
        accumulated_text = "".join(text_parts)
        return VisualResultParser.parse(
            raw_text=accumulated_text,
            expected_description=desc,
            image_id=target_image_id,
        )

    def verify_after_mutation(
        self,
        semantic_result: VerificationResult,
        expected_description: str,
        image_id: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        turn_id: str = "visual_verify",
    ) -> VisualVerificationResult:
        """Execute visual verification only after semantic verification has passed.

        Enforces semantic-first verification hierarchy. If RNA semantic verification
        failed, visual verification is never executed and returns UNCERTAIN.
        """
        if not semantic_result.passed:
            return VisualVerificationResult(
                status=VisualVerificationStatus.UNCERTAIN,
                reason=f"Visual verification skipped: Semantic verification failed ({semantic_result.summary}).",
                expected_description=expected_description,
                image_id=image_id,
                details={
                    "error_type": "SEMANTIC_VERIFICATION_FAILED",
                    "semantic_summary": semantic_result.summary,
                    "mismatches": semantic_result.mismatches,
                },
            )

        return self.verify(
            expected_description=expected_description,
            image_id=image_id,
            cancel_event=cancel_event,
            turn_id=turn_id,
        )

