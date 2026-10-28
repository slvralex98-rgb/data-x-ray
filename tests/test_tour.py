"""Regression coverage for every laid-out embedded guidance card."""
import os
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from data_xray.ui import MainWindow


def assert_card_readable(card):
    card.layout().activate()
    assert card.parentWidget().rect().contains(card.geometry())
    for label in (card.counter, card.heading, card.description):
        area = label.contentsRect()
        needed = label.heightForWidth(label.width()) if label.wordWrap() else label.sizeHint().height()
        assert label.height() >= needed, (card.step, label.text(), label.height(), needed)
        bounds = label.fontMetrics().boundingRect(area, int(Qt.TextFlag.TextWordWrap) if label.wordWrap() else 0, label.text())
        assert bounds.height() <= area.height(), (card.step, label.text(), bounds, area)
        assert bounds.width() <= area.width(), (card.step, label.text(), bounds, area)
    assert card.counter.height() <= card.counter.sizeHint().height() + 2
    assert card.heading.y() - card.counter.geometry().bottom() <= 10
    assert not card.heading.text()[0].isdigit()
    assert not card.target_rect().isEmpty()
    assert card.target.isVisibleTo(card.parentWidget())
    assert not card.isWindow()
    assert card.highlight.isVisible() and card.pointer.isVisible()


@pytest.mark.parametrize('size', [(1000, 680), (1400, 900)])
@pytest.mark.parametrize('scale', [1.0, 1.2])
def test_every_tour_step_has_unclipped_text_and_visible_anchor(tmp_path, size, scale):
    app = QApplication.instance() or QApplication([])
    if os.name == 'nt':
        for font in ('segoeui.ttf', 'segoeuib.ttf'):
            path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / font
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    settings = QSettings(str(tmp_path / 'tour.ini'), QSettings.Format.IniFormat)
    settings.setValue('help/show_on_startup', False)
    window = MainWindow(settings=settings)
    try:
        window.resize(*size)
        window.show()
        app.processEvents()
        window.show_tour()
        card = window.tour
        if scale != 1:
            for label in (card.counter, card.heading, card.description):
                label.setStyleSheet(f'font-size:{round(label.font().pixelSize() * scale)}px')
        assert len(card.steps) == 18
        for step in range(len(card.steps)):
            card.go(step - card.step)
            app.processEvents()
            card.reposition()
            app.processEvents()
            assert_card_readable(card)
            assert card.counter.text() == f'Шаг {step + 1} из {len(card.steps)}'
        # The former step-four clipping also regressed when resizing in place.
        card.go(3 - card.step)
        window.resize(1000, 680)
        app.processEvents()
        card.reposition()
        assert_card_readable(card)
    finally:
        if window.tour:
            window.tour.reject()
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
