"""State container for Blender AI Sidebar In-Viewport GPU Overlay."""

import time
from typing import Optional, Tuple, List, Dict, Any


class GPUOverlayState:
    """Manages input buffer, layout bounding boxes, cursor, and animation state."""

    def __init__(self):
        self.is_open: bool = False
        self.prompt_text: str = ""
        self.cursor_pos: int = 0
        self.cursor_visible: bool = True
        self.last_blink_time: float = time.time()
        self.blink_interval: float = 0.5  # 500ms

        # Bounding boxes (x, y, w, h) in viewport region pixel coordinates
        self.bar_rect: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self.input_rect: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self.send_btn_rect: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self.cancel_btn_rect: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

        # Hover states
        self.hover_element: Optional[str] = None  # 'send', 'cancel', 'input', None

        # Display caches
        self.status_text: str = "Ready"
        self.is_processing: bool = False
        self.active_tool_name: Optional[str] = None
        self.last_response_text: str = ""

        # Approval Card Bounding Boxes and State
        self.pending_approval: Optional[Dict[str, Any]] = None
        self.approval_card_rect: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self.reject_btn_rect: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self.approve_btn_rect: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def reset_input(self) -> None:
        """Clear prompt input buffer and reset cursor."""
        self.prompt_text = ""
        self.cursor_pos = 0
        self.cursor_visible = True
        self.last_blink_time = time.time()

    def update_blink(self) -> bool:
        """Toggle cursor visibility based on timer; returns True if state changed."""
        now = time.time()
        if now - self.last_blink_time >= self.blink_interval:
            self.cursor_visible = not self.cursor_visible
            self.last_blink_time = now
            return True
        return False

    def insert_text(self, text: str) -> None:
        """Insert arbitrary text at the current cursor position."""
        if not text:
            return
        left = self.prompt_text[:self.cursor_pos]
        right = self.prompt_text[self.cursor_pos:]
        self.prompt_text = left + text + right
        self.cursor_pos += len(text)
        self.cursor_visible = True
        self.last_blink_time = time.time()

    def delete_backward(self) -> None:
        """Delete one character before the cursor (Backspace)."""
        if self.cursor_pos > 0:
            left = self.prompt_text[:self.cursor_pos - 1]
            right = self.prompt_text[self.cursor_pos:]
            self.prompt_text = left + right
            self.cursor_pos -= 1
            self.cursor_visible = True
            self.last_blink_time = time.time()

    def delete_word_backward(self) -> None:
        """Delete one word before cursor (Ctrl+Backspace)."""
        if self.cursor_pos > 0:
            left = self.prompt_text[:self.cursor_pos].rstrip()
            last_space = left.rfind(" ")
            new_pos = last_space + 1 if last_space != -1 else 0
            right = self.prompt_text[self.cursor_pos:]
            self.prompt_text = self.prompt_text[:new_pos] + right
            self.cursor_pos = new_pos
            self.cursor_visible = True
            self.last_blink_time = time.time()

    def delete_forward(self) -> None:
        """Delete one character after the cursor (Delete)."""
        if self.cursor_pos < len(self.prompt_text):
            left = self.prompt_text[:self.cursor_pos]
            right = self.prompt_text[self.cursor_pos + 1:]
            self.prompt_text = left + right
            self.cursor_visible = True
            self.last_blink_time = time.time()

    def move_cursor_left(self) -> None:
        """Move cursor one character to the left."""
        if self.cursor_pos > 0:
            self.cursor_pos -= 1
            self.cursor_visible = True
            self.last_blink_time = time.time()

    def move_cursor_right(self) -> None:
        """Move cursor one character to the right."""
        if self.cursor_pos < len(self.prompt_text):
            self.cursor_pos += 1
            self.cursor_visible = True
            self.last_blink_time = time.time()

    def move_cursor_home(self) -> None:
        """Move cursor to the start of the line."""
        self.cursor_pos = 0
        self.cursor_visible = True
        self.last_blink_time = time.time()

    def move_cursor_end(self) -> None:
        """Move cursor to the end of the line."""
        self.cursor_pos = len(self.prompt_text)
        self.cursor_visible = True
        self.last_blink_time = time.time()

    def hit_test(self, px: float, py: float) -> Optional[str]:
        """Determine which UI element (if any) contains the point (px, py)."""
        # Check Approval Card elements if pending approval is active
        if self.pending_approval:
            ax, ay, aw, ah = self.approve_btn_rect
            if ax <= px <= ax + aw and ay <= py <= ay + ah:
                return "approve"

            rx, ry, rw, rh = self.reject_btn_rect
            if rx <= px <= rx + rw and ry <= py <= ry + rh:
                return "reject"

            cx, cy, cw, ch = self.approval_card_rect
            if cx <= px <= cx + cw and cy <= py <= cy + ch:
                return "approval_card"

        bx, by, bw, bh = self.bar_rect
        if not (bx <= px <= bx + bw and by <= py <= by + bh):
            return None

        # Check send / cancel button
        sx, sy, sw, sh = self.send_btn_rect
        if sx <= px <= sx + sw and sy <= py <= sy + sh:
            return "send"

        cx, cy, cw, ch = self.cancel_btn_rect
        if cx <= px <= cx + cw and cy <= py <= cy + ch:
            return "cancel"

        ix, iy, iw, ih = self.input_rect
        if ix <= px <= ix + iw and iy <= py <= iy + ih:
            return "input"

        return "bar"


# Global singleton instance
overlay_state = GPUOverlayState()
