"""Verify all 18 actual Qt tour layouts; capture every compact native step."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import sys
os.environ.setdefault('QT_QPA_PLATFORM', 'windows' if os.name == 'nt' else 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from data_xray.demo import create_demo
from data_xray.ui import MainWindow


def verify(card):
    card.layout().activate()
    assert card.parentWidget().rect().contains(card.geometry()), ('card bounds', card.step)
    for label in (card.counter, card.heading, card.description):
        required = label.heightForWidth(label.width()) if label.wordWrap() else label.sizeHint().height()
        bounds = label.fontMetrics().boundingRect(label.contentsRect(), int(Qt.TextFlag.TextWordWrap) if label.wordWrap() else 0, label.text())
        assert label.height() >= required and bounds.height() <= label.contentsRect().height(), ('clipped height', card.step, label.text())
        assert bounds.width() <= label.contentsRect().width(), ('clipped width', card.step, label.text())
    assert card.heading.y() - card.counter.geometry().bottom() <= 10
    assert card.counter.height() <= card.counter.sizeHint().height() + 2
    assert not card.isWindow() and not card.target_rect().isEmpty()
    assert card.target.isVisibleTo(card.parentWidget())


def main():
    app = QApplication([])
    for font in ('segoeui.ttf', 'segoeuib.ttf'):
        path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / font
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    output = Path(__file__).resolve().parents[1] / 'docs' / 'tour-v010'
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='xray-tour-') as temp:
        settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.Format.IniFormat)
        settings.setValue('help/show_on_startup', False)
        window = MainWindow(settings=settings)
        try:
            window.show()
            window.select_path(create_demo(Path(temp), all_formats=False), auto_start=True)
            for _ in range(2000):
                app.processEvents()
                if window.result is not None and not window._busy and not window.loading_overlay.isVisible():
                    break
                QTest.qWait(5)
            assert window.result is not None and not window._busy
            for scale in (1.0, 1.2):
                window.show_tour()
                card = window.tour
                if scale != 1:
                    for label in (card.counter, card.heading, card.description):
                        label.setStyleSheet(f'font-size:{round(label.font().pixelSize() * scale)}px')
                for size in ((1000, 680), (1400, 900)):
                    window.resize(*size)
                    app.processEvents()
                    for step in range(len(card.steps)):
                        card.go(step - card.step)
                        app.processEvents()
                        card.reposition()
                        app.processEvents()
                        verify(card)
                        if size == (1000, 680) and scale == 1:
                            assert window.grab().save(str(output / f'step-{step + 1:02}.png'))
                card.reject()
            print(f'{app.platformName()}: 18 steps x 2 window sizes x 2 font sizes passed; 18 compact screenshots in {output}')
        finally:
            if window.tour:
                window.tour.reject()
            if window.worker and window.worker.isRunning():
                window.worker.cancel_event.set()
                assert window.worker.wait(5000)
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()


if __name__ == '__main__':
    main()

