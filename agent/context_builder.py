"""ContextBuilder for assembling provider-ready request contexts.

Combines system prompt, conversation history, and tool schemas into
a normalized ProviderRequestContext with deterministic context size guards.
Zero Blender (bpy) dependencies. Pure Python.
"""

from dataclasses import dataclass
import json
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from agent.models import ChatMessage, Conversation, Role, ToolCall
from agent.tool_mapper import OpenAICompatibleToolMapper
from tools.base import BaseTool

MAX_CONTEXT_CHARS: int = 15000

DEFAULT_SYSTEM_PROMPT = (
    "You are an AI agent operating inside Blender.\n"
    "Use the available tools to inspect and safely modify Blender state.\n"
    "Do not invent Blender state.\n"
    "When information about the current scene/object/material/mesh is required, "
    "use the corresponding inspection tool instead of guessing.\n"
    "When requested to create primitives, transform objects, or delete objects, "
    "use the corresponding safe mutation tools.\n"
    "After executing tools, explain the result clearly to the user."
)


class ImageResolutionError(ValueError):
    """Raised when an image_id referenced in a message cannot be resolved to in-memory bytes."""

    def __init__(self, image_id: str, message: Optional[str] = None):
        self.image_id = image_id
        super().__init__(
            message
            or f"Failed to resolve image bytes for image_id '{image_id}'. Image not found or cache expired."
        )


@dataclass(frozen=True)
class ProviderRequestContext:
    """Normalized internal container for a provider request ready for dispatch.

    Immutable dataclass. Contains internal ChatMessage instances, mapped tool definitions,
    effective system prompt, and optional in-memory image attachments.
    Does NOT produce raw HTTP JSON body.
    """

    messages: Tuple[ChatMessage, ...]
    tools: Tuple[Dict[str, Any], ...]
    system_prompt: str
    images: Mapping[str, bytes]

    def __init__(
        self,
        messages: Iterable[ChatMessage],
        tools: Iterable[Dict[str, Any]],
        system_prompt: str,
        images: Optional[Mapping[str, bytes]] = None,
    ):
        object.__setattr__(self, "messages", tuple(messages))
        object.__setattr__(self, "tools", tuple(tools))
        object.__setattr__(self, "system_prompt", str(system_prompt))
        object.__setattr__(self, "images", dict(images) if images else {})

    def to_dict(self) -> Dict[str, Any]:
        """Serialize context to a deterministic dictionary."""
        d: Dict[str, Any] = {
            "system_prompt": self.system_prompt,
            "messages": [m.to_dict() for m in self.messages],
            "tools": list(self.tools),
        }
        if self.images:
            d["image_ids"] = sorted(list(self.images.keys()))
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProviderRequestContext":
        """Deserialize context from a dictionary."""
        return cls(
            messages=[ChatMessage.from_dict(m) for m in data.get("messages", [])],
            tools=data.get("tools", []),
            system_prompt=data.get("system_prompt", ""),
            images={},
        )


def _calculate_messages_chars(messages: Sequence[ChatMessage]) -> int:
    """Calculate the total character count across all messages."""
    total = 0
    for m in messages:
        if m.content:
            total += len(m.content)
        if m.tool_calls:
            for tc in m.tool_calls:
                total += len(tc.call_id) + len(tc.tool_name)
                total += len(json.dumps(tc.arguments))
    return total


class ContextBuilder:
    """Assembles provider-ready context from conversation, system prompt, and tools."""

    @classmethod
    def build(
        cls,
        conversation: Optional[Conversation] = None,
        system_prompt: Optional[str] = None,
        tools: Optional[Iterable[Union[BaseTool, Dict[str, Any]]]] = None,
        max_context_chars: int = MAX_CONTEXT_CHARS,
        image_resolver: Optional[Callable[[str], Optional[bytes]]] = None,
        images: Optional[Mapping[str, bytes]] = None,
        compact_threshold: Optional[int] = None,
    ) -> ProviderRequestContext:
        """Construct a ProviderRequestContext respecting character safety cap.

        Args:
            conversation: Active Conversation instance or None.
            system_prompt: Custom system prompt or None (uses DEFAULT_SYSTEM_PROMPT).
            tools: Registered tools or schemas to map to OpenAI function format.
            max_context_chars: Context safety limit in characters (default 15,000).
            image_resolver: Optional callable (image_id -> raw bytes) to fetch in-memory images.
            images: Optional pre-resolved mapping of image_id -> raw PNG bytes.
            compact_threshold: Optional threshold to run rolling memory compaction prior to build.

        Returns:
            ProviderRequestContext with normalized messages, mapped tools, and resolved images.
        """
        # 1. Resolve effective system prompt
        effective_sys_prompt = (
            system_prompt.strip()
            if system_prompt is not None and system_prompt.strip()
            else DEFAULT_SYSTEM_PROMPT
        )

        # 2. Extract conversation messages preserving order (applying optional pre-compaction)
        conv = conversation
        if conv is not None and compact_threshold is not None:
            from agent.memory import compact_conversation
            conv, _ = compact_conversation(conv, trigger_chars=compact_threshold)

        raw_messages: List[ChatMessage] = conv.messages if conv else []

        # 3. Handle System message insertion / override
        assembled: List[ChatMessage] = []
        if raw_messages and raw_messages[0].role == Role.SYSTEM:
            # If conversation already starts with SYSTEM:
            # Use explicit system_prompt if provided, else keep existing
            sys_content = (
                effective_sys_prompt
                if system_prompt is not None
                else raw_messages[0].content or effective_sys_prompt
            )
            assembled.append(ChatMessage(role=Role.SYSTEM, content=sys_content))
            assembled.extend(raw_messages[1:])
        else:
            # Prepend system prompt at index 0
            assembled.append(ChatMessage(role=Role.SYSTEM, content=effective_sys_prompt))
            assembled.extend(raw_messages)

        # 4. Apply deterministic context size guard if limit exceeded
        final_messages = cls._apply_context_size_guard(assembled, max_context_chars)

        # 5. Map tools if provided
        mapped_tools: List[Dict[str, Any]] = []
        if tools:
            mapped_tools = OpenAICompatibleToolMapper.map_tools(tools)

        # 6. Resolve in-memory image attachments
        resolved_images: Dict[str, bytes] = dict(images) if images else {}
        for m in final_messages:
            img_id = getattr(m, "image_id", None)
            if not img_id and m.role == Role.TOOL and getattr(m, "name", None) == "capture_viewport" and m.content:
                try:
                    content_dict = json.loads(m.content)
                    if isinstance(content_dict, dict):
                        img_id = content_dict.get("image_id")
                except Exception:
                    pass

            if img_id:
                if img_id not in resolved_images:
                    if image_resolver is None:
                        raise ImageResolutionError(
                            image_id=img_id,
                            message=f"Image '{img_id}' referenced in conversation but no image_resolver was provided.",
                        )
                    try:
                        img_bytes = image_resolver(img_id)
                    except Exception as exc:
                        raise ImageResolutionError(
                            image_id=img_id,
                            message=f"Failed to resolve image '{img_id}' via image_resolver: {exc}",
                        ) from exc

                    if not img_bytes:
                        raise ImageResolutionError(
                            image_id=img_id,
                            message=f"Image '{img_id}' could not be resolved from in-memory cache (image not found or cache expired).",
                        )
                    resolved_images[img_id] = img_bytes

        return ProviderRequestContext(
            messages=final_messages,
            tools=mapped_tools,
            system_prompt=effective_sys_prompt,
            images=resolved_images,
        )

    @classmethod
    def _apply_context_size_guard(
        cls,
        messages: List[ChatMessage],
        max_context_chars: int,
    ) -> List[ChatMessage]:
        """Enforce character safety cap without slicing JSON or breaking tool sequences.

        Eviction Priority:
        1. System message (index 0) is ALWAYS retained.
        2. Latest user message and latest active tool turn are retained.
        3. Older conversation messages/turns are evicted first (from oldest to newest).
        4. If a single tool message still exceeds max_context_chars, an explicit
           truncation marker is used instead of slicing corrupted JSON.
        """
        if not messages:
            return []

        if _calculate_messages_chars(messages) <= max_context_chars:
            return list(messages)

        sys_msg = messages[0]
        remaining = list(messages[1:])

        # Find the index of the latest USER message
        last_user_idx = -1
        for i in range(len(remaining) - 1, -1, -1):
            if remaining[i].role == Role.USER:
                last_user_idx = i
                break

        # Group older messages (before last_user_idx) into atomic clusters:
        # A cluster can be a single message or an ASSISTANT(tool_calls) + all matching TOOL messages.
        older_messages = remaining[:last_user_idx] if last_user_idx > 0 else []
        latest_interaction = remaining[last_user_idx:] if last_user_idx >= 0 else remaining

        # Build clusters for older messages
        clusters: List[List[ChatMessage]] = []
        idx = 0
        while idx < len(older_messages):
            msg = older_messages[idx]
            if msg.role == Role.ASSISTANT and msg.tool_calls:
                cluster = [msg]
                idx += 1
                while idx < len(older_messages) and older_messages[idx].role == Role.TOOL:
                    cluster.append(older_messages[idx])
                    idx += 1
                clusters.append(cluster)
            else:
                clusters.append([msg])
                idx += 1

        # Evict older clusters from the front until within limit
        while clusters:
            current_candidates = [sys_msg]
            for cl in clusters:
                current_candidates.extend(cl)
            current_candidates.extend(latest_interaction)

            if _calculate_messages_chars(current_candidates) <= max_context_chars:
                return current_candidates

            # Evict the oldest cluster
            clusters.pop(0)

        # If all older clusters are evicted and still over limit, evaluate latest interaction
        current_candidates = [sys_msg] + latest_interaction
        if _calculate_messages_chars(current_candidates) <= max_context_chars:
            return current_candidates

        # Edge case: A single huge message (e.g. giant ToolResult) in the latest interaction
        # We replace any overflowing Tool message content with a clean, explicit truncation marker
        sanitized_interaction: List[ChatMessage] = []
        for m in latest_interaction:
            if m.role == Role.TOOL and m.content and len(m.content) > (max_context_chars // 2):
                marker = (
                    f"[TRUNCATED: Tool result exceeded {max_context_chars} characters safety cap. "
                    f"Original size: {len(m.content)} chars. Full result omitted to prevent context overflow.]"
                )
                sanitized_interaction.append(
                    ChatMessage(
                        role=Role.TOOL,
                        content=marker,
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                        image_id=getattr(m, "image_id", None),
                    )
                )
            else:
                sanitized_interaction.append(m)

        return [sys_msg] + sanitized_interaction
