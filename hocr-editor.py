#!/usr/bin/env python3

import os
import sys
import re
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QGraphicsScene,
    QGraphicsRectItem, QGraphicsTextItem, QGraphicsItem,
    QGraphicsSimpleTextItem, QGraphicsProxyWidget,
    QWidget, QVBoxLayout, QLabel, QLineEdit, QSplitter,
    QGraphicsEllipseItem,
    QDockWidget,
    QFileDialog,
    QMessageBox,
    QStyleOptionGraphicsItem,
    QStyle,
)
from PySide6.QtGui import QBrush, QColor, QPen, QFont, QMouseEvent
from PySide6.QtGui import (
    QPixmap,
    QImage,
)
from PySide6.QtCore import QRectF, Qt, QPointF
from PySide6.QtCore import (
    QTimer,
    QSizeF,
)

from hocr_parser import HocrParser, Word

bbox_re = re.compile(r"bbox (\d+) (\d+) (\d+) (\d+)")
xwconf_re = re.compile(r"x_wconf (\d+)")


# --- utilities for images / dark mode ---
def _extract_image_from_title(title: str) -> str:
    m = re.search(r'image\s+"([^"]+)"', title)
    return m.group(1) if m else None

def _is_dark_mode(view) -> bool:
    pal = view.palette()
    bg = pal.color(view.backgroundRole())
    return (0.299*bg.red() + 0.587*bg.green() + 0.114*bg.blue()) < 128

def _invert_pixmap(pixmap: QPixmap) -> QPixmap:
    img = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
    img.invertPixels()
    return QPixmap.fromImage(img)


class WordItem(QGraphicsRectItem):
    HANDLE_SIZE = 8

    def __init__(self, word, inspector_update_cb, parser_update_cb):
        x0, y0, x1, y1 = word.bbox
        super().__init__(QRectF(0, 0, x1 - x0, y1 - y0))  # local rect
        self.setPos(x0, y0)  # scene position
        self.word = word
        self.inspector_update_cb = inspector_update_cb
        self.parser_update_cb = parser_update_cb
        self.editor = None

        # Enable moving and selection
        self.setFlags(
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemIsMovable
        )
        self.setAcceptHoverEvents(True)

        # Text
        self.text_item = QGraphicsSimpleTextItem(word.text, self)
        self._update_text_position()

        # Resize handle
        self.handle_item = QGraphicsRectItem(
            0, 0, self.HANDLE_SIZE, self.HANDLE_SIZE, self
        )
        self.handle_item.setBrush(QBrush(QColor("blue")))
        self.handle_item.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.handle_item.setCursor(Qt.SizeFDiagCursor)
        self._update_handle_position()

        # Track drag/resizing
        self._resizing = False
        self._drag_offset = QPointF(0, 0)

    def set_theme_text_color(self):
        """Call this after item is in a scene."""
        # print("set_theme_text_color scene", self.scene())
        if self.scene() and self.scene().views():
            # print("setting text color")
            palette = self.scene().views()[0].palette()
            text_color = palette.color(palette.ColorRole.Text)
            self.text_item.setBrush(QBrush(text_color))

    # Override QGraphicsItem hook when added to scene
    def itemChange(self, change, value):
        # print("itemChange", change, value)
        # if change == QGraphicsItem.ItemSceneChange:
        if change == QGraphicsItem.ItemSceneHasChanged:
            self.set_theme_text_color()
        return super().itemChange(change, value)

    # ---------------- Helpers ----------------
    def _update_text_position(self):
        self.text_item.setPos(2, 2)
        font = self.text_item.font()
        font.setPointSizeF(max(10, self.rect().height() * 0.6))
        self.text_item.setFont(font)

    def _update_handle_position(self):
        rect = self.rect()
        self.handle_item.setPos(rect.width() - self.HANDLE_SIZE,
                                rect.height() - self.HANDLE_SIZE)

    def update_word_bbox(self):
        # combine rect() and scene position
        top_left = self.mapToScene(self.rect().topLeft())
        bottom_right = self.mapToScene(self.rect().bottomRight())
        new_bbox = (int(top_left.x()), int(top_left.y()),
                    int(bottom_right.x()), int(bottom_right.y()))
        old_bbox = self.word.bbox
        self.word.bbox = new_bbox
        if old_bbox != new_bbox:
            print(f"update_word_bbox: {old_bbox} -> {new_bbox}")
            self.parser_update_cb(self.word.id, self.word.text, bbox=new_bbox)

    # ---------------- Events ----------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._is_on_handle(event.pos()):
            self._resizing = True
        else:
            self._resizing = False
            self._drag_offset = event.pos()
        self.inspector_update_cb(self)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._resizing = False
        self.update_word_bbox()
        self.inspector_update_cb(self)

    def mouseDoubleClickEvent(self, event):
        if self.editor is None:
            line_edit = QLineEdit(self.word.text)
            line_edit.setFrame(False)
            line_edit.setFixedWidth(int(self.rect().width()))
            self.editor = QGraphicsProxyWidget(self)
            self.editor.setWidget(line_edit)
            self.editor.setPos(2, 2)
            line_edit.editingFinished.connect(self.finish_editing)

    def finish_editing(self):
        if self.editor:
            line_edit = self.editor.widget()
            new_text = line_edit.text()
            if new_text != self.word.text:
                self.word.text = new_text
                self.text_item.setText(new_text)
                self.parser_update_cb(self.word.id, new_text, bbox=self.word.bbox)
                self.inspector_update_cb(self)
            self.scene().removeItem(self.editor)
            self.editor = None

    # ---------------- Helpers ----------------
    def _is_on_handle(self, pos: QPointF) -> bool:
        handle_rect = QRectF(self.handle_item.pos(),
                             QSizeF(self.HANDLE_SIZE, self.HANDLE_SIZE))
        return handle_rect.contains(pos)


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
        self.hocr_file = hocr_file  # remember original filename
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.setCentralWidget(self.view)

        # create Inspector dock
        self.inspector = Inspector()
        dock = QDockWidget("Inspector", self)
        dock.setWidget(self.inspector)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        # Menu bar
        self._create_menubar()

        self.words = []
        self.load_hocr(hocr_file)

    def _create_menubar(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        save_action = file_menu.addAction("Save")
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_hocr)

        save_as_action = file_menu.addAction("Save As...")
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self.save_hocr_as)

    def load_hocr(self, hocr_file):
        with open(hocr_file, "r", encoding="utf-8") as f:
            source = f.read()

        self.parser = HocrParser(source)
        self.words = self.parser.find_words()
        # print("self.words", self.words)

        def parser_update_cb(word_id, text=None, bbox=None):
            self.parser.update(word_id, text=text, bbox=bbox)

        # --- add page images ---
        for page in self.parser.find_pages():
            # print("page", page)
            img_path = _extract_image_from_title(page.title_value)
            if img_path and os.path.exists(img_path):
                pixmap = QPixmap(img_path)
                if _is_dark_mode(self.view):
                    pixmap = _invert_pixmap(pixmap)
                self.scene.addPixmap(pixmap).setZValue(-1)
            # FIXME support hocr files with multiple pages
            break # stop after first page

        # --- add words ---
        for word in self.words:
            item = WordItem(word, self.inspector.update_word, parser_update_cb)
            self.scene.addItem(item)

    def save_hocr(self):
        """Save to original file."""
        if not self.hocr_file:
            self.save_hocr_as()
            return
        try:
            with open(self.hocr_file, "w", encoding="utf-8") as f:
                f.write(self.parser.source)
            # QMessageBox.information(self, "Saved", f"File saved to {self.hocr_file}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save file:\n{e}")

    def save_hocr_as(self):
        """Save to new file via dialog."""
        filename, _ = QFileDialog.getSaveFileName(self, "Save HOCR File", "", "HOCR Files (*.hocr *.html *.xhtml);;All Files (*)")
        if filename:
            self.hocr_file = filename
            self.save_hocr()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    if len(sys.argv) < 2:
        print("Usage: python hocr_editor_qt_native.py file.hocr.html")
        sys.exit(1)
    editor = HocrEditor(sys.argv[1])
    editor.resize(1200, 800)
    editor.show()
    sys.exit(app.exec())
