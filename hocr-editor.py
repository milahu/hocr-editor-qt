#!/usr/bin/env python3

import os
import sys
import re
import argparse
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
    QPlainTextEdit,
    QColorDialog,
)
from PySide6.QtGui import QBrush, QColor, QPen, QFont, QMouseEvent
from PySide6.QtGui import (
    QPixmap,
    QImage,
    QPainter,
    QTransform,
    QShortcut,
    QKeySequence,
    QTextCursor,
    QWheelEvent,
    QIcon,
    QAction,
    QColor,
)
from PySide6.QtCore import QRectF, Qt, QPointF
from PySide6.QtCore import (
    QTimer,
    QSizeF,
)

from hocr_parser import HocrParser, Word
from hocr_source_editor import HocrSourceEditor
from resizable_rect_item import ResizableRectItem

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


class WordItem(ResizableRectItem):
    def __init__(self, word, word_selected_cb, word_changed_cb):
        x0, y0, x1, y1 = word.bbox
        w = x1 - x0
        h = y1 - y0
        super().__init__(
            # QRectF(x0, y0, w, h), # broken text position
            QRectF(0, 0, w, h), # local rect
            move_done_cb=self.move_done_cb,
            resize_done_cb=self.resize_done_cb,
        )
        self.setPos(x0, y0)  # scene position
        self.word = word
        self.word_selected_cb = word_selected_cb
        self.word_changed_cb = word_changed_cb
        self.editor = None

        # Text
        # note: text position is (0, 0) relative to its parent setPos(x0, y0)
        self.text_item = QGraphicsSimpleTextItem(word.text, self)
        self._update_text_position()

    def move_done_cb(self, pos1, pos2):
        self._update_text_position()
        self.update_word_bbox()

    def resize_done_cb(self, rect1, rect2):
        self._update_text_position()
        self.update_word_bbox()

    def set_theme_colors(self):
        """Call this after item is in a scene."""
        if self.scene() and self.scene().views():
            view = self.scene().views()[0]
            palette = view.palette()
            # no: 'PySide6.QtGui.QPalette' object has no attribute 'Text'
            # fg_color = palette.color(palette.Text) # text / line color
            # bg_color = palette.color(palette.Base) # background color (optional)
            fg_color = palette.color(palette.ColorRole.Text) # text / line color
            bg_color = palette.color(palette.ColorRole.Base) # background color (optional)
            # Text color
            self.text_item.setBrush(QBrush(fg_color))
            # Rectangle outline
            # pen = QPen(fg_color, 1) # solid line
            # pen = QPen(fg_color, 1, Qt.DashLine) # dashed line
            pen = QPen(fg_color, 1, Qt.DotLine) # dotted line
            self.setPen(pen)
            # no, this is ugly
            # Optional: fill color with some transparency
            # self.setBrush(QBrush(fg_color, Qt.Dense4Pattern))  # or light alpha

    def set_text_color(self, color):
        """Apply color to text and bbox outline."""
        self.text_item.setBrush(color)
        self.setPen(QPen(color, 1))

    # Override QGraphicsItem hook when added to scene
    def itemChange(self, change, value):
        # print("itemChange", change, value)
        # if change == QGraphicsItem.ItemSceneChange:
        if change == QGraphicsItem.ItemSceneHasChanged:
            self.set_theme_colors()
        return super().itemChange(change, value)

    def word_changed_cb(word_id, new_text):
        self.parser.update(word_id, text=new_text)
        self.source_editor.update_from_page()

    # ---------------- Helpers ----------------
    def _update_text_position(self):
        self.text_item.setPos(self.rect().x() + 2, self.rect().y() + 2)
        font = self.text_item.font()
        font.setPointSizeF(max(10, self.rect().height() * 0.6))
        self.text_item.setFont(font)

    def update_word_bbox(self):
        top_left = self.mapToScene(self.rect().topLeft())
        bottom_right = self.mapToScene(self.rect().bottomRight())
        new_bbox = (
            int(top_left.x()),
            int(top_left.y()),
            int(bottom_right.x()),
            int(bottom_right.y())
        )
        old_bbox = self.word.bbox
        if old_bbox != new_bbox:
            print(f"update_word_bbox: {old_bbox} -> {new_bbox}")
            self.word.bbox = new_bbox
            self.word_changed_cb(self.word.id, self.word.text, bbox=new_bbox)
        else:
            print(f"update_word_bbox: no change")

    def mouseDoubleClickEvent(self, event):
        if self.editor is None:
            line_edit = QLineEdit(self.word.text)
            line_edit.setFrame(False)
            line_edit.setFixedWidth(int(self.rect().width()))
            self.editor = QGraphicsProxyWidget(self)
            self.editor.setWidget(line_edit)
            self.editor.setPos(2, 2)
            # Select all text so user can overwrite immediately
            line_edit.selectAll()
            line_edit.setFocus(Qt.FocusReason.MouseFocusReason)
            line_edit.editingFinished.connect(self.finish_editing)

    # ---------------- Helpers ----------------
    def commit_text(self, new_text):
        # print(f"commit_text: word.text {self.word.text!r} -> {new_text!r}")
        self.word.text = new_text
        self.text_item.setText(new_text)
        self.word_changed_cb(self.word.id, new_text, bbox=self.word.bbox)
        self.word_selected_cb(self)

    def finish_editing(self):
        if self.editor:
            line_edit = self.editor.widget()
            new_text = line_edit.text()
            # Disconnect signal immediately
            try:
                line_edit.editingFinished.disconnect()
            except Exception:
                pass
            if new_text != self.word.text:
                # Delay update until after editor fully closes
                QTimer.singleShot(0, lambda: self.commit_text(new_text))
            # Remove proxy safely after current events
            proxy = self.editor
            self.editor = None
            QTimer.singleShot(0, lambda: self.scene().removeItem(proxy))


class PageView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self._zoom = 0

    def fit_width(self):
        """Scale so that scene width fits view width."""
        if not self.scene() or self.scene().width() == 0:
            return
        view_width = self.viewport().width()
        scene_width = self.scene().width()
        factor = view_width / scene_width
        self.setTransform(QTransform())  # reset
        self.scale(factor, factor)
        self._zoom = 0

    def wheelEvent(self, event):
        """Zoom with Ctrl+wheel"""
        modifiers = event.modifiers()
        if modifiers & Qt.ControlModifier:
            # --- Zoom ---
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
        elif modifiers & Qt.ShiftModifier:
            # --- Horizontal scroll ---
            delta = event.angleDelta().y()  # vertical wheel normally
            if delta != 0:
                step = delta
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() - step
                )
            event.accept()
        else:
            super().wheelEvent(event)

    def zoom_in(self):
        self._zoom += 1
        self.scale(1.2, 1.2)

    def zoom_out(self):
        self._zoom -= 1
        self.scale(1/1.2, 1/1.2)


class HocrEditor(QMainWindow):
    def __init__(self, hocr_file):
        super().__init__()
        self.hocr_file = hocr_file  # remember original filename
        self.scene = QGraphicsScene()
        self.view = PageView(self.scene)

        self.setWindowTitle("HOCR Editor")
        self.setWindowIcon(QIcon(os.path.dirname(__file__) + "/Eo_circle_blue_letter-h.2.png"))

        # track chosen overlay color
        self.overlay_color = None

        # load words into scene
        # set self.parser
        self.words = []
        self.load_hocr(hocr_file)

        # HOCR source editor dock
        self.source_editor = HocrSourceEditor(
            self.parser,
            update_page_cb=self.refresh_page_view,
            parent=self,
        )

        # Splitter to control widths
        splitter = QSplitter()
        splitter.addWidget(self.view)
        splitter.addWidget(self.source_editor)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.setCentralWidget(container)

        # Menu bar
        self._create_menubar()

        # --- zoom shortcuts ---
        QShortcut(QKeySequence("Ctrl++"), self, self.view.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self, self.view.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self, self.view.fit_width)

        self.showMaximized() # use full screen size

        # Initial width ratio
        page_view_initial_width = 0.5
        current_width = self.width()
        splitter.setSizes([int(current_width * page_view_initial_width), int(current_width * (1 - page_view_initial_width))])
        # proportional resizing behavior after initial sizing
        splitter.setStretchFactor(0, page_view_initial_width)
        splitter.setStretchFactor(1, (1 - page_view_initial_width))

        # TODO better
        for delay in [1, 10, 20, 50, 100, 200, 500]:
            QTimer.singleShot(delay, self.view.fit_width)  # fit width after layout

    def _create_menubar(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        save_action = file_menu.addAction("Save")
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_hocr)

        save_as_action = file_menu.addAction("Save As...")
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self.save_hocr_as)

        # View menu
        view_menu = menubar.addMenu("&View")

        text_color_action = QAction("Set Overlay Color", self)
        text_color_action.triggered.connect(self.pick_text_color)
        view_menu.addAction(text_color_action)

    def pick_text_color(self):
        color = QColorDialog.getColor(self.overlay_color or Qt.green, self, "Set Overlay Color")
        if color.isValid():
            self.overlay_color = color
            # apply to all WordItems
            for item in self.scene.items():
                if isinstance(item, WordItem):
                    item.set_text_color(color)

    def load_hocr(self, hocr_file):
        self.hocr_file = hocr_file
        with open(hocr_file, "r", encoding="utf-8") as f:
            source = f.read()

        self.parser = HocrParser(source)
        self.words = self.parser.find_words()
        # print("self.words", self.words)

        self.load_words()

        # QTimer.singleShot(0, self.view.fit_width)  # fit width after layout

    def load_words(self):
        """Populate the scene with WordItems from parser"""

        # --- add page images ---
        for page in self.parser.find_pages():
            # print("page", page)
            img_path = _extract_image_from_title(page.title_value)
            img_path = os.path.join(
                os.path.dirname(self.hocr_file),
                img_path
            )
            if img_path and os.path.exists(img_path):
                pixmap = QPixmap(img_path)
                if _is_dark_mode(self.view):
                    pixmap = _invert_pixmap(pixmap)
                self.scene.addPixmap(pixmap).setZValue(-1)
            # FIXME support hocr files with multiple pages
            break # stop after first page

        for word in self.parser.find_words():
            item = WordItem(
                word,
                word_selected_cb=self.on_word_selected,
                word_changed_cb=self.on_word_changed,
            )
            self.scene.addItem(item)

    def refresh_page_view(self):
        """Clear scene and reload words from parser"""
        self.scene.clear()
        self.load_words()

    def on_word_selected(self, word_item: WordItem):
        # Convert byte offsets to character offsets
        start_char = len(self.parser.source_bytes[:word_item.word.text_range[0]].decode("utf-8", errors="replace"))
        end_char = start_char + len(word_item.word.text)

        # Set selection
        cursor = self.source_editor.textCursor()
        cursor.setPosition(start_char)
        cursor.setPosition(end_char, QTextCursor.KeepAnchor)
        self.source_editor.setTextCursor(cursor)

        # center the cursor
        self.source_editor.centerCursor()

    def on_word_changed(self, word_id: str, new_text: str = None, bbox=None):
        """Called when WordItem text changes"""
        # print("on_word_changed", word_id, new_text, bbox)
        self.parser.update(word_id, text=new_text, bbox=bbox)
        self.source_editor.update_from_page()

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


def main():
    parser = argparse.ArgumentParser(description="HOCR Editor")
    parser.add_argument("hocr_file", help="Path to HOCR file")
    parser.add_argument(
        "--overlay-color",
        default=None,
        help="Overlay color (color name or #RRGGBB)",
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)

    overlay_color = None
    if args.overlay_color:
        overlay_color = QColor(args.overlay_color)
        if not overlay_color.isValid():
            print(f"Warning: invalid color {args.overlay_color}, using theme color")
            overlay_color = None

    editor = HocrEditor(args.hocr_file)
    if overlay_color:
        editor.overlay_text_color = overlay_color
        # apply immediately to all items
        for item in editor.scene.items():
            if hasattr(item, "set_text_color"):
                item.set_text_color(overlay_color)

    # editor.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
