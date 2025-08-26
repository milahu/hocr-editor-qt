from PySide6.QtWidgets import (
    QPlainTextEdit,
)
from PySide6.QtGui import (
    QWheelEvent,
    QFont,
    QShortcut,
    QKeySequence,
    QTextCursor,
)
from PySide6.QtCore import (
    Qt,
)
from hocr_parser import HocrParser, Word


class HocrSourceEditor(QPlainTextEdit):
    """Editable HOCR source view with sync callback"""
    def __init__(self, parser: HocrParser, update_page_cb, parent=None):
        super().__init__(parent)
        self.parser = parser
        self.update_page_cb = update_page_cb  # callback to refresh page view
        self.setPlainText(parser.source)
        self.textChanged.connect(self.on_text_changed)
        self._updating = False  # avoid recursive updates

        self.default_font = QFont("Courier New", 12)  # readable monospace
        self.setFont(self.default_font)
        self.current_font_size = self.default_font.pointSize()

        # keyboard shortcuts
        QShortcut(QKeySequence("Ctrl++"), self, activated=self.zoom_in)
        QShortcut(QKeySequence("Ctrl+="), self, activated=self.zoom_in)  # some keyboards
        QShortcut(QKeySequence("Ctrl+-"), self, activated=self.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self, activated=self.reset_zoom)

    def on_text_changed(self):
        if self._updating:
            return
        self._updating = True
        # Update parser source
        new_source = self.toPlainText()
        self.parser.set_source(new_source)
        # Notify page view to refresh WordItems positions/text
        self.update_page_cb()
        self._updating = False

    def update_from_page(self):
        """Call this when page view edits a word; refresh source editor"""
        if self._updating:
            return
        self._updating = True
        self.setPlainText(self.parser.source)
        self._updating = False

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

    def zoom_in(self):
        self.current_font_size = min(self.current_font_size + 1, 72)
        self.apply_zoom()

    def zoom_out(self):
        self.current_font_size = max(self.current_font_size - 1, 6)
        self.apply_zoom()

    def reset_zoom(self):
        self.current_font_size = self.default_font.pointSize()
        self.apply_zoom()

    def apply_zoom(self):
        font = QFont(self.default_font)
        font.setPointSize(self.current_font_size)
        self.setFont(font)
