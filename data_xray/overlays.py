"""Loading belongs to the workspace; its fade never changes window opacity."""
from PySide6.QtCore import Qt, QTimer, QElapsedTimer, QPropertyAnimation, QEasingCurve, QRectF, QEvent
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QGraphicsOpacityEffect


class LoadingOverlay(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("loadingOverlay")
        self.setStyleSheet("QFrame#loadingOverlay{background:#181e27;border:1px solid #df8548;border-radius:12px;}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 68, 40, 40)
        layout.addStretch()
        self.title = QLabel("Изучаем данные")
        self.title.setObjectName("section")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stage = QLabel("Чтение файла…")
        self.stage.setWordWrap(True)
        self.stage.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint = QLabel("Можно отменить анализ кнопкой слева")
        self.hint.setObjectName("muted")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for label in (self.title, self.stage, self.hint):
            layout.addWidget(label)
        layout.addStretch()
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.fade = QPropertyAnimation(self.effect, b"opacity", self)
        self.fade.setDuration(210)
        self.fade.setStartValue(1.)
        self.fade.setEndValue(0.)
        self.fade.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.fade.finished.connect(self._hide_finished)
        self.delay = QTimer(self)
        self.delay.setSingleShot(True)
        self.delay.timeout.connect(self._fade_out)
        self.pulse = QTimer(self)
        self.pulse.setInterval(40)
        self.pulse.timeout.connect(self._tick)
        self.elapsed = QElapsedTimer()
        self.generation = 0
        self.fade_generation = -1
        self.state = "idle"
        self.angle = 0
        self.hide()

    def begin(self, target):
        self.generation += 1
        self.delay.stop()
        self.fade.stop()
        self.state = "loading"
        self.effect.setOpacity(1.)
        self.title.setText("Изучаем данные")
        self.stage.setText("Чтение файла…")
        self.hint.setText("Можно отменить анализ кнопкой слева")
        self.target = target
        target.installEventFilter(self)
        self.setGeometry(target.geometry())
        self.elapsed.start()
        self.pulse.start()
        self.show()
        self.raise_()

    def eventFilter(self, watched, event):
        if watched is getattr(self, "target", None) and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self.setGeometry(watched.geometry())
        return super().eventFilter(watched, event)

    def set_stage(self, value, message):
        if self.state == "loading":
            self.stage.setText(f"{message} · {value}%")

    def finish(self):
        if self.state != "loading":
            return
        self.state = "settling"
        self.delay.start(max(0, 210 - self.elapsed.elapsed()))

    def _fade_out(self):
        if self.state != "settling":
            return
        self.state = "fading"
        self.fade_generation = self.generation
        self.fade.start()

    def _hide_finished(self):
        if self.state == "fading" and self.fade_generation == self.generation:
            self.reset()

    def reset(self):
        self.generation += 1
        self.delay.stop()
        self.fade.stop()
        self.pulse.stop()
        self.state = "idle"
        self.hide()

    def _tick(self):
        self.angle = (self.angle + 12) % 360
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Quiet skeleton rows suggest the table without exposing partial data.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#222b37"))
        for row in range(3):
            for col, fraction in enumerate((.28, .18, .32)):
                x = 30 + col * (self.width() - 60) / 3
                painter.drawRoundedRect(QRectF(x, 28 + row * 18, (self.width() - 60) * fraction, 7), 3, 3)
        center_x, center_y = self.width() / 2, self.title.geometry().top() - 34
        painter.setPen(QPen(QColor("#34404e"), 3))
        rect = QRectF(center_x - 12, center_y - 12, 24, 24)
        painter.drawEllipse(rect)
        painter.setPen(QPen(QColor("#ffb776"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, -self.angle * 16, 95 * 16)
