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

from tree_sitter import Parser
from tree_sitter_language_pack import get_language

HTML_LANGUAGE = get_language("html")

bbox_re = re.compile(r"bbox (\d+) (\d+) (\d+) (\d+)")
xwconf_re = re.compile(r"x_wconf (\d+)")


class WordItem(QGraphicsRectItem):
    def __init__(self, word_id, text, bbox, x_wconf, inspector_update_cb):
        super().__init__(*QRectF(*bbox))
        self.word_id = word_id
        self.word_text = text
        self.bbox = list(bbox)  # [x0,y0,x1,y1]
        self.x_wconf = x_wconf
        self.inspector_update_cb = inspector_update_cb

        # Styling
        self.setBrush(QBrush(QColor(255, 255, 0, 50)))
        self.setPen(QPen(QColor(255, 0, 0), 1, Qt.SolidLine))
        self.setFlags(
            QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemSendsGeometryChanges
        )

        # Text
        self.text_item = QGraphicsSimpleTextItem(text, self)
        font_size = max(10, (bbox[3] - bbox[1]) * 0.8)
        font = QFont("Arial", int(font_size))
        self.text_item.setFont(font)
        self.text_item.setPos(0, 0)

        # Resize handle
        self.handle_size = 8
        self.resizing = False

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

    def mouseReleaseEvent(self, event: QMouseEvent):
        self.resizing = False
        self._update_bbox_from_rect()
        super().mouseReleaseEvent(event)

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

    def update_text_pos(self):
        self.text_item.setPos(0, 0)

    def _update_bbox_from_rect(self):
        rect = self.sceneBoundingRect()
        self.bbox = [int(rect.left()), int(rect.top()), int(rect.right()), int(rect.bottom())]
        if self.isSelected():
            self.inspector_update_cb(self)

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
        src = open(hocr_file, "r", encoding="utf-8").read()

        parser = Parser(HTML_LANGUAGE)
        tree = parser.parse(bytes(src, "utf8"))
        root = tree.root_node

        def walk(node):
            if node.type == "start_tag" and any(
                (c.type == "attribute" and
                # FIXME AttributeError: 'NoneType' object has no attribute 'text'
                 c.child_by_field_name("name").text.decode() == "class" and
                 "ocrx_word" in c.text.decode())
                for c in node.children
            ):
                # Extract id, title, text
                attrs = {
                    c.child_by_field_name("name").text.decode(): c.text.decode()
                    for c in node.children if c.type == "attribute"
                }
                word_id = attrs.get("id", "unknown")
                title = attrs.get("title", "")
                bbox_m = bbox_re.search(title)
                if not bbox_m:
                    return
                bbox = tuple(map(int, bbox_m.groups()))
                xwconf_m = xwconf_re.search(title)
                xwconf = int(xwconf_m.group(1)) if xwconf_m else 0
                # Grab text from next text node
                text_node = node.next_named_sibling
                text_val = text_node.text.decode().strip() if text_node else ""
                item = WordItem(word_id, text_val, bbox, xwconf, self.inspector.update_word)
                self.scene.addItem(item)
                self.words.append(item)

            for ch in node.children:
                walk(ch)

        walk(root)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    if len(sys.argv) < 2:
        print("Usage: python hocr_editor_qt_native.py file.hocr.html")
        sys.exit(1)
    editor = HocrEditor(sys.argv[1])
    editor.resize(1200, 800)
    editor.show()
    sys.exit(app.exec())
