#!/usr/bin/env python3

import sys
import re
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QGraphicsScene,
    QGraphicsRectItem, QGraphicsTextItem, QGraphicsItem,
    QGraphicsSimpleTextItem, QGraphicsProxyWidget,
    QWidget, QVBoxLayout, QLabel, QLineEdit, QSplitter
)
from PySide6.QtGui import QBrush, QColor, QPen, QFont, QMouseEvent
from PySide6.QtCore import QRectF, Qt, QPointF

from hocr_parser import HocrParser

bbox_re = re.compile(r"bbox (\d+) (\d+) (\d+) (\d+)")
xwconf_re = re.compile(r"x_wconf (\d+)")


class WordItem(QGraphicsRectItem):
    HANDLE_SIZE = 6

    def __init__(self, word: Word, inspector_update_cb):
        super().__init__(QRectF(word.bbox[0], word.bbox[1],
                               word.bbox[2] - word.bbox[0],
                               word.bbox[3] - word.bbox[1]))
        self.word = word   # <—— now wraps a Word dataclass
        self.inspector_update_cb = inspector_update_cb

        # Draw rect style
        self.setPen(QPen(QColor("red"), 1, Qt.DashLine))
        self.setBrush(QBrush(QColor(255, 0, 0, 40)))
        self.setFlags(QGraphicsRectItem.ItemIsSelectable |
                      QGraphicsRectItem.ItemIsMovable)

        # Show text
        self.text_item = QGraphicsSimpleTextItem(word.text, self)
        self.text_item.setBrush(QColor("black"))
        self._update_text_position()

        # Resize handle
        self.handle_item = QGraphicsEllipseItem(0, 0, self.HANDLE_SIZE, self.HANDLE_SIZE, self)
        self.handle_item.setBrush(QBrush(QColor("blue")))
        self._update_handle_position()
        self.handle_item.setFlag(QGraphicsEllipseItem.ItemIsMovable)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            rect = self.rect()
            handle_rect = QRectF(rect.right() - self.handle_size,
                                 rect.bottom() - self.handle_size,
                                 self.handle_size, self.handle_size)
            if handle_rect.contains(event.pos()):
                self.resizing = True
            else:
                self.resizing = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.resizing:
            new_rect = QRectF(self.rect().topLeft(), event.pos())
            self.setRect(new_rect.normalized())
            self._update_bbox_from_rect()
        else:
            super().mouseMoveEvent(event)
        self.update_text_pos()

    # --- Overridden events
    def mouseReleaseEvent(self, event: QMouseEvent):
        super().mouseReleaseEvent(event)
        self.update_word_bbox()
        self._update_text_position()
        self._update_handle_position()
        self.inspector_update_cb(self.word)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            # Inline editor
            editor = QLineEdit(self.word_text)
            proxy = QGraphicsProxyWidget(self)
            proxy.setWidget(editor)
            proxy.setPos(0, 0)
            editor.setFocus()
            editor.editingFinished.connect(lambda: self._commit_editor(editor, proxy))

    def _commit_editor(self, editor, proxy):
        self.word_text = editor.text()
        self.text_item.setText(self.word_text)
        proxy.deleteLater()
        self.update_text_pos()

    # --- Helpers
    def _update_text_position(self):
        rect = self.rect()
        self.text_item.setPos(rect.x() + 2, rect.y() + 2)
        font = self.text_item.font()
        font.setPointSizeF(max(10, rect.height() * 0.8))
        self.text_item.setFont(font)

    def _update_handle_position(self):
        rect = self.rect()
        self.handle_item.setPos(rect.right() - self.HANDLE_SIZE,
                                rect.bottom() - self.HANDLE_SIZE)

    # --- Sync back bbox
    def update_word_bbox(self):
        rect = self.rect()
        self.word.bbox = (int(rect.left()), int(rect.top()),
                          int(rect.right()), int(rect.bottom()))

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        # Draw resize handle
        rect = self.rect()
        handle_rect = QRectF(rect.right() - self.handle_size,
                             rect.bottom() - self.handle_size,
                             self.handle_size, self.handle_size)
        painter.fillRect(handle_rect, QColor(0, 0, 255))


class Inspector(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.labels = {}
        for field in ["id", "text", "bbox", "x_wconf"]:
            lbl = QLabel(f"{field}: ")
            layout.addWidget(lbl)
            self.labels[field] = lbl

    def update_word(self, word_item: WordItem):
        self.labels["id"].setText(f"id: {word_item.word_id}")
        self.labels["text"].setText(f"text: {word_item.word_text}")
        self.labels["bbox"].setText(f"bbox: {' '.join(map(str, word_item.bbox))}")
        self.labels["x_wconf"].setText(f"x_wconf: {word_item.x_wconf}")


class HocrEditor(QMainWindow):
    def __init__(self, hocr_file):
        super().__init__()
        self.setWindowTitle("HOCR Editor (Qt Native)")
        splitter = QSplitter()
        self.setCentralWidget(splitter)

        # Scene + View
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        splitter.addWidget(self.view)

        # Inspector
        self.inspector = Inspector()
        splitter.addWidget(self.inspector)

        self.words = []
        self.load_hocr(hocr_file)

    def load_hocr(self, hocr_file):
        with open(hocr_file, "r", encoding="utf-8") as f:
            source = f.read()

        parser = HocrParser(source)
        self.words = parser.find_words()
        print("self.words", self.words)

        # FIXME integrate class Word with class WordItem
        for word in self.words:
            item = WordItem(word)
            self.scene.addItem(item)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    if len(sys.argv) < 2:
        print("Usage: python hocr_editor_qt_native.py file.hocr.html")
        sys.exit(1)
    editor = HocrEditor(sys.argv[1])
    editor.resize(1200, 800)
    editor.show()
    sys.exit(app.exec())
