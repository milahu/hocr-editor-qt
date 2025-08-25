#!/usr/bin/env python3

import sys
import re
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QGraphicsScene,
    QGraphicsRectItem, QGraphicsTextItem, QGraphicsItem,
    QGraphicsSimpleTextItem, QGraphicsProxyWidget,
    QWidget, QVBoxLayout, QLabel, QLineEdit, QSplitter,
    QGraphicsEllipseItem,
    QDockWidget,
)
from PySide6.QtGui import QBrush, QColor, QPen, QFont, QMouseEvent
from PySide6.QtCore import QRectF, Qt, QPointF
from PySide6.QtCore import (
    QTimer,
)

from hocr_parser import HocrParser

bbox_re = re.compile(r"bbox (\d+) (\d+) (\d+) (\d+)")
xwconf_re = re.compile(r"x_wconf (\d+)")


class WordItem(QGraphicsRectItem):
    HANDLE_SIZE = 6

    def __init__(self, word, inspector_update_cb, parser_update_cb):
        # assert word.bbox is not None, f"{word.id} has no bbox (title='{word.title_value}')"
        super().__init__(QRectF(word.bbox[0], word.bbox[1],
                               word.bbox[2] - word.bbox[0],
                               word.bbox[3] - word.bbox[1]))
        self.word = word
        # self.bbox = list(word.bbox)
        self.inspector_update_cb = inspector_update_cb
        self.parser_update_cb = parser_update_cb
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

        '''
        self.word_id = word.id
        self.word_text = word.text
        self.bbox = list(word.bbox)
        self.x_wconf = word.x_wconf
        self.inspector_update_cb = inspector_update_cb

        # add label on top
        self.text_item = QGraphicsSimpleTextItem(self.word_text, self)
        self.text_item.setFont(QFont("Arial", 12))
        self.text_item.setPos(self.bbox[0], self.bbox[1])

        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        '''

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
    def mousePressEvent(self, event):
        # when clicked, update inspector
        # if self.inspector_update_cb:
        #     self.inspector_update_cb(self)
        self.inspector_update_cb(self)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.update_word_bbox()
        self._update_text_position()
        self._update_handle_position()
        # self.inspector_update_cb(self.word)
        self.inspector_update_cb(self)

    '''
    def mouseReleaseEvent(self, event):
        # after move/resize, refresh inspector with new bbox
        self.bbox = [int(self.rect().x()), int(self.rect().y()),
                     int(self.rect().x() + self.rect().width()),
                     int(self.rect().y() + self.rect().height())]
        if self.inspector_update_cb:
            self.inspector_update_cb(self)
        super().mouseReleaseEvent(event)
    '''

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

    def commit_text(self, new_text):
        # update Word + graphics
        self.word.text = new_text
        self.text_item.setText(new_text)
        self._update_text_position()
        # refresh inspector
        self.inspector_update_cb(self)

    def finish_editing(self):
        if self.editor:
            line_edit = self.editor.widget()
            new_text = line_edit.text()
            if new_text != self.word.text:
                # Delay update until editor is fully committed
                def do_update():
                    self.commit_text(new_text)
                    # safely remove proxy
                    self.scene().removeItem(self.editor)
                    self.editor.deleteLater()
                    self.editor = None
                QTimer.singleShot(0, do_update)
            else:
                # just remove editor
                def cleanup():
                    self.scene().removeItem(self.editor)
                    self.editor.deleteLater()
                    self.editor = None
                QTimer.singleShot(0, cleanup)

    def eventFilter(self, obj, event):
        if obj is self.text_item:
            if event.type() == QEvent.FocusOut:
                new_text = self.text_item.toPlainText()
                if new_text != self.word_text:
                    self.word_text = new_text
                    # safe: update parser now
                    self.parser_update_cb(self.word_id, new_text)
                    self.inspector_update_cb(self)
        return super().eventFilter(obj, event)


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
        self.labels["id"].setText(f"id: {word_item.word.id}")
        self.labels["text"].setText(f"text: {word_item.word.text}")
        self.labels["bbox"].setText(f"bbox: {' '.join(map(str, word_item.word.bbox))}")
        self.labels["x_wconf"].setText(f"x_wconf: {word_item.word.x_wconf}")


class HocrEditor(QMainWindow):
    def __init__(self, hocr_file):
        super().__init__()
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.setCentralWidget(self.view)

        # create Inspector dock
        self.inspector = Inspector()
        dock = QDockWidget("Inspector", self)
        dock.setWidget(self.inspector)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        self.words = []
        self.load_hocr(hocr_file)

    def load_hocr(self, hocr_file):
        with open(hocr_file, "r", encoding="utf-8") as f:
            source = f.read()

        parser = HocrParser(source)
        self.words = parser.find_words()
        # print("self.words", self.words)

        def parser_update_cb(word_id, new_text):
            self.parser.update(word_id, text=new_text)

        for word in self.words:
            item = WordItem(word, self.inspector.update_word, parser_update_cb)
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
