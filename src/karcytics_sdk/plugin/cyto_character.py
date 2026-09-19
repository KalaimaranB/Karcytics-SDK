"""The Cyto Character Widget for the Karcytics Academy."""

import math
import random

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItemGroup,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)

from .cyto_costumes import CostumeFactory
from .draggable import DraggableMixin
from .effects import apply_glow_effect
from .theme_fallback import theme_manager


class Particle(QGraphicsEllipseItem):
    """Energy particle with additive blending."""

    def __init__(self, x, y):
        super().__init__(-3, -3, 6, 6)
        self.setPos(x, y)
        self.life = 1.0
        self.decay = random.uniform(0.02, 0.05)
        self.dx = random.uniform(-0.8, 0.8)
        self.dy = random.uniform(1.0, 2.5)

        colors = [QColor(121, 192, 255), QColor(56, 139, 253), QColor(88, 166, 255)]
        self.setBrush(QBrush(random.choice(colors)))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setZValue(-2)

    def paint(self, painter, option, widget):
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        super().paint(painter, option, widget)

    def update_particle(self):
        self.moveBy(self.dx, self.dy)
        self.life -= self.decay
        self.setOpacity(max(0, self.life))
        return self.life > 0


class CytoWidget(QGraphicsView, DraggableMixin):
    """A pure rendering widget for the Cyto character."""

    drag_started = pyqtSignal()
    drag_finished = pyqtSignal()
    dragged_by = pyqtSignal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Transparent background so it can float over the overlay
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        theme_manager.apply_style(self, "background: transparent; border: none;")
        self.setBackgroundBrush(QBrush(Qt.BrushStyle.NoBrush))  # critical: prevents QGraphicsView white fill
        self.setFrameStyle(0)  # remove any frame
        self.setFixedSize(300, 400)
        self.scene.setSceneRect(0, 0, 300, 400)
        self._init_draggable()

        self.time_step = 0
        self.particles = []

        # Animation states
        self.is_blinking = False
        self.blink_progress = 0

        self.is_talking = False
        self.talking_timer = 0
        self.target_mouth_open = 2.0
        self.current_mouth_open = 2.0

        self.current_arm_angle = -35.0
        self.target_arm_angle = -35.0
        self.emotion = "happy"

        self.theme_manager = theme_manager
        self.current_costume = None
        self.build_character()

        self.apply_theme()
        theme_manager.theme_changed.connect(self.apply_theme)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(30)

    def build_character(self):
        self.cyto_base_y = 250
        self.cyto_base_x = 150

        self.cyto_group = QGraphicsItemGroup()
        self.scene.addItem(self.cyto_group)
        self.cyto_group.setZValue(10)

        # 1. Left Arm (Background arm)
        self.left_arm = QGraphicsRectItem(-8, -5, 12, 35)
        self.left_arm.setBrush(QBrush(QColor("#1f6feb")))
        self.left_arm.setPen(QPen(QColor("#79c0ff"), 2))
        self.cyto_group.addToGroup(self.left_arm)
        self.left_arm.setPos(-35, 5)
        self.left_arm.setTransformOriginPoint(0, 0)
        self.left_arm.setZValue(-1)

        # 2. Glow Aura
        self.aura = QGraphicsEllipseItem(-65, -65, 130, 130)
        aura_grad = QRadialGradient(0, 0, 65)
        aura_grad.setColorAt(0, QColor(56, 139, 253, 150))
        aura_grad.setColorAt(1, QColor(56, 139, 253, 0))
        self.aura.setBrush(QBrush(aura_grad))
        self.aura.setPen(QPen(Qt.PenStyle.NoPen))
        self.cyto_group.addToGroup(self.aura)
        self.aura.setZValue(0)

        # 3. Main Biological Body (Undulating Path)
        self.body = QGraphicsPathItem()
        grad = QRadialGradient(0, 0, 45)
        grad.setColorAt(0, QColor("#388bfd"))
        grad.setColorAt(0.7, QColor("#1f6feb"))
        grad.setColorAt(1, QColor("#010409"))
        self.body.setBrush(QBrush(grad))
        self.body.setPen(QPen(QColor("#79c0ff"), 3))
        self.cyto_group.addToGroup(self.body)
        self.body.setZValue(1)

        # Receptors
        self.receptors = []
        for i in range(5):
            receptor = QGraphicsRectItem(-3, -55, 6, 15)
            receptor.setBrush(QBrush(QColor("#58a6ff")))
            receptor.setPen(QPen(QColor("#79c0ff"), 1))
            receptor.setRotation(i * 72)
            self.cyto_group.addToGroup(receptor)
            receptor.setZValue(0.5)
            self.receptors.append(receptor)

        # 4. Nucleus / Tech Core
        self.core = QGraphicsEllipseItem(-25, -25, 50, 50)
        core_grad = QRadialGradient(0, 0, 25)
        core_grad.setColorAt(0, QColor("#58a6ff"))
        core_grad.setColorAt(1, QColor(31, 111, 235, 80))
        self.core.setBrush(QBrush(core_grad))
        self.core.setPen(QPen(QColor("#c9d1d9"), 1.5, Qt.PenStyle.DotLine))
        self.cyto_group.addToGroup(self.core)
        self.core.setZValue(2)

        # 5. Eyes
        self.left_eye = QGraphicsEllipseItem(-18, -15, 12, 22)
        self.right_eye = QGraphicsEllipseItem(6, -15, 12, 22)
        eye_brush = QBrush(QColor("#ffffff"))

        self.left_eye.setBrush(eye_brush)
        self.left_eye.setPen(QPen(Qt.PenStyle.NoPen))
        apply_glow_effect(self.left_eye, QColor("#39ff14"), blur_radius=10)
        self.cyto_group.addToGroup(self.left_eye)
        self.left_eye.setZValue(4)

        self.right_eye.setBrush(eye_brush)
        self.right_eye.setPen(QPen(Qt.PenStyle.NoPen))
        apply_glow_effect(self.right_eye, QColor("#39ff14"), blur_radius=10)
        self.cyto_group.addToGroup(self.right_eye)
        self.right_eye.setZValue(4)

        # 5b. Mouth
        self.mouth = QGraphicsRectItem(-8, 14, 16, 2)
        self.mouth.setBrush(QBrush(QColor("#ffffff")))
        self.mouth.setPen(QPen(Qt.PenStyle.NoPen))
        self.cyto_group.addToGroup(self.mouth)
        self.mouth.setZValue(4)

        apply_glow_effect(self.mouth, QColor("#39ff14"), blur_radius=8)

        # 6. Right Arm (Foreground)
        self.right_arm = QGraphicsRectItem(0, -8, 55, 16)
        self.right_arm.setBrush(QBrush(QColor("#388bfd")))
        self.right_arm.setPen(QPen(QColor("#79c0ff"), 2))
        self.cyto_group.addToGroup(self.right_arm)
        self.right_arm.setPos(25, 10)
        self.right_arm.setTransformOriginPoint(0, 0)
        self.right_arm.setZValue(6)

    # --- API Methods ---

    def apply_theme(self):
        """Updates Cyto's costume based on the active theme."""
        if self.current_costume:
            self.current_costume.detach(self)

        theme_name = theme_manager.current_theme_name
        self.current_costume = CostumeFactory.get_costume(theme_name)
        self.current_costume.attach(self)

    def speak(self, text: str):  # noqa: ARG002
        """Starts the character's talking animation."""
        self.is_talking = True
        self.talking_timer = 150  # Roughly 4 seconds of lip flap

    def set_emotion(self, emotion: str):
        """Changes eye expression: 'happy', 'sad', 'thinking', 'surprised', 'scanning', 'cheering', 'idle'."""
        self.emotion = emotion
        if emotion == "cheering":
            self.play_animation("cheering")
        elif emotion == "thinking":
            self.target_arm_angle = -120  # Arm up like hand on chin

    def point_at(self, angle: float):
        """Points the lightsaber arm at a specific target angle."""
        self.target_arm_angle = angle

    def play_animation(self, anim_name: str):
        """Plays a predefined animation sequence."""
        if anim_name == "cheering":
            self.target_arm_angle = -90  # Throw arm straight up in the air
            self.emotion = "happy"
            self.speak("")  # Trigger mouth flapping

    # --- Internal Animation Loop ---

    def update_body_path(self):
        """Update the character body's animated outline."""
        path = QPainterPath()
        base_radius = 45
        points = 40
        for i in range(points + 1):
            angle = (i * 2 * math.pi) / points
            wobble = math.sin(angle * 4 + self.time_step * 3) * 2.5
            wobble += math.cos(angle * 3 - self.time_step * 2) * 1.5
            r = base_radius + wobble

            x = math.cos(angle) * r
            y = math.sin(angle) * r
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        self.body.setPath(path)

    def drawBackground(self, painter, rect):  # noqa: N802
        """Override to prevent QGraphicsView from painting any background fill."""
        pass  # Intentionally empty — transparency handled by overlay paintEvent

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self._handle_drag_press(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._handle_drag_move(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if not self._handle_drag_release(event):
            super().mouseReleaseEvent(event)

    def animate(self):
        self.time_step += 0.05

        self.update_body_path()

        hover_y = self.cyto_base_y + math.sin(self.time_step * 2) * 12
        self.cyto_group.setPos(self.cyto_base_x, hover_y)

        breathe = 1.0 + math.sin(self.time_step * 1.5) * 0.03
        self.core.setScale(breathe)

        aura_pulse = 1.0 + math.sin(self.time_step * 5) * 0.05
        self.aura.setScale(aura_pulse)

        # Blinking
        if not self.is_blinking:
            if random.random() < 0.02:
                self.is_blinking = True
                self.blink_progress = 0
        else:
            self.blink_progress += 0.35
            if self.blink_progress >= math.pi:
                self.is_blinking = False

        if self.is_blinking:
            scale_y = max(0.1, 1.0 - math.sin(self.blink_progress) * 0.9)
            self.left_eye.setRect(-18, -15 + (11 * (1 - scale_y)), 12, 22 * scale_y)
            self.right_eye.setRect(6, -15 + (11 * (1 - scale_y)), 12, 22 * scale_y)
        else:
            # Handle emotions
            if self.emotion in ["happy", "cheering"]:
                # Happy squint: curved up by moving up slightly and squishing less
                self.left_eye.setRect(-18, -18, 12, 14)
                self.right_eye.setRect(6, -18, 12, 14)
            elif self.emotion == "sad":
                # Half-closed drooping eyes
                self.left_eye.setRect(-18, -10, 12, 12)
                self.right_eye.setRect(6, -10, 12, 12)
            elif self.emotion == "surprised":
                # Wide open eyes
                self.left_eye.setRect(-18, -20, 12, 28)
                self.right_eye.setRect(6, -20, 12, 28)
            elif self.emotion == "thinking":
                # Look up and squint slightly
                self.left_eye.setRect(-18, -20, 12, 18)
                self.right_eye.setRect(6, -20, 12, 18)
            elif self.emotion == "scanning":
                # Rapid scan motion
                look_x = math.sin(self.time_step * 8) * 6
                self.left_eye.setRect(-18 + look_x, -15, 12, 22)
                self.right_eye.setRect(6 + look_x, -15, 12, 22)
            else:
                self.left_eye.setRect(-18, -15, 12, 22)
                self.right_eye.setRect(6, -15, 12, 22)

            if self.emotion != "scanning":
                look_x = math.sin(self.time_step * 1.5) * 3
                look_y = math.cos(self.time_step * 1.1) * 2
                if self.emotion == "thinking":
                    look_x -= 4
                    look_y -= 4
                self.left_eye.setPos(look_x, look_y)
                self.right_eye.setPos(look_x, look_y)
                self.mouth.setPos(look_x * 0.5, look_y * 0.5)

            # Sadness causes drooping posture
            if self.emotion == "sad":
                hover_y += 15
                self.cyto_group.setPos(self.cyto_base_x, hover_y)
                self.target_arm_angle = 10

        # Talking Animation
        if self.is_talking:
            self.talking_timer -= 1
            if self.talking_timer <= 0:
                self.is_talking = False
                self.target_mouth_open = 2.0
            elif random.random() < 0.2:
                self.target_mouth_open = random.uniform(2.0, 12.0)
        else:
            self.target_mouth_open = 2.0

        self.current_mouth_open += (self.target_mouth_open - self.current_mouth_open) * 0.3
        self.mouth.setRect(-8, 14 - (self.current_mouth_open / 2 - 1), 16, self.current_mouth_open)

        if self.current_costume:
            self.current_costume.animate(self, self.time_step)

        # Arm Pointing
        if self.emotion == "idle" and random.random() < 0.02:
            self.target_arm_angle = random.uniform(-60, -10)

        self.current_arm_angle += (self.target_arm_angle - self.current_arm_angle) * 0.1
        self.right_arm.setRotation(self.current_arm_angle)

        left_arm_angle = math.cos(self.time_step * 1.2) * 10 + 5
        self.left_arm.setRotation(left_arm_angle)

        # Particles
        particle_chance = 0.5
        if self.emotion == "cheering" or self.emotion == "happy":
            particle_chance = 0.9
        if self.emotion == "sad":
            particle_chance = 0.1

        if random.random() < particle_chance:
            px = self.cyto_base_x + random.uniform(-15, 15)
            py = hover_y + 40
            p = Particle(px, py)
            if self.emotion in ["cheering", "happy"]:
                p.dy = random.uniform(-3.0, -1.0)  # Burst upward
                py = hover_y - 20
                p.setPos(px, py)
            self.scene.addItem(p)
            self.particles.append(p)

        alive_particles = []
        for p in self.particles:
            if p.update_particle():
                alive_particles.append(p)
            else:
                self.scene.removeItem(p)
        self.particles = alive_particles
