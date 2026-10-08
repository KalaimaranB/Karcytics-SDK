"""AcademyCatalogWindow — the Course Hub dialog for a plugin's own Academy.

Ported from the Hub's original global `AcademyWindow` (course cards over an
animated particle-network background, progress pills, earned badges) to the
isolated-plugin world:

  - No global multi-module view — each plugin only ever sees its own courses,
    registered on its own process-local `AcademyManager`
    (`runtime_services.tutorial_manager`) — so this dialog is always scoped
    to exactly one `module_id`, with no "Enter Module to Start" disabled
    state (that only existed for courses belonging to a module other than
    the one currently open).
  - No `core_intro` registration or special-cased course ID — that course
    belongs to the Hub, not any plugin.
  - Course launching is a callback (`on_start_course`) rather than calling
    `tutorial_manager.start_course()` directly — that method only emits
    `ACADEMY_COURSE_PREPARE_PROJECT` and expects a listener to call
    `start_course_confirmed()` back, which relied on the Hub's own
    always-on event loop. An isolated plugin's `RemoteEventBus.subscribe()`
    is a documented no-op, so nothing would ever receive that event; see
    `academy_driver.open_academy()`, which owns starting the course and
    showing the `TutorialOverlay` the same way its Help > Academy entry
    point already does.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QPointF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .theme_fallback import Colors, Fonts, theme_manager

_COURSE_COMPLETE_PROGRESS = 100.0


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    if len(h) == 6:
        return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, {alpha})"
    return hex_color


class _Particle:
    def __init__(self, w: float, h: float) -> None:
        self.x = random.uniform(0, w)
        self.y = random.uniform(0, h)
        self.vx = random.uniform(-0.5, 0.5)
        self.vy = random.uniform(-0.5, 0.5)
        self.radius = random.uniform(1.5, 3.5)

    def update(self, w: float, h: float) -> None:
        self.x += self.vx
        self.y += self.vy
        if self.x < 0 or self.x > w:
            self.vx *= -1
        if self.y < 0 or self.y > h:
            self.vy *= -1


class AcademyCatalogWindow(QDialog):
    """The Course Hub for one plugin's own registered Academy courses.

    Shows a card per course (title, description, progress pill, earned-badge
    pill, Start/Review + Reset buttons) over an animated particle-network
    background, plus any badges this plugin's `AcademyManager` has already
    awarded. `on_start_course(course_id)` fires when the user picks a course
    — the caller owns actually starting it and showing the `TutorialOverlay`,
    so this dialog stays a pure picker with no knowledge of overlay wiring.
    """

    def __init__(
        self,
        tutorial_manager: Any,
        module_id: str,
        on_start_course: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tutorial_manager = tutorial_manager
        self.module_id = module_id
        self._on_start_course = on_start_course

        self.setWindowTitle(f"Karcytics Academy - {module_id.replace('_', ' ').title()} Courses")
        self.setMinimumSize(800, 500)
        # No stylesheet background — paintEvent below draws the particles.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.particles = [_Particle(800, 500) for _ in range(40)]
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate_particles)
        self._timer.start(30)  # ~33 fps

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 20)
        layout.setSpacing(20)

        header_layout = QHBoxLayout()
        self.header = QLabel("Available Courses")
        self.header_desc = QLabel("Master the techniques of bio analysis.")
        header_vbox = QVBoxLayout()
        header_vbox.addWidget(self.header)
        header_vbox.addWidget(self.header_desc)
        header_layout.addLayout(header_vbox)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        self.scroll: QScrollArea = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        theme_manager.apply_style(self.scroll, "QScrollArea { background: transparent; }")

        self.scroll_content = QWidget()
        theme_manager.apply_style(self.scroll_content, "background: transparent;")
        self.cards_layout = QVBoxLayout(self.scroll_content)
        self.cards_layout.setContentsMargins(10, 10, 10, 10)
        self.cards_layout.setSpacing(20)
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll)

        btn_layout = QHBoxLayout()
        # The character only — courses, the bubble and spotlights run the
        # same either way. Persisted per plugin by the AcademyManager.
        self.show_character_check = QCheckBox("Show Cyto character during courses")
        self.show_character_check.setObjectName("AcademyShowCharacterCheck")
        self.show_character_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_character_check.setToolTip(
            "Turn off to hide Cyto himself. Course steps and instructions still appear."
        )
        self.show_character_check.setChecked(bool(getattr(tutorial_manager, "show_character", True)))
        self.show_character_check.toggled.connect(self._on_show_character_toggled)
        # A manager without the setting (an older Hub-side one) can't honour it.
        self.show_character_check.setVisible(hasattr(tutorial_manager, "set_show_character"))
        btn_layout.addWidget(self.show_character_check)
        btn_layout.addStretch()
        self.close_btn = QPushButton("Close")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self._apply_styles()
        theme_manager.theme_changed.connect(self._apply_styles)

    def _apply_styles(self) -> None:
        theme_manager.apply_style(
            self.header,
            f"color: {Colors.ACCENT_PRIMARY}; font-size: {Fonts.SIZE_XLARGE}px;"
            f" font-weight: bold; background: transparent;",
        )
        theme_manager.apply_style(
            self.header_desc,
            f"color: {Colors.FG_SECONDARY}; font-size: {Fonts.SIZE_NORMAL}px; background: transparent;",
        )
        theme_manager.apply_style(
            self.show_character_check,
            f"QCheckBox {{ color: {Colors.FG_SECONDARY}; font-size: {Fonts.SIZE_NORMAL}px;"
            f" background: transparent; spacing: 8px; }}",
        )
        theme_manager.apply_style(
            self.close_btn,
            f"""
            QPushButton {{
                background-color: {Colors.BG_MEDIUM};
                color: {Colors.FG_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 8px 24px;
                font-size: {Fonts.SIZE_NORMAL}px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BG_LIGHT};
                border: 1px solid {Colors.BORDER_FOCUS};
            }}
            """,
        )
        self._populate_courses()

    def _on_show_character_toggled(self, checked: bool) -> None:
        self.tutorial_manager.set_show_character(checked)

    def _animate_particles(self) -> None:
        w, h = self.width(), self.height()
        for p in self.particles:
            p.update(w, h)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802
        """Render the dialog background with connected, colored particles."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(Colors.BG_DARKEST))

        max_dist = 120
        pen = QPen(QColor(Colors.DNA_PRIMARY))
        for i, p1 in enumerate(self.particles):
            for p2 in self.particles[i + 1 :]:
                dist = math.hypot(p1.x - p2.x, p1.y - p2.y)
                if dist < max_dist:
                    opacity = 1.0 - (dist / max_dist)
                    c = QColor(Colors.DNA_PRIMARY)
                    c.setAlphaF(opacity * 0.4)
                    pen.setColor(c)
                    pen.setWidthF(1.5)
                    painter.setPen(pen)
                    painter.drawLine(QPointF(p1.x, p1.y), QPointF(p2.x, p2.y))

        painter.setPen(Qt.PenStyle.NoPen)
        for i, p in enumerate(self.particles):
            c = QColor(Colors.DNA_PRIMARY) if i % 2 == 0 else QColor(Colors.DNA_SECONDARY)
            c.setAlpha(150)
            painter.setBrush(QBrush(c))
            painter.drawEllipse(QPointF(p.x, p.y), p.radius, p.radius)

    def _populate_courses(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        badges = getattr(self.tutorial_manager, "badges", [])
        if badges:
            badges_lbl = QLabel("🏆 Earned Badges")
            theme_manager.apply_style(
                badges_lbl,
                f"color: {Colors.ACCENT_WARNING}; font-size: {Fonts.SIZE_LARGE}px;"
                f" font-weight: bold; padding-top: 10px;",
            )
            self.cards_layout.addWidget(badges_lbl)

            badges_container = QWidget()
            badges_layout = QHBoxLayout(badges_container)
            badges_layout.setContentsMargins(0, 0, 0, 0)
            badges_layout.setSpacing(10)

            # Use a set to avoid duplicates if any.
            unique_badges = set()
            for b in badges:
                if isinstance(b, dict):
                    icon = b.get("icon", "🏅")
                    label = b.get("label", b.get("id", "Badge"))
                    unique_badges.add(f"{icon} {label}")
                else:
                    unique_badges.add(str(b))

            for b_text in sorted(unique_badges):
                b_lbl = QLabel(b_text)
                theme_manager.apply_style(
                    b_lbl,
                    f"background-color: {Colors.BG_DARK}; color: {Colors.FG_PRIMARY};"
                    f" border: 1px solid {Colors.ACCENT_WARNING}; border-radius: 8px;"
                    f" padding: 8px 16px; font-size: {Fonts.SIZE_NORMAL}px; font-weight: bold;",
                )
                badges_layout.addWidget(b_lbl)
            badges_layout.addStretch()
            self.cards_layout.addWidget(badges_container)
            self.cards_layout.addSpacing(20)

        courses = self.tutorial_manager.get_courses_for_module(self.module_id)
        if not courses:
            lbl = QLabel("No courses implemented yet. Check back soon!")
            theme_manager.apply_style(
                lbl,
                f"color: {Colors.FG_SECONDARY}; font-size: {Fonts.SIZE_LARGE}px; font-weight: bold;",
            )
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.cards_layout.addWidget(lbl)
        else:
            for course in courses:
                self.cards_layout.addWidget(self._create_course_card(course))

        self.cards_layout.addStretch()

    def _create_course_card(self, course: Any) -> QWidget:
        card = QFrame()
        card.setObjectName("CourseCard")
        bg_dark_rgba = _hex_to_rgba(Colors.BG_DARK, 0.85)
        bg_medium_rgba = _hex_to_rgba(Colors.BG_MEDIUM, 0.95)
        theme_manager.apply_style(
            card,
            f"""
            QFrame#CourseCard {{
                background-color: {bg_dark_rgba};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
            QFrame#CourseCard:hover {{
                border: 1px solid {Colors.BORDER_FOCUS};
                background-color: {bg_medium_rgba};
            }}
            """,
        )

        layout = QHBoxLayout(card)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(8)

        title = QLabel(course.title)
        theme_manager.apply_style(
            title,
            f"color: {Colors.FG_PRIMARY}; font-size: {Fonts.SIZE_LARGE}px; font-weight: bold; background: transparent;",
        )
        info_layout.addWidget(title)

        if getattr(course, "description", None):
            desc = QLabel(course.description)
            theme_manager.apply_style(
                desc,
                f"color: {Colors.FG_SECONDARY}; font-size: {Fonts.SIZE_NORMAL}px; background: transparent;",
            )
            desc.setWordWrap(True)
            info_layout.addWidget(desc)

        progress = self.tutorial_manager.get_progress(course.id)
        status_layout = QHBoxLayout()
        status_layout.setSpacing(12)

        status_pill = QLabel()
        if progress >= _COURSE_COMPLETE_PROGRESS:
            status_pill.setText(" COMPLETED ")
            success_rgba = _hex_to_rgba(Colors.ACCENT_SUCCESS, 0.2)
            theme_manager.apply_style(
                status_pill,
                f"background-color: {success_rgba}; color: {Colors.ACCENT_SUCCESS};"
                f" border: 1px solid {Colors.ACCENT_SUCCESS}; border-radius: 10px;"
                f" padding: 4px 8px; font-size: {Fonts.SIZE_SMALL}px; font-weight: bold;",
            )
        else:
            status_pill.setText(" IN PROGRESS " if progress > 0 else " NOT STARTED ")
            secondary_rgba = _hex_to_rgba(Colors.FG_SECONDARY, 0.1)
            theme_manager.apply_style(
                status_pill,
                f"background-color: {secondary_rgba}; color: {Colors.FG_SECONDARY};"
                f" border: 1px solid {Colors.BORDER}; border-radius: 10px;"
                f" padding: 4px 8px; font-size: {Fonts.SIZE_SMALL}px; font-weight: bold;",
            )
        status_layout.addWidget(status_pill)

        if progress >= _COURSE_COMPLETE_PROGRESS and getattr(course, "badge_reward", None):
            badge = QLabel(f" AWARD: {course.badge_reward} ")
            warning_rgba = _hex_to_rgba(Colors.ACCENT_WARNING, 0.15)
            theme_manager.apply_style(
                badge,
                f"background-color: {warning_rgba}; color: {Colors.ACCENT_WARNING};"
                f" border: 1px solid {Colors.ACCENT_WARNING}; border-radius: 10px;"
                f" padding: 4px 8px; font-size: {Fonts.SIZE_SMALL}px; font-weight: bold;",
            )
            status_layout.addWidget(badge)

        status_layout.addStretch()
        info_layout.addLayout(status_layout)
        layout.addLayout(info_layout, stretch=1)

        action_layout = QVBoxLayout()
        action_layout.setSpacing(10)
        action_layout.addStretch()

        action_btn = QPushButton("Start Course" if progress < _COURSE_COMPLETE_PROGRESS else "Review Course")
        action_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        if progress < _COURSE_COMPLETE_PROGRESS:
            success_hover_rgba = _hex_to_rgba(Colors.ACCENT_SUCCESS, 0.8)
            theme_manager.apply_style(
                action_btn,
                f"""
                QPushButton {{
                    background-color: {Colors.ACCENT_SUCCESS};
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 10px 24px;
                    font-size: {Fonts.SIZE_NORMAL}px;
                    font-weight: bold;
                }}
                QPushButton:hover {{ background-color: {success_hover_rgba}; }}
                """,
            )
        else:
            theme_manager.apply_style(
                action_btn,
                f"""
                QPushButton {{
                    background-color: {Colors.BG_MEDIUM};
                    color: {Colors.FG_PRIMARY};
                    border: 1px solid {Colors.BORDER_FOCUS};
                    border-radius: 6px;
                    padding: 10px 24px;
                    font-size: {Fonts.SIZE_NORMAL}px;
                    font-weight: bold;
                }}
                QPushButton:hover {{ background-color: {Colors.BORDER_FOCUS}; color: #ffffff; }}
                """,
            )

        action_btn.clicked.connect(lambda _, cid=course.id: self._start_course(cid))
        action_layout.addWidget(action_btn)

        if progress >= _COURSE_COMPLETE_PROGRESS:
            reset_btn = QPushButton("Reset Progress")
            reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            danger_rgba = _hex_to_rgba(Colors.ACCENT_DANGER, 0.1)
            theme_manager.apply_style(
                reset_btn,
                f"""
                QPushButton {{
                    background-color: transparent;
                    color: {Colors.ACCENT_DANGER};
                    border: 1px solid {Colors.ACCENT_DANGER};
                    border-radius: 4px;
                    padding: 4px 10px;
                    font-size: {Fonts.SIZE_SMALL}px;
                }}
                QPushButton:hover {{ background-color: {danger_rgba}; }}
                """,
            )
            reset_btn.clicked.connect(lambda _, cid=course.id: self._reset_course(cid))
            action_layout.addWidget(reset_btn, alignment=Qt.AlignmentFlag.AlignRight)

        action_layout.addStretch()
        layout.addLayout(action_layout)
        return card

    def _start_course(self, course_id: str) -> None:
        self._on_start_course(course_id)
        self.accept()

    def _reset_course(self, course_id: str) -> None:
        if hasattr(self.tutorial_manager, "reset_course"):
            self.tutorial_manager.reset_course(course_id)
            self._populate_courses()
