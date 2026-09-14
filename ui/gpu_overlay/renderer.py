"""High-performance 2D GPU drawing functions for Blender AI Viewport Overlay."""

import math
from typing import List, Tuple
import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader

from .state import overlay_state

# Cache compiled built-in shader
_uniform_shader = None


def get_uniform_shader():
    """Retrieve cached UNIFORM_COLOR shader."""
    global _uniform_shader
    if _uniform_shader is None:
        _uniform_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    return _uniform_shader


def build_rounded_rect_tris(
    x: float, y: float, w: float, h: float, radius: float, segments: int = 8
) -> List[Tuple[float, float]]:
    """Tessellate an anti-aliased rounded rectangle into triangles."""
    r = max(0.0, min(radius, w / 2.0, h / 2.0))
    if r <= 0.0:
        # Simple rectangle (2 triangles)
        return [
            (x, y), (x + w, y), (x + w, y + h),
            (x, y), (x + w, y + h), (x, y + h),
        ]

    tris: List[Tuple[float, float]] = []

    # Center box
    cx1, cx2 = x + r, x + w - r
    cy1, cy2 = y + r, y + h - r

    # Center core
    tris.extend([
        (cx1, cy1), (cx2, cy1), (cx2, cy2),
        (cx1, cy1), (cx2, cy2), (cx1, cy2),
    ])

    # Left & Right edges
    tris.extend([
        (x, cy1), (cx1, cy1), (cx1, cy2),
        (x, cy1), (cx1, cy2), (x, cy2),
        (cx2, cy1), (x + w, cy1), (x + w, cy2),
        (cx2, cy1), (x + w, cy2), (cx2, cy2),
    ])

    # Bottom & Top edges
    tris.extend([
        (cx1, y), (cx2, y), (cx2, cy1),
        (cx1, y), (cx2, cy1), (cx1, cy1),
        (cx1, cy2), (cx2, cy2), (cx2, y + h),
        (cx1, cy2), (cx2, y + h), (cx1, y + h),
    ])

    # 4 Corner Arcs
    corners = [
        (cx2, cy2, 0.0, 0.5 * math.pi),            # Top-Right
        (cx1, cy2, 0.5 * math.pi, math.pi),        # Top-Left
        (cx1, cy1, math.pi, 1.5 * math.pi),        # Bottom-Left
        (cx2, cy1, 1.5 * math.pi, 2.0 * math.pi),  # Bottom-Right
    ]

    for center_x, center_y, start_angle, end_angle in corners:
        angle_step = (end_angle - start_angle) / segments
        for i in range(segments):
            a1 = start_angle + i * angle_step
            a2 = start_angle + (i + 1) * angle_step
            p1 = (center_x + r * math.cos(a1), center_y + r * math.sin(a1))
            p2 = (center_x + r * math.cos(a2), center_y + r * math.sin(a2))
            tris.extend([(center_x, center_y), p1, p2])

    return tris


def draw_rounded_rect(
    x: float, y: float, w: float, h: float, radius: float, color: Tuple[float, float, float, float]
) -> None:
    """Draw a filled rounded rectangle with given color."""
    if w <= 0 or h <= 0:
        return
    shader = get_uniform_shader()
    tris = build_rounded_rect_tris(x, y, w, h, radius)
    batch = batch_for_shader(shader, "TRIS", {"pos": tris})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def draw_rounded_shadow(
    x: float, y: float, w: float, h: float, radius: float, shadow_size: float = 12.0
) -> None:
    """Draw smooth multi-pass drop shadow behind a rounded rectangle."""
    passes = 4
    for i in range(passes):
        spread = shadow_size * ((i + 1) / passes)
        alpha = 0.07 * (1.0 - (i / passes))
        draw_rounded_rect(
            x - spread,
            y - spread - (shadow_size * 0.4),
            w + 2 * spread,
            h + 2 * spread,
            radius + spread,
            (0.02, 0.02, 0.03, alpha),
        )


def draw_text(
    text: str,
    x: float,
    y: float,
    size: int = 14,
    color: Tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
    font_id: int = 0,
) -> Tuple[float, float]:
    """Draw 2D text using Blender's blf module; returns (width, height)."""
    blf.size(font_id, size)
    blf.color(font_id, *color)
    blf.position(font_id, x, y, 0.0)
    blf.draw(font_id, text)
    return blf.dimensions(font_id, text)


def wrap_text(text: str, max_width: float, size: int, font_id: int = 0) -> List[str]:
    """Wrap text to pixel width while preserving explicit newlines."""
    blf.size(font_id, size)
    lines: List[str] = []
    for paragraph in (text or "").splitlines() or [""]:
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if current and blf.dimensions(font_id, candidate)[0] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines or [""]


def draw_overlay_hud(context) -> None:
    """Primary Viewport draw callback rendering the floating Higgsfield-style AI HUD."""
    if not overlay_state.is_open:
        return

    region = context.region
    if not region:
        return

    reg_w = region.width
    reg_h = region.height

    # Responsive dimensions
    bar_w = min(680.0, max(460.0, reg_w - 60.0))
    bar_h = 74.0
    bar_x = (reg_w - bar_w) / 2.0
    bar_y = 42.0
    corner_r = 18.0

    overlay_state.bar_rect = (bar_x, bar_y, bar_w, bar_h)
    overlay_state.response_copy_btn_rect = (0.0, 0.0, 0.0, 0.0)

    # Enable alpha blending
    gpu.state.blend_set("ALPHA")

    # 1. Outer Drop Shadow
    draw_rounded_shadow(bar_x, bar_y, bar_w, bar_h, corner_r, shadow_size=16.0)

    # 2. Main Background Container & Thin Border
    # Outer 1px border (#2E3036)
    draw_rounded_rect(bar_x - 1, bar_y - 1, bar_w + 2, bar_h + 2, corner_r + 1, (0.18, 0.19, 0.22, 0.95))
    # Inner background (#141518)
    draw_rounded_rect(bar_x, bar_y, bar_w, bar_h, corner_r, (0.08, 0.085, 0.095, 0.96))

    # -------------------------------------------------------------------------
    # 3. Upper Row: Prompt Input & Sparkle Icon
    # -------------------------------------------------------------------------
    sparkle_x = bar_x + 18.0
    sparkle_y = bar_y + bar_h - 28.0
    # Sparkle icon
    draw_text("✦", sparkle_x, sparkle_y, size=15, color=(0.82, 0.99, 0.09, 1.0))  # Neon Lime Sparkle

    input_x = sparkle_x + 22.0
    input_y = sparkle_y
    input_w = bar_w - 180.0
    input_h = 24.0
    overlay_state.input_rect = (input_x, input_y - 4.0, input_w, input_h)

    # Display prompt or placeholder
    prompt = overlay_state.prompt_text
    if prompt:
        # Draw user typed text
        draw_text(prompt, input_x, input_y, size=14, color=(0.95, 0.95, 0.97, 1.0))
        # Cursor line calculation
        prefix = prompt[:overlay_state.cursor_pos]
        blf.size(0, 14)
        cur_offset = blf.dimensions(0, prefix)[0]
        if overlay_state.cursor_visible:
            cur_x = input_x + cur_offset + 1.0
            cur_y = input_y - 2.0
            draw_rounded_rect(cur_x, cur_y, 2.0, 16.0, 1.0, (0.82, 0.99, 0.09, 0.9))
    else:
        # Placeholder
        draw_text("Ask Blender AI... (e.g. Inspect scene, count meshes)", input_x, input_y, size=13, color=(0.45, 0.47, 0.52, 0.8))
        if overlay_state.cursor_visible:
            draw_rounded_rect(input_x, input_y - 2.0, 2.0, 16.0, 1.0, (0.82, 0.99, 0.09, 0.7))

    # -------------------------------------------------------------------------
    # 4. Action Button (Right Side - Higgsfield Style)
    # -------------------------------------------------------------------------
    btn_w = 110.0
    btn_h = 44.0
    btn_x = bar_x + bar_w - btn_w - 14.0
    btn_y = bar_y + (bar_h - btn_h) / 2.0
    btn_r = 14.0

    is_processing = overlay_state.is_processing
    if not is_processing:
        overlay_state.send_btn_rect = (btn_x, btn_y, btn_w, btn_h)
        overlay_state.cancel_btn_rect = (0, 0, 0, 0)
        # Vibrant Neon Lime (#D1FE17)
        btn_hover = overlay_state.hover_element == "send"
        btn_color = (0.86, 1.0, 0.15, 1.0) if btn_hover else (0.82, 0.99, 0.09, 1.0)
        draw_rounded_rect(btn_x, btn_y, btn_w, btn_h, btn_r, btn_color)
        # Bold text
        text_w, _ = blf.dimensions(0, "SEND ✦")
        draw_text("SEND ✦", btn_x + (btn_w - text_w) / 2.0, btn_y + 15.0, size=12, color=(0.05, 0.06, 0.07, 1.0))
    else:
        overlay_state.cancel_btn_rect = (btn_x, btn_y, btn_w, btn_h)
        overlay_state.send_btn_rect = (0, 0, 0, 0)
        # Red / Orange Cancel button
        btn_hover = overlay_state.hover_element == "cancel"
        btn_color = (0.9, 0.25, 0.25, 1.0) if btn_hover else (0.8, 0.2, 0.2, 0.95)
        draw_rounded_rect(btn_x, btn_y, btn_w, btn_h, btn_r, btn_color)
        text_w, _ = blf.dimensions(0, "CANCEL ✕")
        draw_text("CANCEL ✕", btn_x + (btn_w - text_w) / 2.0, btn_y + 15.0, size=12, color=(1.0, 1.0, 1.0, 1.0))

    # -------------------------------------------------------------------------
    # 5. Bottom Row: Pills (Higgsfield metadata styling)
    # -------------------------------------------------------------------------
    pill_y = bar_y + 12.0
    pill_h = 22.0
    pill_r = 6.0

    # Pill 1: Mode
    p1_x = sparkle_x
    p1_w = 95.0
    draw_rounded_rect(p1_x, pill_y, p1_w, pill_h, pill_r, (0.13, 0.14, 0.16, 0.9))
    draw_text("⚙ Mode: Agent", p1_x + 8.0, pill_y + 6.0, size=10, color=(0.7, 0.72, 0.78, 1.0))

    # Pill 2: Status Indicator
    p2_x = p1_x + p1_w + 8.0
    status_label = f"● {overlay_state.status_text}"
    blf.size(0, 10)
    p2_w = blf.dimensions(0, status_label)[0] + 18.0
    draw_rounded_rect(p2_x, pill_y, p2_w, pill_h, pill_r, (0.13, 0.14, 0.16, 0.9))
    status_col = (0.3, 0.85, 0.4, 1.0) if not is_processing else (0.95, 0.7, 0.1, 1.0)
    draw_text(status_label, p2_x + 8.0, pill_y + 6.0, size=10, color=status_col)

    # Pill 3: Key hints
    p3_x = p2_x + p2_w + 8.0
    p3_w = 90.0
    draw_rounded_rect(p3_x, pill_y, p3_w, pill_h, pill_r, (0.13, 0.14, 0.16, 0.7))
    draw_text("Esc to close", p3_x + 10.0, pill_y + 6.0, size=10, color=(0.5, 0.52, 0.56, 0.9))

    # -------------------------------------------------------------------------
    # 6. Upper Drawer: Approval Card (High Priority) OR Response Drawer
    # -------------------------------------------------------------------------
    if overlay_state.pending_approval:
        # Approval Card takes visual precedence over regular response drawer
        appr = overlay_state.pending_approval
        card_w = bar_w
        is_plan = appr.get("kind") == "plan"
        shown_steps = appr.get("steps_shown", []) if is_plan else []
        card_h = 142.0 + min(len(shown_steps), 5) * 18.0 if is_plan else 106.0
        card_x = bar_x
        card_y = bar_y + bar_h + 10.0
        corner_r = 16.0

        overlay_state.approval_card_rect = (card_x, card_y, card_w, card_h)

        # 1. Drop shadow & glowing warning border
        draw_rounded_shadow(card_x, card_y, card_w, card_h, corner_r, shadow_size=14.0)
        draw_rounded_rect(card_x - 1.5, card_y - 1.5, card_w + 3.0, card_h + 3.0, corner_r + 1.0, (0.95, 0.65, 0.15, 0.95))
        draw_rounded_rect(card_x, card_y, card_w, card_h, corner_r, (0.09, 0.095, 0.11, 0.98))

        # 2. Header row
        header = "▣ Plan Ready" if is_plan else "⚠ Confirm Action"
        header_color = (0.55, 0.85, 1.0, 1.0) if is_plan else (0.96, 0.72, 0.18, 1.0)
        draw_text(header, card_x + 18.0, card_y + card_h - 24.0, size=12, color=header_color)
        appr_id = appr.get("approval_id", "")
        if appr_id:
            draw_text(appr_id, card_x + card_w - 110.0, card_y + card_h - 22.0, size=10, color=(0.55, 0.57, 0.62, 0.8))

        # 3. Middle row: Action Description & Risk Badge
        desc = appr.get("description", "Execute action")
        draw_text(desc, card_x + 18.0, card_y + card_h - 48.0, size=14, color=(0.96, 0.96, 0.98, 1.0))

        if is_plan:
            step_y = card_y + card_h - 75.0
            for step in shown_steps[:5]:
                tool_name = step.get("tool_name", "tool")
                step_desc = step.get("description", "")
                label = f"✓ {tool_name}: {step_desc}" if step_desc else f"✓ {tool_name}"
                draw_text(label, card_x + 22.0, step_y, size=11, color=(0.78, 0.9, 0.82, 1.0))
                step_y -= 18.0
            hidden_count = int(appr.get("hidden_count", 0) or 0)
            if hidden_count:
                draw_text(f"+ {hidden_count} more step(s)", card_x + 22.0, step_y, size=10, color=(0.6, 0.65, 0.7, 1.0))

        risk = str(appr.get("risk_level", "MEDIUM")).upper()
        risk_text = f"Risk: {risk}"
        draw_rounded_rect(card_x + card_w - 120.0, card_y + card_h - 52.0, 102.0, 20.0, 5.0, (0.22, 0.16, 0.08, 0.95))
        draw_rounded_rect(card_x + card_w - 120.0, card_y + card_h - 52.0, 102.0, 20.0, 5.0, (0.85, 0.55, 0.12, 0.5))
        draw_text(risk_text, card_x + card_w - 112.0, card_y + card_h - 46.0, size=10, color=(1.0, 0.82, 0.30, 1.0))

        # 4. Bottom row: Reject and Approve Buttons
        btn_h = 32.0
        btn_y = card_y + 12.0

        # Reject Button (Muted Dark Red)
        r_w = 115.0
        r_x = card_x + 18.0
        overlay_state.reject_btn_rect = (r_x, btn_y, r_w, btn_h)
        r_hover = overlay_state.hover_element == "reject"
        r_col = (0.45, 0.12, 0.14, 1.0) if r_hover else (0.26, 0.09, 0.11, 0.95)
        draw_rounded_rect(r_x - 1, btn_y - 1, r_w + 2, btn_h + 2, 8.0, (0.65, 0.18, 0.20, 0.8))
        draw_rounded_rect(r_x, btn_y, r_w, btn_h, 7.0, r_col)
        draw_text("✕ Reject (N)", r_x + 16.0, btn_y + 10.0, size=11, color=(1.0, 0.7, 0.7, 1.0))

        # Approve Button (Muted Dark Emerald Green with Crisp Border)
        a_w = 125.0
        a_x = card_x + card_w - a_w - 18.0
        overlay_state.approve_btn_rect = (a_x, btn_y, a_w, btn_h)
        a_hover = overlay_state.hover_element == "approve"
        a_col = (0.16, 0.42, 0.22, 1.0) if a_hover else (0.10, 0.26, 0.14, 0.95)
        draw_rounded_rect(a_x - 1, btn_y - 1, a_w + 2, btn_h + 2, 8.0, (0.25, 0.75, 0.38, 0.85))
        draw_rounded_rect(a_x, btn_y, a_w, btn_h, 7.0, a_col)
        draw_text("✓ Approve (Y)", a_x + 16.0, btn_y + 10.0, size=11, color=(0.55, 0.98, 0.65, 1.0))

    else:
        # Reset approval rects
        overlay_state.approval_card_rect = (0.0, 0.0, 0.0, 0.0)
        overlay_state.reject_btn_rect = (0.0, 0.0, 0.0, 0.0)
        overlay_state.approve_btn_rect = (0.0, 0.0, 0.0, 0.0)

        # Regular Response Drawer (If AI is thinking or has a response)
        overlay_state.response_copy_btn_rect = (0.0, 0.0, 0.0, 0.0)
        if is_processing or overlay_state.last_response_text or overlay_state.streaming_response_text or overlay_state.active_tool_name:
            resp_text = overlay_state.streaming_response_text or overlay_state.last_response_text
            if is_processing and overlay_state.active_tool_name:
                resp_text = f"Running tool: {overlay_state.active_tool_name}..."
            elif is_processing and not resp_text:
                resp_text = "AI Thinking..."

            if resp_text:
                drawer_w = bar_w
                body_lines = wrap_text(resp_text, drawer_w - 36.0, 12)
                max_lines = max(1, min(len(body_lines), 10))
                drawer_h = 58.0 + max_lines * 18.0
                drawer_x = bar_x
                drawer_y = bar_y + bar_h + 10.0

                # Drawer shadow & background
                draw_rounded_shadow(drawer_x, drawer_y, drawer_w, drawer_h, corner_r, shadow_size=12.0)
                draw_rounded_rect(drawer_x - 1, drawer_y - 1, drawer_w + 2, drawer_h + 2, corner_r + 1, (0.18, 0.19, 0.22, 0.95))
                draw_rounded_rect(drawer_x, drawer_y, drawer_w, drawer_h, corner_r, (0.09, 0.095, 0.11, 0.96))

                # Header
                draw_text("🤖 Blender AI Assistant", drawer_x + 16.0, drawer_y + drawer_h - 22.0, size=11, color=(0.82, 0.99, 0.09, 0.9))

                # Copy action for the complete response text.
                copy_w = 60.0
                copy_h = 22.0
                copy_x = drawer_x + drawer_w - copy_w - 14.0
                copy_y = drawer_y + drawer_h - copy_h - 10.0
                overlay_state.response_copy_btn_rect = (copy_x, copy_y, copy_w, copy_h)
                draw_rounded_rect(copy_x, copy_y, copy_w, copy_h, 6.0, (0.16, 0.18, 0.21, 1.0))
                draw_text("Copy", copy_x + 14.0, copy_y + 7.0, size=10, color=(0.85, 0.88, 0.92, 1.0))

                # Body text wraps into multiple lines instead of being truncated.
                body_y = drawer_y + drawer_h - 48.0
                for line in body_lines[:max_lines]:
                    draw_text(line, drawer_x + 16.0, body_y, size=12, color=(0.92, 0.93, 0.95, 1.0))
                    body_y -= 18.0

    # Restore default blend state
    gpu.state.blend_set("NONE")
