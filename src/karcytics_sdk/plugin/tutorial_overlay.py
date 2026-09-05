"""TutorialOverlay — full-screen coaching overlay for Karcytics Academy.

Design principles (SOLID):
- Single responsibility: owns only rendering and masking of the overlay.
  All state machine logic lives in AcademyManager / workspace_window.
- Open/Closed: new step types are handled by extending render_step()
  branching logic without modifying mask or paint internals.
- Dependency Inversion: receives its `AcademyManager` and event bus via the
  constructor rather than importing a Hub-only global singleton — the same
  DI shape `AcademyManager` itself uses (see `academy.py`). This is what
  lets the Hub and every isolated plugin share this exact class: each side
  passes in its own `AcademyManager` instance and its own `AcademyEventBus`
  adapter at construction time, instead of keeping a second copy of this
  widget around to bind to a different global.
"""

import math
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QRegion
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .academy import (
    ACADEMY_COURSE_COMPLETED,
    ACADEMY_STEP_CHANGED,
    ACADEMY_SUBTASK_COMPLETED,
    AcademyEventBus,
    AcademyManager,
)
from .cyto_character import CytoWidget
from .dialogs import ask_yes_no
from .theme_fallback import Colors, theme_manager
from .tutorial_models import (
    BaseStep,
    BranchingStep,
    ConsentStep,
    ForcedInteractionStep,
    InfoStep,
    InteractionStep,
    SubplotCheckStep,
    VerificationStep,
    WaitForEventStep,
)

# We will use Colors dynamically in the UI code rather than hardcoded hex codes.

CONTENT_WIDTH: int = 392
_HEADER_TEXT_HEIGHT_BUFFER: int = 48


class TutorialOverlay(QWidget):
    """Full-screen coaching overlay.

    Sits as a sibling of the plugin panel inside analysis_page.
    Cuts transparent "spotlight" holes over target widgets so the user
    can interact with them while the rest of the UI is dimmed.

    Public interface
    ----------------
    render_step(step)        Called by event_bus when the step changes.
    show_text(text)          Update the bubble text (called by timer loop).
    set_targets(rects)       Update spotlight rectangles + reposition Cyto.
    set_progress(cur, total) Update the progress bar.
    """

    skip_requested = pyqtSignal()

    def __init__(
        self,
        academy_manager: AcademyManager,
        event_bus: AcademyEventBus,
        parent: QWidget | None = None,
        compact_mode: bool = False,
    ) -> None:
        """Initialize the tutorial overlay and synchronize it with the current tutorial state.

        Parameters:
            academy_manager: The `AcademyManager` this overlay renders state from.
            event_bus: The `AcademyEventBus` `academy_manager` itself was built with —
                used to receive `ACADEMY_STEP_CHANGED`/etc. notifications.
            compact_mode (bool): Whether to use compact positioning instead of positioning around spotlight targets.
        """
        super().__init__(parent)
        self._academy_manager = academy_manager
        self._event_bus = event_bus

        # Allow mouse events; masking handles the passthrough behaviour.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self.target_rects: list[QRect] = []
        self.current_step: BaseStep | None = None
        self._compact_mode = compact_mode
        self.custom_completion_factories: dict[str, Callable[[QWidget], QWidget]] = {}
        self.completion_container: Any = None

        self._build_cyto()
        self._build_bubble()

        # Save bound methods as instance variables so they can be reliably removed later
        # (Accessing self.method creates a new bound method object each time, which fails list.remove)
        self._render_step_cb = self.render_step
        self._on_subtask_cb = self._on_subtask_completed
        self._on_course_cb = self.show_completion_screen

        self._event_bus.subscribe(ACADEMY_STEP_CHANGED, self._render_step_cb)
        self._event_bus.subscribe(ACADEMY_SUBTASK_COMPLETED, self._on_subtask_cb)
        self._event_bus.subscribe(ACADEMY_COURSE_COMPLETED, self._on_course_cb)

        self._on_theme_cb = self._on_theme_changed
        theme_manager.theme_changed.connect(self._on_theme_cb)

        self.destroyed.connect(self._cleanup)

        self._populate_default_buttons()

        # Initialize with the current state of the tutorial manager
        from PyQt6.QtCore import QTimer

        if getattr(self._academy_manager, "current_step", None):
            # Defer rendering slightly to allow layout to settle
            QTimer.singleShot(0, lambda: self.render_step(self._academy_manager.current_step))
        else:
            self.hide()

    def _cleanup(self, *args) -> None:  # noqa: ARG002
        """Unsubscribe from event bus when the C++ object is deleted."""
        self._event_bus.unsubscribe(ACADEMY_STEP_CHANGED, self._render_step_cb)
        self._event_bus.unsubscribe(ACADEMY_SUBTASK_COMPLETED, self._on_subtask_cb)
        self._event_bus.unsubscribe(ACADEMY_COURSE_COMPLETED, self._on_course_cb)

        import contextlib

        with contextlib.suppress(TypeError):
            theme_manager.theme_changed.disconnect(self._on_theme_cb)

    def _is_alive(self) -> bool:
        """Safely check if the underlying C++ object has been deleted."""
        try:
            self.objectName()
            return True
        except RuntimeError:
            return False

    # ── Build helpers ─────────────────────────────────────────────────────────

    def _build_cyto(self) -> None:
        self.cyto = CytoWidget(self)

    def _build_bubble(self) -> None:
        """Builds the overlay's instructional bubble and its progress and navigation controls."""
        self.bubble_container = QWidget(self)
        self.bubble_container.setObjectName("BubbleContainer")
        theme_manager.apply_style(
            self.bubble_container,
            "#BubbleContainer { background-color: {BG_DARKEST}; border: 2px solid {ACCENT_SUCCESS}; border-radius: 12px; }",
        )
        self.bubble_container.setFixedWidth(420)
        self.bubble_layout = QVBoxLayout(self.bubble_container)
        self.bubble_layout.setContentsMargins(0, 0, 0, 0)
        self.bubble_layout.setSpacing(0)

        self.body_container = QWidget()
        self.body_layout = QVBoxLayout(self.body_container)
        self.body_layout.setContentsMargins(14, 14, 14, 14)
        self.body_layout.setSpacing(8)
        self.bubble_layout.addWidget(self.body_container)

        # Header row
        header = QHBoxLayout()
        self.lbl_progress = QLabel("Karcytics Academy")
        theme_manager.apply_style(
            self.lbl_progress,
            "color: {FG_SECONDARY}; font-size: 13px; font-weight: bold; font-family: sans-serif;",
        )
        self.btn_close = QPushButton("×")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setFixedSize(24, 24)
        theme_manager.apply_style(
            self.btn_close,
            "color: {FG_SECONDARY}; border: none; font-size: 16px; font-weight: bold;",
        )
        self.btn_close.enterEvent = lambda e: self.btn_close.setStyleSheet(  # type: ignore # noqa: ARG005
            f"color: {Colors.FG_PRIMARY}; border: none; font-size: 16px; font-weight: bold;"
        )
        self.btn_close.leaveEvent = lambda e: self.btn_close.setStyleSheet(  # type: ignore # noqa: ARG005
            f"color: {Colors.FG_SECONDARY}; border: none; font-size: 16px; font-weight: bold;"
        )
        self.btn_close.clicked.connect(self._prompt_close)

        header.addWidget(self.lbl_progress)
        header.addStretch()
        header.addWidget(self.btn_close)
        self.body_layout.addLayout(header)

        # Step text
        self.text_label = QLabel("Welcome to Karcytics Academy!")
        self.text_label.setTextFormat(Qt.TextFormat.RichText)
        font = self.text_label.font()
        font.setPixelSize(16)
        font.setFamily("sans-serif")
        self.text_label.setFont(font)
        theme_manager.apply_style(self.text_label, "color: {FG_PRIMARY}; padding: 8px 0px;")
        self.text_label.setWordWrap(True)
        self.text_label.setFixedWidth(CONTENT_WIDTH)  # 420 (container) - 28 (margins)
        self.body_layout.addWidget(self.text_label)

        # Dynamic content (checklists, etc.)
        self.dynamic_content = QVBoxLayout()
        self.body_layout.addLayout(self.dynamic_content)

        # Footer container
        self.footer_container = QWidget()
        self.footer_container.setObjectName("BubbleFooter")
        theme_manager.apply_style(
            self.footer_container,
            "#BubbleFooter {"
            "  background-color: {BG_MEDIUM};"
            "  border-bottom-left-radius: 8px;"
            "  border-bottom-right-radius: 8px;"
            "  border-top: 1px solid {BORDER};"
            "}",
        )
        self.footer_layout = QHBoxLayout(self.footer_container)
        self.footer_layout.setContentsMargins(14, 12, 14, 12)
        self.bubble_layout.addWidget(self.footer_container)

        # Progress bar (moved to footer)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        theme_manager.apply_style(
            self.progress_bar,
            "QProgressBar { background-color: {BG_DARKER}; border-radius: 3px; }"
            "QProgressBar::chunk { background-color: {ACCENT_PRIMARY}; border-radius: 3px; }",
        )
        self.footer_layout.addWidget(self.progress_bar, stretch=1)
        self.footer_layout.addSpacing(16)

        # Button row
        self.btn_container = QWidget()
        theme_manager.apply_style(self.btn_container, "background: transparent;")
        self.btn_layout = QHBoxLayout(self.btn_container)
        self.btn_layout.setContentsMargins(0, 0, 0, 0)
        self.footer_layout.addWidget(self.btn_container)

        self.btn_next = QPushButton("Next →")
        theme_manager.apply_style(
            self.btn_next,
            "background-color: {ACCENT_PRIMARY}; color: {BG_DARKEST};"
            "border: 1px solid {ACCENT_PRIMARY}; border-radius: 4px;"
            "padding: 6px 14px; font-weight: bold;",
        )
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)

        # Lets a step (e.g. a hands-off VerificationStep the user is meant to
        # work through freely) offer a manual "I've got it" dismissal instead
        # of a forced hide_bubble_after_ms timer — the timer hides Cyto's
        # bubble on its own schedule whether or not the user finished
        # reading it; this button hides on the user's own schedule instead.
        self.btn_dismiss_bubble = QPushButton("Got it, I'll take it from here →")
        theme_manager.apply_style(
            self.btn_dismiss_bubble,
            "background-color: transparent; color: {FG_SECONDARY};"
            "border: 1px solid {BORDER}; border-radius: 4px;"
            "padding: 6px 14px;",
        )
        self.btn_dismiss_bubble.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_dismiss_bubble.clicked.connect(self._dismiss_bubble)
        self.btn_dismiss_bubble.clicked.connect(self._dismiss_bubble)
        self.btn_dismiss_bubble.hide()

    def on_cyto_clicked(self) -> None:
        """User clicked Cyto directly (e.g. to hear a fun tip)."""
        pass

    def _on_theme_changed(self) -> None:
        """Re-apply all tracked styles and re-render text so the bubble reflects the new theme."""
        # Re-apply every widget style that was registered via theme_manager.apply_style()
        theme_manager._apply_dynamic_styles()
        # Re-render text so inline HTML accent colors (bold spans) also update
        if self.current_step:
            self._update_text_rendering(self.current_step.text)
        if hasattr(self, "cyto") and hasattr(self.cyto, "apply_theme"):
            self.cyto.apply_theme()

    def _update_text_rendering(self, text: str) -> None:
        import re

        # Replace newlines with <br>
        text = text.replace("\\n", "<br>")

        # Replace --- with a styled horizontal rule
        text = re.sub(r"---", f'<hr style="border: none; border-top: 1px solid {Colors.BORDER}; margin: 8px 0;">', text)

        # Replace **text** with highlighted accent color
        text = re.sub(r"\*\*(.*?)\*\*", f'<b style="color: {Colors.ACCENT_PRIMARY};">\\1</b>', text)
        # Replace *text* or _text_ with italic
        text = re.sub(r"\*(.*?)\*", r"<i>\1</i>", text)
        text = re.sub(r"_(.*?)_", r"<i>\1</i>", text)
        # Replace `text` with inline code style
        text = re.sub(
            r"`(.*?)`",
            f'<code style="color: {Colors.FG_PRIMARY}; background-color: {Colors.BG_MEDIUM}; padding: 2px 4px; border-radius: 3px;">\\1</code>',
            text,
        )

        self.text_label.setText(text)

    def _prompt_close(self) -> None:
        """Prompts the user before emitting skip_requested."""
        if ask_yes_no(
            self,
            "Exit Cyto Academy?",
            "Leaving a tutorial means you will have to restart the course from the beginning next time you launch it.<br><br>Are you sure you want to exit?",
        ):
            self.skip_requested.emit()

    # ── Public API ────────────────────────────────────────────────────────────

    def show_completion_screen(self, course_id: str, badge_reward: str) -> None:
        """Renders the sleek, professional completion overlay."""
        if not self._is_alive():
            return

        self.show()

        if hasattr(self, "completion_container"):
            self.completion_container.deleteLater()

        from .course_complete_overlay import CourseCompleteOverlay

        if course_id in self.custom_completion_factories:
            self.completion_container = self.custom_completion_factories[course_id](self)
            # Assuming custom factories return an object that matches the interface:
            # .dismissed signal and .show_completion(course_id, badge_reward) method.
        else:
            self.completion_container = CourseCompleteOverlay(self)

        self.completion_container.dismissed.connect(self._close_completion_screen)

        self.bubble_container.hide()
        self.cyto.hide()
        self.target_rects = []
        self._update_mask()
        self.update()

        self.completion_container.setGeometry(self.rect())
        self.completion_container.show_completion(course_id, badge_reward)

    def _center_completion_container(self) -> None:
        if self.completion_container is not None and self.completion_container.isVisible():
            cx = (self.width() - self.completion_container.width()) // 2
            cy = (self.height() - self.completion_container.height()) // 2
            self.completion_container.move(cx, cy)

    def _close_completion_screen(self) -> None:
        if hasattr(self, "completion_container"):
            self.completion_container.hide()
        self.hide()

    def show_text(self, text: str) -> None:
        """Update only the bubble text label (called from timer loop)."""
        self.text_label.setText(text)
        self._force_resize()

    def set_dark_mode(self, enabled: bool) -> None:
        """Forces the overlay into a pure dark screen (no cyto, no bubble, no holes)."""
        if not self._is_alive():
            return
        if getattr(self, "_dark_mode_enabled", None) == enabled:
            return
        self._dark_mode_enabled = enabled

        if enabled:
            self.cyto.hide()
            self.bubble_container.hide()
            self.target_rects = []
            self.clearMask()
        else:
            self.cyto.show()
            self.bubble_container.show()
            self._update_mask()
        self.update()

    def set_progress(self, current: int, total: int, phase_name: str = "") -> None:
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(current)
        label = f"Step {current} of {total}"
        if phase_name:
            label += f"  —  {phase_name}"
        self.lbl_progress.setText(label)

    def render_step(self, step: BaseStep | None) -> None:
        """Full render of a step — called by event_bus on ACADEMY_STEP_CHANGED."""
        if not self._is_alive():
            return

        if not step:
            if hasattr(self, "completion_container") and self.completion_container.isVisible():
                return
            self.hide()
            return

        self.current_step = step
        self.show()
        self.cyto.show()
        self.bubble_container.show()
        self._clear_dynamic_content()
        self._populate_default_buttons()

        # Update progress bar
        course = self._academy_manager.active_course
        if course and course.steps:
            main_path = course.get_main_path()
            total = len(main_path)
            if step.id in main_path:
                current = main_path.index(step.id) + 1
                self._last_main_step_idx = current
            else:
                current = getattr(self, "_last_main_step_idx", 1)
            self.set_progress(current, max(total, 1))
        self._update_text_rendering(step.text)

        emotion = getattr(step, "cyto_emotion", "idle")
        self.cyto.set_emotion(emotion)
        if step.cyto_animation:
            self.cyto.play_animation(step.cyto_animation)

        # Step-type-specific button configuration
        if isinstance(step, InfoStep):
            self.btn_next.show()
            self.btn_next.setText("Next →")

            if getattr(step, "id", None) == "handoff_return_home":
                self.btn_next.hide()

        elif isinstance(step, InteractionStep):
            # Auto-advances when the target widget fires its signal.
            self.btn_next.hide()
            if getattr(step, "show_waiting_indicator", False):
                self._render_waiting_indicator()

        elif isinstance(step, VerificationStep):
            self.btn_next.hide()

        elif isinstance(step, BranchingStep):
            self.btn_next.hide()
            self._render_branching_options(step.options)

        elif isinstance(step, ConsentStep):
            self.btn_next.hide()
            self._render_consent_options(step)

        elif isinstance(step, ForcedInteractionStep):
            self.btn_next.hide()
            self._render_checklist(step)

        elif isinstance(step, SubplotCheckStep):
            self.btn_next.show()
            self.btn_next.setText("Confirm Subplot")

        elif isinstance(step, WaitForEventStep):
            # Auto-advances; no Next button needed. Show a waiting indicator.
            self.btn_next.hide()
            self._render_waiting_indicator()

        if getattr(step, "manual_dismiss_bubble", False):
            self.btn_dismiss_bubble.show()
        else:
            self.btn_dismiss_bubble.hide()
        self._force_resize()
        self._update_mask()
        self._reposition_cyto_and_bubble(getattr(self, "target_rects", []))

        # Freshly-added checklist widgets (ForcedInteractionStep) don't always
        # report a settled sizeHint() on this same synchronous pass, which can
        # leave bubble_container undersized and the checklist overlapping the
        # header text. A deferred follow-up, once the event loop has caught
        # up, corrects any residual sizing/position drift — same "allow
        # layout to settle" pattern used for the very first render below.
        from PyQt6.QtCore import QTimer

        def _settle() -> None:
            if not self._is_alive():
                return
            self._force_resize()
            self._update_mask()
            self._reposition_cyto_and_bubble(getattr(self, "target_rects", []))

        QTimer.singleShot(0, _settle)

        hide_after = getattr(step, "hide_bubble_after_ms", None)
        if hide_after is not None:

            def _hide_bubble() -> None:
                if not self._is_alive() or self.current_step is not step:
                    return
                self.cyto.hide()
                self.bubble_container.hide()
                self._update_mask()

            QTimer.singleShot(hide_after, _hide_bubble)

    # ── Spotlight geometry ────────────────────────────────────────────────────

    def set_targets(self, rects: list[QRect]) -> None:
        """Sets spotlight rectangles (in overlay-local coordinates)."""
        if self.target_rects == rects:
            return
        self.target_rects = rects
        self._reposition_cyto_and_bubble(rects)
        self._update_mask()
        self.update()  # schedule repaint

    def _reposition_cyto_and_bubble(self, rects: list[QRect]) -> None:
        """Move Cyto and bubble so they don't overlap spotlight holes.

        In ``compact_mode`` (hub launcher) Cyto is hidden and the bubble is
        centred in the overlay — no complex geometry needed.
        """
        if self._compact_mode or not rects:
            # ── Centered layout: Cyto and bubble as a side-by-side pair ────────
            self.cyto.show()
            self.bubble_container.show()
            self._force_resize()
            bubble_w = self.bubble_container.sizeHint().width()
            bubble_h = self.bubble_container.sizeHint().height()

            # Cyto's visual right edge is roughly cx + 240.
            # We place the bubble at cx + 240 so they sit side-by-side.
            total_w = 240 + bubble_w

            # Centre the pair horizontally
            start_x = max(10, (self.width() - total_w) // 2)
            cx = start_x
            bx = start_x + 240

            cyto_h = 400
            pair_h = max(cyto_h, bubble_h)

            # Centre vertically
            start_y = max(10, (self.height() - pair_h) // 2)
            cy = start_y + (pair_h - cyto_h) // 2
            by = start_y + (pair_h - bubble_h) // 2 + 30  # shift bubble slightly down for balance

            self.cyto.move(cx, cy)
            self.cyto.point_at(10)  # Point towards bubble

            self.bubble_container.move(bx, by)

            self.cyto.raise_()
            self.bubble_container.raise_()
            return

        primary = rects[0]

        # Compute the union bounding box of ALL targets to ensure Cyto avoids all of them
        union_rect = rects[0]
        for r in rects[1:]:
            union_rect = union_rect.united(r)

        cyto_x = union_rect.x() + union_rect.width() + 40
        cyto_y = max(20, primary.y() - 120)

        # If not enough room on the right, try the left
        if cyto_x + 320 > self.width():
            cyto_x = union_rect.x() - 350
            # If not enough room on the left either, put Cyto above or below the entire block
            if cyto_x < 20:
                cyto_x = max(20, union_rect.x() + (union_rect.width() // 2) - 150)
                if union_rect.y() + union_rect.height() + 400 < self.height():
                    cyto_y = union_rect.y() + union_rect.height() + 40
                else:
                    cyto_y = max(20, union_rect.y() - 400)

        # If any target is massive (like the plot canvas), move Cyto to the left sidebar
        if any(r.width() > self.width() * 0.5 for r in rects):
            cyto_x = 20
            cyto_y = max(20, self.height() - 400)

        # Point Cyto's arm at target centre
        target_cx = primary.center().x()
        target_cy = primary.center().y()
        arm_x = cyto_x + 150 + 25
        arm_y = cyto_y + 250 + 10
        dx = target_cx - arm_x
        dy = target_cy - arm_y
        dist = math.hypot(dx, dy)
        target_angle = math.degrees(math.atan2(dy, dx))
        if dist > 47:
            angle = target_angle + math.degrees(math.acos(min(1.0, 47 / dist)))
        else:
            angle = target_angle + 90
        self.cyto.point_at(angle)

        self.cyto.move(int(cyto_x), int(cyto_y))

        # Bubble sits below Cyto by default
        bubble_x = cyto_x
        bubble_y = cyto_y + 370
        bubble_w = self.bubble_container.sizeHint().width()
        bubble_h = self.bubble_container.sizeHint().height()

        # Clamp to screen
        if bubble_x + bubble_w > self.width():
            bubble_x = self.width() - bubble_w - 20
        if bubble_y + bubble_h > self.height():
            # If placing below Cyto pushes it off-screen, place it above Cyto's visible face.
            # Cyto's face is around y=150. We want the bottom of the bubble (bubble_y + bubble_h)
            # to be above y=150, leaving a small gap.
            bubble_y = max(10, cyto_y + 130 - bubble_h)

        # Keep bubble out of spotlight holes (unless hole is huge)
        bubble_rect = QRect(int(bubble_x), int(bubble_y), bubble_w, bubble_h)
        for r in rects:
            if r.width() > self.width() * 0.6 or r.height() > self.height() * 0.6:
                continue  # Ignore massive holes like FlowCanvas
            if bubble_rect.intersects(r):
                if r.x() > self.width() / 2:
                    bubble_x = min(bubble_x, r.x() - bubble_w - 20)
                else:
                    bubble_x = max(bubble_x, r.right() + 20)

        bubble_x = max(10, min(bubble_x, self.width() - bubble_w - 10))

        self.bubble_container.move(int(bubble_x), int(bubble_y))

    # ── Input Events ──────────────────────────────────────────────────────────

    def wheelEvent(self, event) -> None:
        if getattr(self, "current_step", None) and getattr(self.current_step, "allow_scroll", False):
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            from PyQt6.QtWidgets import QApplication

            widget = QApplication.widgetAt(event.globalPosition().toPoint())
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            if widget and widget != self:
                QApplication.sendEvent(widget, event)
                return
        event.accept()

    # ── Painting & masking ────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        """Paints the dimmed overlay and spotlight borders around target regions."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        allow = getattr(self.current_step, "allow_interaction", False)
        dim = QColor(0, 0, 0, 160)

        if self.target_rects:
            # Paint dim as a set of rects that surrounds the holes — never paint OVER holes.
            # This avoids CompositionMode_Clear which produces a white fill on non-translucent widgets.
            full = self.rect()
            holes = QRegion()
            for r in self.target_rects:
                holes = holes.united(QRegion(r))

            # Clip the painter to everything EXCEPT the holes, then fill
            dim_region = QRegion(full).subtracted(holes)
            painter.setClipRegion(dim_region)
            painter.fillRect(full, dim)
            painter.setClipping(False)

            # Cyan glow border around each hole
            glow_pen = QPen(QColor(88, 166, 255, 90))
            glow_pen.setWidth(8)
            painter.setPen(glow_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for r in self.target_rects:
                painter.drawRoundedRect(r.adjusted(-4, -4, 4, 4), 8, 8)

            solid_pen = QPen(QColor(Colors.ACCENT_PRIMARY))
            solid_pen.setWidth(2)
            painter.setPen(solid_pen)
            for r in self.target_rects:
                painter.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 5, 5)

        elif not allow:
            # No targets and not interactive — full dim
            painter.fillRect(self.rect(), dim)

        # else: allow_interaction with no targets — no dimming (pass-through mode)

        painter.end()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_mask()
        self._center_completion_container()

    def _update_mask(self) -> None:
        """Build a widget mask so mouse events pass through to target areas."""
        allow = getattr(self.current_step, "allow_interaction", False)
        if isinstance(self.current_step, (InteractionStep, ForcedInteractionStep)):
            allow = True

        if self.target_rects and allow:
            # Mask = whole overlay MINUS the spotlight holes (holes are click-through)
            full = QRegion(self.rect())
            holes = QRegion()
            for r in self.target_rects:
                holes = holes.united(QRegion(r))

            mask = full.subtracted(holes)

            # Re-add Cyto and Bubble so they aren't erased by the holes —
            # unless a hide_bubble_after_ms step has hidden them, in which
            # case reserving that space would leave a dead click zone with
            # nothing visibly there to explain it.
            if self.cyto.isVisible():
                mask = mask.united(QRegion(self.cyto.geometry()))
            if self.bubble_container.isVisible():
                mask = mask.united(QRegion(self.bubble_container.geometry()))

            self.setMask(mask)

        elif allow:
            # No specific targets but interaction is allowed — only the bubble
            # and Cyto block clicks; everything else is pass-through.
            mask = QRegion()
            if self.cyto.isVisible():
                mask = mask.united(QRegion(self.cyto.geometry()))
            if self.bubble_container.isVisible():
                mask = mask.united(QRegion(self.bubble_container.geometry()))
            self.setMask(mask)

        else:
            # Full lock — no clicks through at all
            self.clearMask()

    # ── Checklist (ForcedInteractionStep) ────────────────────────────────────

    def _render_checklist(self, step: ForcedInteractionStep) -> None:
        for task in step.sub_tasks:
            lbl = QLabel(f"☐  {task.instruction}")
            lbl.setObjectName(f"subtask_{task.id}")
            lbl.setWordWrap(True)
            lbl.setFixedWidth(CONTENT_WIDTH)
            theme_manager.apply_style(lbl, "color: {FG_PRIMARY}; font-size: 13px; margin-left: 8px;")
            self.dynamic_content.addWidget(lbl)

    def _on_subtask_completed(self, subtask_id: str, remaining_count: int) -> None:
        if not self._is_alive():
            return
        if not isinstance(self.current_step, ForcedInteractionStep):
            return
        for i in range(self.dynamic_content.count()):
            widget = self.dynamic_content.itemAt(i).widget()
            if widget and widget.objectName() == f"subtask_{subtask_id}":
                widget.setText(widget.text().replace("☐", "✅"))
                theme_manager.apply_style(
                    widget,
                    "color: {FG_PRIMARY}; font-size: 13px; margin-left: 8px; font-weight: bold;",
                )
        if remaining_count == 0 and not getattr(self.current_step, "auto_advance_when_complete", False):
            self.btn_next.show()

    def _render_branching_options(self, options: dict) -> None:
        """Render branching-option buttons that advance to their selected tutorial steps.

        Parameters:
            options (dict): Mapping of option labels to target step identifiers.
        """
        self._clear_buttons()
        btn_style = (
            "background-color: #1f6feb; color: white; border: none;"
            "border-radius: 4px; padding: 8px 14px; font-weight: bold;"
        )

        for text, target_id in options.items():
            btn = QPushButton(text.replace("btn_", "").replace("_", " ").title())
            theme_manager.apply_style(btn, btn_style)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            # Use default argument capture for target_id inside the lambda
            btn.clicked.connect(lambda _checked, tid=target_id: self._academy_manager.next_step(tid))
            self.btn_layout.addWidget(btn)

    def _render_waiting_indicator(self) -> None:
        """Add a static 'waiting' label in the footer button slot (where Next normally sits)."""
        wait_lbl = QLabel("⏳  Waiting for your action…")
        wait_lbl.setObjectName("waitingIndicator")
        theme_manager.apply_style(wait_lbl, "color: {ACCENT_PRIMARY}; font-size: 12px; font-style: italic;")
        self.btn_layout.addWidget(wait_lbl)

    def _render_consent_options(self, step: ConsentStep) -> None:
        """Render explicit Accept/Decline buttons for a ConsentStep."""
        self._clear_buttons()

        btn_decline = QPushButton(step.decline_text)
        theme_manager.apply_style(
            btn_decline,
            "background-color: transparent; color: {FG_SECONDARY}; border: 1px solid {BORDER}; border-radius: 4px; padding: 8px 14px;",
        )
        btn_decline.setCursor(Qt.CursorShape.PointingHandCursor)
        if step.on_decline_step_id:
            btn_decline.clicked.connect(
                lambda _checked, tid=step.on_decline_step_id: self._academy_manager.next_step(tid)
            )
        self.btn_layout.addWidget(btn_decline)

        btn_accept = QPushButton(step.accept_text)
        theme_manager.apply_style(
            btn_accept,
            "background-color: {ACCENT_SUCCESS}; color: white; border: none; border-radius: 4px; padding: 8px 14px; font-weight: bold;",
        )
        btn_accept.setCursor(Qt.CursorShape.PointingHandCursor)
        if step.on_accept_step_id:
            btn_accept.clicked.connect(
                lambda _checked, tid=step.on_accept_step_id: self._academy_manager.next_step(tid)
            )
        self.btn_layout.addWidget(btn_accept)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _populate_default_buttons(self) -> None:
        self._clear_buttons()
        self.btn_layout.addStretch()
        self.btn_layout.addWidget(self.btn_dismiss_bubble)
        self.btn_layout.addWidget(self.btn_next)

    def _clear_buttons(self) -> None:
        persistent = (getattr(self, "btn_next", None), getattr(self, "btn_dismiss_bubble", None))
        while self.btn_layout.count():
            item = self.btn_layout.takeAt(0)
            if item is None:
                break
            w = item.widget()
            if w is not None:
                if w in persistent:
                    w.hide()
                    w.setParent(None)  # type: ignore[arg-type]
                else:
                    w.deleteLater()

    def _dismiss_bubble(self) -> None:
        """Manually hides Cyto's bubble — the click-driven counterpart to
        the hide_bubble_after_ms timer, for steps that would rather let the
        user decide when they're done reading than hide on a fixed clock.
        """
        self.cyto.hide()
        self.bubble_container.hide()
        self.btn_dismiss_bubble.hide()
        self._update_mask()

    def _clear_dynamic_content(self) -> None:
        # Stop any running WaitForEventStep pulse timer
        if hasattr(self, "_wait_pulse_timer") and self._wait_pulse_timer.isActive():
            self._wait_pulse_timer.stop()
        while self.dynamic_content.count():
            item = self.dynamic_content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _force_resize(self) -> None:
        # Reset minimum height first so we don't infinitely compound
        self.text_label.setMinimumHeight(0)

        # QFontMetrics doesn't accurately measure HTML rich text.
        # We must use QTextDocument to get the exact rendered height of the rich text.
        from PyQt6.QtGui import QTextDocument

        doc = QTextDocument()
        doc.setDefaultFont(self.text_label.font())
        doc.setHtml(self.text_label.text())
        doc.setTextWidth(CONTENT_WIDTH)

        # Add buffer to account for stylesheet padding and macOS line-height quirks
        required_height = int(doc.size().height()) + _HEADER_TEXT_HEIGHT_BUFFER
        self.text_label.setMinimumHeight(required_height)

        self.text_label.updateGeometry()
        self.dynamic_content.invalidate()
        self.body_layout.invalidate()
        self.body_container.updateGeometry()
        self.bubble_layout.invalidate()
        self.bubble_container.updateGeometry()

        # Force layouts to activate and calculate their sizes synchronously
        self.bubble_layout.activate()
        self.bubble_container.resize(self.bubble_layout.sizeHint())
