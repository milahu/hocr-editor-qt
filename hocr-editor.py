#!/usr/bin/env python3

import sys
import re
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QGraphicsScene,
    QGraphicsRectItem, QGraphicsTextItem, QGraphicsItem,
    QGraphicsSimpleTextItem, QGraphicsProxyWidget,
    QWidget, QVBoxLayout, QLabel, QLineEdit, QSplitter,
    QGraphicsEllipseItem,
)
from PySide6.QtGui import QBrush, QColor, QPen, QFont, QMouseEvent
from PySide6.QtCore import QRectF, Qt, QPointF

from hocr_parser import HocrParser

bbox_re = re.compile(r"bbox (\d+) (\d+) (\d+) (\d+)")
xwconf_re = re.compile(r"x_wconf (\d+)")


class WordItem(QGraphicsRectItem):
    HANDLE_SIZE = 6

    def __init__(self, word, inspector_update_cb):
        super().__init__(QRectF(word.bbox[0], word.bbox[1],
                               word.bbox[2] - word.bbox[0],
                               word.bbox[3] - word.bbox[1]))
        self.word = word
        self.inspector_update_cb = inspector_update_cb
        self.editor = None  # QGraphicsProxyWidget for editing

        # Style
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

    # --- Helpers
    def _update_text_position(self):
        rect = self.rect()
        self.text_item.setPos(rect.x() + 2, rect.y() + 2)
        font = self.text_item.font()
        font.setPointSizeF(max(10, rect.height() * 0.6))
        self.text_item.setFont(font)

    def _update_handle_position(self):
        rect = self.rect()
        self.handle_item.setPos(rect.right() - self.HANDLE_SIZE,
                                rect.bottom() - self.HANDLE_SIZE)

    def update_word_bbox(self):
        rect = self.rect()
        self.word.bbox = (int(rect.left()), int(rect.top()),
                          int(rect.right()), int(rect.bottom()))

    # --- Events
    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.update_word_bbox()
        self._update_text_position()
        self._update_handle_position()
        self.inspector_update_cb(self.word)

    def mouseDoubleClickEvent(self, event):
        if self.editor is None:
            # Create QLineEdit for editing text
            line_edit = QLineEdit(self.word.text)
            line_edit.setFrame(False)
            line_edit.setFixedWidth(int(self.rect().width()))

            # Wrap in proxy widget so it can be placed in the scene
            self.editor = QGraphicsProxyWidget(self)
            self.editor.setWidget(line_edit)
            self.editor.setPos(self.rect().x() + 2, self.rect().y() + 2)

            line_edit.editingFinished.connect(self.finish_editing)

        else:
            # already editing → ignore
            pass

    def finish_editing(self):
        if self.editor:
            line_edit = self.editor.widget()
            new_text = line_edit.text()

            # update Word + graphics
            self.word.text = new_text
            self.text_item.setText(new_text)
            self._update_text_position()

            # cleanup editor
            self.scene().removeItem(self.editor)
            self.editor = None

            # notify inspector
            self.inspector_update_cb(self.word)


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
