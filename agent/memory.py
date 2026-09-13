"""RollingMemory and deterministic context compaction for agent conversations.

Provides bounded rolling memory and context compaction for long agent conversations
without external databases, RAG, embeddings, or LLM-based summarization.
Zero Blender (bpy) dependencies. Pure Python standard library.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from agent.models import ChatMessage, Conversation, Role, ToolCall

COMPACTION_TRIGGER_CHARS: int = 10000
RETAINED_TURNS_COUNT: int = 2
SUMMARY_MARKER: str = "[Context Summary & Scene Memory]"


def calculate_messages_chars(messages: Sequence[ChatMessage]) -> int:
    """Calculate the total character count across a sequence of ChatMessages."""
    total = 0
    for m in messages:
        if m.content:
            total += len(m.content)
        if m.tool_calls:
            for tc in m.tool_calls:
                total += len(tc.call_id) + len(tc.tool_name)
                total += len(json.dumps(tc.arguments))
    return total


class RollingMemory:
    """Deterministic, rule-based working memory of earlier conversation turns.

    Extracts concise, structured task and scene knowledge from older turn clusters
    to prevent context window overflow while preserving grounded scene truth.
    Maintains strict scene-state consistency: deleted entities are moved to
    deleted_entities and never shown as living in active Verified Scene State.
    """

    def __init__(self):
        self.tasks: List[str] = []
        self.verified_mutations: Dict[str, Dict[str, Any]] = {}
        self.deleted_entities: List[str] = []
        self.inspections: List[str] = []
        self.errors: List[str] = []
        self.last_visual_verification: Optional[Dict[str, str]] = None

    def add_turn_cluster(self, cluster: Sequence[ChatMessage]) -> None:
        """Extract deterministic task, mutation, and verification knowledge from a turn cluster."""
        for msg in cluster:
            if msg.role == Role.USER:
                self._extract_user_message(msg)
            elif msg.role == Role.TOOL:
                self._extract_tool_message(msg)
            elif msg.role == Role.ASSISTANT:
                self._extract_assistant_message(msg)

    def _extract_user_message(self, msg: ChatMessage) -> None:
        """Extract user instructions while merging any prior summary block."""
        if not msg.content:
            return

        content = msg.content.strip()
        # If this is a previously compacted summary block, merge its text back
        if SUMMARY_MARKER in content:
            self._merge_previous_summary(content)
            return

        # Cap prompt to 120 chars for concise task logging
        task_str = content if len(content) <= 120 else content[:117] + "..."
        if task_str and (not self.tasks or self.tasks[-1] != task_str):
            self.tasks.append(task_str)

    def _merge_previous_summary(self, content: str) -> None:
        """Parse and absorb items from a previously generated summary block."""
        lines = content.splitlines()
        current_section = None
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            if line_str.startswith("- Completed Tasks:") or line_str.startswith("- Previous Tasks:"):
                current_section = "tasks"
                continue
            elif line_str.startswith("- Verified Scene State:"):
                current_section = "verified"
                continue
            elif line_str.startswith("- Deleted Objects:"):
                current_section = "deleted"
                continue
            elif line_str.startswith("- Inspections:"):
                current_section = "inspections"
                continue
            elif line_str.startswith("- Visual Verification:"):
                current_section = "visual"
                continue

            if current_section == "tasks" and line_str.startswith("* "):
                task_item = line_str[2:].strip().strip('"')
                if task_item and task_item not in self.tasks:
                    self.tasks.append(task_item)
            elif current_section == "deleted" and line_str.startswith("* "):
                # e.g. * 'Cube': deleted [PASS]
                target_str = line_str[2:].split(":")[0].strip().strip("'")
                if target_str and target_str not in self.deleted_entities:
                    if target_str not in self.verified_mutations:
                        self.deleted_entities.append(target_str)
            elif current_section == "inspections" and line_str.startswith("* "):
                insp_item = line_str[2:].strip()
                if insp_item and insp_item not in self.inspections:
                    self.inspections.append(insp_item)
            elif current_section == "visual" and "Last verdict:" in line_str:
                parts = line_str.split("Last verdict:", 1)[-1].strip()
                decision = parts.split()[0] if parts else "UNKNOWN"
                rationale = ""
                if "(" in parts and ")" in parts:
                    rationale = parts[parts.find("(") + 1 : parts.rfind(")")].strip('"')
                self.last_visual_verification = {"decision": decision, "rationale": rationale}

    def _extract_tool_message(self, msg: ChatMessage) -> None:
        """Extract mutation verification, visual verification, and brief inspection results."""
        if not msg.content:
            return

        tool_name = getattr(msg, "name", None) or "tool"
        data: Dict[str, Any] = {}
        try:
            parsed = json.loads(msg.content)
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            # Not JSON content; check if error string
            if "error" in msg.content.lower():
                self.errors.append(f"{tool_name}: {msg.content[:80]}")
            return

        # 1. Check for error payload
        if "error" in data:
            err_msg = data.get("error")
            err_type = data.get("type", "ERROR")
            self.errors.append(f"{tool_name} ({err_type}): {err_msg}")
            return

        # 2. Check for RNA mutation verification
        verif = data.get("verification")
        if isinstance(verif, dict):
            status = verif.get("status")
            op = verif.get("operation") or tool_name
            target = verif.get("target_name") or data.get("name") or data.get("object_name") or "target"

            if status in ("PASS", "OK"):
                if op in ("delete", "delete_object"):
                    # Object successfully deleted: remove from active living entities
                    self.verified_mutations.pop(target, None)
                    if target not in self.deleted_entities:
                        self.deleted_entities.append(target)
                else:
                    # Object created or modified: remove from deleted if re-created
                    if target in self.deleted_entities:
                        self.deleted_entities.remove(target)

                    entry: Dict[str, Any] = {
                        "operation": op,
                        "target": target,
                        "status": "PASS",
                        "properties": {},
                    }

                    if op in ("create", "create_primitive"):
                        entry["primitive_type"] = data.get("primitive_type") or data.get("type", "OBJECT")
                        if "location" in data and isinstance(data["location"], (list, tuple)):
                            entry["properties"]["location"] = [round(float(v), 2) for v in data["location"][:3]]
                    elif op in ("transform", "transform_object"):
                        for prop_name in ("location", "rotation", "scale"):
                            if prop_name in data and isinstance(data[prop_name], (list, tuple)):
                                entry["properties"][prop_name] = [round(float(v), 2) for v in data[prop_name][:3]]
                    elif op in ("set_material", "assign_material"):
                        mat_name = data.get("material_name") or data.get("name")
                        if mat_name:
                            entry["material"] = mat_name
                        shader_keys = ("roughness", "metallic", "emission_strength", "alpha")
                        for sk in shader_keys:
                            if sk in data:
                                entry["properties"][sk] = round(float(data[sk]), 2)

                    self.verified_mutations[target] = entry
            elif status == "FAIL":
                # Mutation failed semantic verification: record failure, do NOT modify active state
                self.errors.append(f"{tool_name} ('{target}'): VERIFICATION_FAILED")
                return

        # 3. Check for Visual Verification
        vis = data.get("visual_verification")
        if isinstance(vis, dict):
            decision = vis.get("decision") or vis.get("status") or "UNKNOWN"
            rationale = vis.get("rationale") or ""
            self.last_visual_verification = {
                "decision": str(decision),
                "rationale": str(rationale)[:120],
            }

        # 4. Check for Read-only Inspection tools
        if tool_name == "inspect_mesh":
            obj_name = data.get("name") or data.get("object_name") or "object"
            vert_count = data.get("vertex_count") or data.get("vertices_count")
            face_count = data.get("face_count") or data.get("polygon_count")
            detail = f"{vert_count}v, {face_count}f" if vert_count and face_count else "inspected"
            insp_str = f"inspect_mesh('{obj_name}': {detail})"
            if insp_str not in self.inspections:
                self.inspections.append(insp_str)
        elif tool_name == "inspect_scene":
            total_objs = data.get("counts", {}).get("total") or len(data.get("collections", []))
            active_obj = data.get("active_object")
            detail = f"{total_objs} objects" + (f", active='{active_obj}'" if active_obj else "")
            insp_str = f"inspect_scene({detail})"
            if insp_str not in self.inspections:
                self.inspections.append(insp_str)
        elif tool_name in ("inspect_object", "inspect_material", "inspect_selection"):
            target = data.get("name") or data.get("material_name") or data.get("active_object") or ""
            insp_str = f"{tool_name}('{target}')" if target else tool_name
            if insp_str not in self.inspections:
                self.inspections.append(insp_str)
        elif tool_name == "capture_viewport":
            insp_str = "capture_viewport (visual check)"
            if insp_str not in self.inspections:
                self.inspections.append(insp_str)

    def _extract_assistant_message(self, msg: ChatMessage) -> None:
        """Assistant messages are represented through their verified tool effects and tasks."""
        pass

    def to_summary_text(self) -> str:
        """Produce deterministic, concise text representation of rolling memory."""
        sections: List[str] = []

        # 1. Tasks
        if self.tasks:
            task_lines = [f'  * "{t}"' for t in self.tasks[-6:]]  # Keep up to last 6 distinct tasks
            sections.append("- Completed Tasks:\n" + "\n".join(task_lines))

        # 2. Verified Scene State (Living/active entities only)
        if self.verified_mutations:
            state_lines = []
            for name, item in sorted(self.verified_mutations.items()):
                op = item.get("operation", "modified")
                props = item.get("properties", {})
                props_str = ""
                if props:
                    props_str = " (" + ", ".join(f"{k}={v}" for k, v in sorted(props.items())) + ")"
                mat_str = f", material='{item['material']}'" if "material" in item else ""
                state_lines.append(f"  * '{name}': {op}{props_str}{mat_str} [PASS]")
            sections.append("- Verified Scene State:\n" + "\n".join(state_lines))

        # 3. Deleted Objects
        if self.deleted_entities:
            del_lines = [f"  * '{d}': deleted [PASS]" for d in sorted(self.deleted_entities)]
            sections.append("- Deleted Objects:\n" + "\n".join(del_lines))

        # 4. Inspections
        if self.inspections:
            insp_lines = [f"  * {i}" for i in self.inspections[-4:]]  # Keep up to last 4
            sections.append("- Inspections:\n" + "\n".join(insp_lines))

        # 5. Visual Verification
        if self.last_visual_verification:
            dec = self.last_visual_verification.get("decision", "UNKNOWN")
            rat = self.last_visual_verification.get("rationale", "")
            rat_str = f' ("{rat}")' if rat else ""
            sections.append(f"- Visual Verification:\n  * Last verdict: {dec}{rat_str}")

        # 6. Errors if any
        if self.errors:
            err_lines = [f"  * {e}" for e in self.errors[-3:]]
            sections.append("- Past Warnings/Errors:\n" + "\n".join(err_lines))

        if not sections:
            return "- No previous actions recorded."

        return "\n".join(sections)

    def build_summary_messages(self) -> List[ChatMessage]:
        """Construct standard ChatMessage pair (USER + ASSISTANT ack) representing the memory.

        Uses normal message roles so Conversation.validate_sequence() and provider protocols
        remain 100% compliant without modifying the SYSTEM prompt.
        """
        summary_content = (
            f"{SUMMARY_MARKER}\n"
            "Earlier conversation turns were compacted to conserve context while preserving verified state:\n"
            f"{self.to_summary_text()}"
        )
        summary_user = ChatMessage(
            role=Role.USER,
            content=summary_content,
            image_id=None,
        )
        ack_assistant = ChatMessage(
            role=Role.ASSISTANT,
            content="Understood. I have recorded the previous tasks and verified scene state in memory.",
        )
        return [summary_user, ack_assistant]


def partition_conversation_into_turns(
    messages: Sequence[ChatMessage],
) -> Tuple[Optional[ChatMessage], Optional[List[ChatMessage]], List[List[ChatMessage]]]:
    """Partition a sequence of ChatMessages into:
    (system_message, prior_summary_cluster, real_turn_clusters).

    Invariants:
    - system_message: The Role.SYSTEM message at index 0, if present.
    - prior_summary_cluster: The synthetic [USER, ASSISTANT] pair containing SUMMARY_MARKER,
      if generated by a previous compaction.
    - real_turn_clusters: List of actual user turns, where each turn starts with a real
      Role.USER message and includes all subsequent ASSISTANT and TOOL messages.

    This guarantees prior summary blocks are NEVER miscounted as new real user turns
    during successive compactions.
    """
    if not messages:
        return None, None, []

    system_msg: Optional[ChatMessage] = None
    remaining: List[ChatMessage] = []

    if messages[0].role == Role.SYSTEM:
        system_msg = messages[0]
        remaining = list(messages[1:])
    else:
        remaining = list(messages)

    prior_summary: Optional[List[ChatMessage]] = None

    # Detect if the conversation starts with a previously compacted summary block
    # Structure: USER(content contains SUMMARY_MARKER) followed by optional ASSISTANT ack
    if remaining and remaining[0].role == Role.USER and remaining[0].content and SUMMARY_MARKER in remaining[0].content:
        summary_user_msg = remaining[0]
        summary_ack_msg = None
        idx = 1
        if len(remaining) > 1 and remaining[1].role == Role.ASSISTANT and remaining[1].content and "Understood." in remaining[1].content:
            summary_ack_msg = remaining[1]
            idx = 2
        prior_summary = [summary_user_msg] + ([summary_ack_msg] if summary_ack_msg else [])
        remaining = remaining[idx:]

    turn_clusters: List[List[ChatMessage]] = []
    current_cluster: List[ChatMessage] = []

    for msg in remaining:
        if msg.role == Role.USER:
            if current_cluster:
                turn_clusters.append(current_cluster)
            current_cluster = [msg]
        else:
            if current_cluster:
                current_cluster.append(msg)
            else:
                current_cluster = [msg]

    if current_cluster:
        turn_clusters.append(current_cluster)

    return system_msg, prior_summary, turn_clusters


def compact_conversation(
    conversation: Conversation,
    trigger_chars: int = COMPACTION_TRIGGER_CHARS,
    retained_turns: int = RETAINED_TURNS_COUNT,
) -> Tuple[Conversation, bool]:
    """Deterministically compact older conversation turns if character threshold is exceeded.

    Invariants:
    - SYSTEM message (index 0) is strictly preserved.
    - The last `retained_turns` complete turns are retained intact with their original messages.
    - Older turns are processed as atomic clusters; tool-call / tool-result pairs are never split.
    - Ephemeral `image_id` references from older turns are completely unlinked to prevent LRU cache misses.
    - Active / retained turn image_ids remain intact.
    - Prior summary blocks are absorbed and never miscounted as new user turns.
    - Conversation.validate_sequence() is strictly verified before returning.

    Args:
        conversation: Canonical Conversation instance to evaluate.
        trigger_chars: Character count threshold to activate compaction (default: 10,000).
        retained_turns: Number of latest complete turns to protect from compaction (default: 2).

    Returns:
        Tuple of (new_compacted_conversation, was_compacted_boolean).
    """
    raw_messages = conversation.messages
    if calculate_messages_chars(raw_messages) <= trigger_chars:
        return conversation, False

    system_msg, prior_summary, turn_clusters = partition_conversation_into_turns(raw_messages)

    # Need more REAL turn clusters than retained_turns to perform compaction
    if len(turn_clusters) <= retained_turns:
        return conversation, False

    older_clusters = turn_clusters[:-retained_turns]
    kept_clusters = turn_clusters[-retained_turns:]

    # Build rolling memory
    memory = RollingMemory()

    # 1. Absorb prior summary if present
    if prior_summary:
        memory.add_turn_cluster(prior_summary)

    # 2. Extract from older real turn clusters
    for cluster in older_clusters:
        memory.add_turn_cluster(cluster)

    # Reconstruct new conversation
    new_conv = Conversation()

    # 1. System message if present
    if system_msg:
        new_conv.add_message(system_msg)

    # 2. Insert rolling memory summary messages
    for summary_msg in memory.build_summary_messages():
        new_conv.add_message(summary_msg)

    # 3. Append retained turns intact (preserving active image_ids)
    for cluster in kept_clusters:
        for msg in cluster:
            new_conv.add_message(msg)

    # 4. Verify sequence validity
    new_conv.validate_sequence()

    return new_conv, True
