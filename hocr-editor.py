#!/usr/bin/env python3

import os
import sys
import re
import argparse
import signal
import random
import string
import traceback
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
from hocr_parser import print_exceptions
from hocr_parser import debug, debug_word_id
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
    @print_exceptions
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
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.word = word
        self.word_selected_cb = word_selected_cb
        self.word_changed_cb = word_changed_cb
        self.editor = None

        if 0:
            # Text
            # note: text position is (0, 0) relative to its parent setPos(x0, y0)
            self.text_item = QGraphicsSimpleTextItem(word.text, self)
            self._update_text_position()
        else:
            # disable text overlay
            self.text_item = None

    @print_exceptions
    def __str__(self):
        pos = self.scenePos()
        return (
            f"WordItem(" +
            f"span_range={self.word.span_range!r}" +
            f", id={self.word.id!r}" +
            f", text={self.word.text!r}" +
            f", bbox={self.word.bbox!r}" +
            f", pos={(pos.x(), pos.y())!r}" +
            f")"
        )

    @print_exceptions
    def move_done_cb(self, pos1, pos2):
        self._update_text_position()
        self.update_word_bbox()

    @print_exceptions
    def resize_done_cb(self, rect1, rect2):
        self._update_text_position()
        self.update_word_bbox()

    @print_exceptions
    def mouseReleaseEvent(self, event):
        try:
            super().mouseReleaseEvent(event)
        except RuntimeError:
            # Internal C++ object (WordItem) already deleted.
            # the word was removed by self.scene.clear() in self.refresh_page_view()
            # TODO better?
            # shiboken6.isValid(self) always returns True
            # self.destroyed.connect(self.on_destroyed) signal is never emitted
            return
        self.word_selected_cb(self)

    @print_exceptions
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
            if self.text_item:
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

    @print_exceptions
    def set_text_color(self, color):
        """Apply color to text and bbox outline."""
        if self.text_item:
            self.text_item.setBrush(color)
        self.setPen(QPen(color, 1))

    # Override QGraphicsItem hook when added to scene
    @print_exceptions
    def itemChange(self, change, value):
        # print("itemChange", change, value)
        # if change == QGraphicsItem.ItemSceneChange:
        if change == QGraphicsItem.ItemSceneHasChanged:
            self.set_theme_colors()
        return super().itemChange(change, value)

    # ---------------- Helpers ----------------
    @print_exceptions
    def _update_text_position(self):
        if not self.text_item: return
        self.text_item.setPos(self.rect().x() + 2, self.rect().y() + 2)
        font = self.text_item.font()
        font.setPointSizeF(max(10, self.rect().height() * 0.6))
        self.text_item.setFont(font)

    @print_exceptions
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
            if debug_word_id and debug_word_id == self.word.id:
                print(f"word {self.word.id}: update_word_bbox: {old_bbox} -> {new_bbox}")
            self.word.bbox = new_bbox
            self.word_changed_cb(
                self.word.id,
                bbox=new_bbox,
                span_start=self.word.span_range[0],
            )
        else:
            if debug_word_id and debug_word_id == self.word.id:
                print(f"word {self.word.id}: update_word_bbox: no change")

    @print_exceptions
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
    @print_exceptions
    def commit_text(self, new_text):
        # print(f"commit_text: word.text {self.word.text!r} -> {new_text!r}")
        self.word.text = new_text
        if self.text_item:
            self.text_item.setText(new_text)
        if debug_word_id and debug_word_id == self.word.id:
            print(f"word {self.word.id}: commit_text: new_text={new_text!r}")
        self.word_changed_cb(
            self.word.id, new_text,
            bbox=self.word.bbox,
            span_start=self.word.span_range[0],
        )
        self.word_selected_cb(self)

    @print_exceptions
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
    @print_exceptions
    def __init__(
            self,
            scene,
            add_new_word_cb,
        ):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self._zoom = 0

        # For new word creation
        self.add_new_word_cb = add_new_word_cb
        self._creating_new_word = False
        self._new_word_start_pos: QPointF | None = None
        self._new_word_rect_item: QGraphicsRectItem | None = None

    @print_exceptions
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

    @print_exceptions
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

    @print_exceptions
    def zoom_in(self):
        self._zoom += 1
        self.scale(1.2, 1.2)

    @print_exceptions
    def zoom_out(self):
        self._zoom -= 1
        self.scale(1/1.2, 1/1.2)

    @print_exceptions
    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = self.mapToScene(event.pos()) # FIXME DeprecationWarning
            # pos = event.scenePos() # AttributeError
            self._creating_new_word = True
            self._new_word_start_pos = pos
            # initial rectangle (default size)
            default_w, default_h = 50, 20
            self._new_word_rect_item = QGraphicsRectItem(
                QRectF(pos.x(), pos.y(), default_w, default_h)
            )
            pen = QPen(Qt.blue, 1, Qt.DashLine)
            self._new_word_rect_item.setPen(pen)
            self.scene().addItem(self._new_word_rect_item)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    @print_exceptions
    def mouseMoveEvent(self, event):
        if self._creating_new_word and self._new_word_start_pos:
            pos = self.mapToScene(event.pos()) # FIXME DeprecationWarning
            rect = QRectF(self._new_word_start_pos, pos).normalized()
            self._new_word_rect_item.setRect(rect)
        else:
            super().mouseMoveEvent(event)

    @print_exceptions
    def mouseReleaseEvent(self, event):
        if self._creating_new_word and self._new_word_rect_item:
            rect = self._new_word_rect_item.rect()
            self._creating_new_word = False
            self.scene().removeItem(self._new_word_rect_item)
            self._new_word_rect_item = None

            # Notify editor about new word
            self.add_new_word_cb(rect)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class HocrEditor(QMainWindow):
    @print_exceptions
    def __init__(self, hocr_file):
        super().__init__()
        self.hocr_file = hocr_file  # remember original filename
        self.scene = QGraphicsScene()
        # TODO rename to self.page_view
        self.view = PageView(
            self.scene,
            add_new_word_cb=self.add_new_word_from_page_view,
        )
        self.page_view = self.view

        self.setWindowTitle("HOCR Editor")
        self.setWindowIcon(QIcon(os.path.dirname(__file__) + "/Eo_circle_blue_letter-h.2.png"))

        # track chosen overlay color
        self.overlay_color = QColor("black")

        # load words into scene
        # set self.parser
        self.words = []
        self.word_items: dict[str, list[WordItem]] = {}
        self.load_hocr(hocr_file)

        self.changed_word_id = None

        # HOCR source editor dock
        self.source_editor = HocrSourceEditor(
            self.parser,
            update_page_cb=self.refresh_page_view,
            cursor_sync_cb=self.on_code_cursor_changed,
            parent=self,
        )

        # Splitter to control widths
        splitter = QSplitter(Qt.Vertical)
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

    @print_exceptions
    def _create_menubar(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        save_action = file_menu.addAction("Save")
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_hocr)

        save_as_action = file_menu.addAction("Save As...")
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self.save_hocr_as)

        quit_action = file_menu.addAction("Quit")
        quit_action.triggered.connect(self.close)
        quit_action.setShortcut(QKeySequence.Quit) # shortcut: Ctrl+Q

        # View menu
        view_menu = menubar.addMenu("&View")

        text_color_action = QAction("Set Overlay Color", self)
        text_color_action.triggered.connect(self.pick_text_color)
        view_menu.addAction(text_color_action)

    @print_exceptions
    def pick_text_color(self):
        color = QColorDialog.getColor(self.overlay_color or Qt.green, self, "Set Overlay Color")
        if color.isValid():
            self.overlay_color = color
            # apply to all WordItems
            for item in self.scene.items():
                if isinstance(item, WordItem):
                    item.set_text_color(color)

    @print_exceptions
    def load_hocr(self, hocr_file):
        self.hocr_file = hocr_file
        with open(hocr_file, "r", encoding="utf-8") as f:
            source = f.read()

        self.parser = HocrParser(source)
        self.words = self.parser.find_words()
        # print("self.words", self.words)

        self.load_words()

        # QTimer.singleShot(0, self.view.fit_width)  # fit width after layout

    @print_exceptions
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
            if not word.id in self.word_items:
                self.word_items[word.id] = list()
            self.word_items[word.id].append(item)

    @print_exceptions
    def refresh_page_view(self):
        """Update words from parser"""
        if 0:
            # slow non-incremental update
            self.scene.clear()
            self.load_words()
            return
        new_words = dict()
        for word in self.parser.find_words():
            if not word.id in new_words:
                new_words[word.id] = list()
            new_words[word.id].append(word)
        # remove words
        num_words_removed = 0
        for wid in list(self.word_items.keys()):
            if wid not in new_words:
                for item in self.word_items.pop(wid):
                    if debug_word_id and debug_word_id == wid:
                        print(f"word {wid}: refresh_page_view: removing item {item}")
                    self.scene.removeItem(item)
                    num_words_removed += 1
        # update or add words
        num_words_updated = 0
        num_words_added = 0
        for wid, words in new_words.items():
            add_words = []
            if wid in self.word_items:
                items = self.word_items[wid]
                if len(words) == 1 and len(items) == 1:
                    # simple case: no collisions in word id
                    # print("refresh_page_view: simple case: no collisions in word id")
                    # update word
                    word = words[0]
                    item = items[0]
                    if debug_word_id and debug_word_id == wid:
                        print(f"word {wid}: refresh_page_view: updating item {item}")
                    # update text and bbox
                    if item.word.text != word.text:
                        # FIXME this is rarely (never?) reached
                        # because item.word.text was already updated somewhere else
                        if item.text_item:
                            if debug_word_id and debug_word_id == wid:
                                print(f"word {wid}: refresh_page_view: updating item text: {item.text_item.text()!r} -> {word.text!r}")
                            item.text_item.setText(word.text)
                        else:
                            if debug_word_id and debug_word_id == wid:
                                print(f"word {wid}: refresh_page_view: not updating item text: no item.text_item")
                    else:
                        if debug_word_id and debug_word_id == wid:
                            print(f"word {wid}: refresh_page_view: not updating item text: no change: {word.text!r}")
                    if item.word.bbox != word.bbox:
                        # FIXME this is rarely (never?) reached
                        # because item.word.bbox was already updated somewhere else
                        if debug_word_id and debug_word_id == wid:
                            print(f"word {wid}: refresh_page_view: updating item bbox: {item.word.bbox!r} -> {word.bbox!r}")
                        x0, y0, x1, y1 = word.bbox
                        item.setPos(x0, y0)
                        item.setRect(0, 0, x1 - x0, y1 - y0)
                        item._update_text_position()
                    else:
                        if debug_word_id and debug_word_id == wid:
                            print(f"word {wid}: refresh_page_view: not updating item bbox: no change: {word.bbox}")
                    item.word = word  # rebind
                    num_words_updated += 1
                else:
                    # complex case: collisions in word id
                    # remove and re-create all items with this word id
                    print(f"refresh_page_view: FIXME collision in word id {wid!r}")
                    for word in words:
                        print(f"  word {word}")
                    for item in items:
                        print(f"  item {item}")
                    for item in items:
                        if debug_word_id and debug_word_id == wid:
                            print(f"word {wid}: refresh_page_view: removing item {item}")
                        self.scene.removeItem(item)
                    self.word_items[wid] = list()
                    # add words
                    add_words = words
            else:
                add_words = words
            if add_words:
                # add words
                for word in add_words:
                    item = WordItem(
                        word,
                        word_selected_cb=self.on_word_selected,
                        word_changed_cb=self.on_word_changed,
                    )
                    item.set_text_color(self.overlay_color)
                    if debug_word_id and debug_word_id == wid:
                        print(f"word {wid}: refresh_page_view: adding item {item}")
                    self.scene.addItem(item)
                    if not wid in self.word_items:
                        self.word_items[wid] = list()
                    self.word_items[wid].append(item)
                    num_words_added += 1
        if debug:
            print(f"refresh_page_view: removed {num_words_removed}, updated {num_words_updated}, added {num_words_added} words")
        # select changed word in code view
        @print_exceptions
        def select_changed_word():
            changed_word_item = self.find_word_item_by_word_id(self.changed_word_id)
            if changed_word_item:
                self.on_word_selected(changed_word_item)
        QTimer.singleShot(0, select_changed_word)

    @print_exceptions
    def find_word_item_by_word_id(self, word_id: str):
        for word_item in self.scene.items():
            if not isinstance(word_item, WordItem): continue
            if word_item.word.id == word_id:
                return word_item

    @print_exceptions
    def on_word_selected(self, word_item: WordItem):
        if self.source_editor.hasFocus():
            return
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

    @print_exceptions
    def on_word_changed(self, word_id: str, new_text: str = None, bbox=None, span_start: int = None):
        """Called when WordItem text changes.

        If the caller provides span_start (byte offset of the span's start tag),
        we call parser.update_by_span() to avoid id collisions. Otherwise fallback
        to the old parser.update(word_id, ...).
        """
        if debug_word_id and debug_word_id == word_id:
            print(f"word {word_id}: on_word_changed: new_text={new_text!r}, bbox={bbox}, span_start={span_start}")
        # Prefer span-based update (disambiguates duplicate ids)
        if span_start is not None:
            ok = self.parser.update_by_span(span_start, text=new_text, bbox=bbox)
        else:
            ok = self.parser.update(word_id, text=new_text, bbox=bbox)

        # reflect changed source immediately in code editor + redraw
        self.source_editor.update_from_page()
        # update the word positions
        # TODO incremental update
        # no. RuntimeError: Internal C++ object (WordItem) already deleted.
        # self.changed_word_id = word_id
        self.changed_word_id = str(word_id) # force-copy value
        self.refresh_page_view()

    @print_exceptions
    def on_code_cursor_changed(self, pos: int):
        # 1. Find which word covers this pos
        word = self.parser.find_word_at_offset(pos)
        if not word:
            return

        # 2. Get the corresponding WordItem
        items = self.word_items.get(word.id)
        if not items:
            return

        if len(items) > 1:
            print(f"on_code_cursor_changed: FIXME collision in word id {word.id!r}")
            for item in items:
                print(f"  item {item}")

        item = items[0]

        # 3. Center page view on that word
        self.page_view.centerOn(item)
        item.setSelected(True)

    @print_exceptions
    def add_new_word_from_page_view(self, rect: QRectF):
        x0, y0 = int(rect.x()), int(rect.y())
        x1, y1 = int(rect.x() + rect.width()), int(rect.y() + rect.height())
        new_id = "word_" + "".join(random.choices(string.ascii_letters + string.digits, k=8))
        new_word = Word(
            id=new_id,
            text="",
            bbox=(x0, y0, x1, y1),
            x_wconf=None,
            title_value=None,
            text_range=(0, 0),
            title_value_range=(0, 0),
            id_value_range=(0, 0),
            element_range=(0, 0),
            span_range=(0, 0),
        )
        # words = self.parser.find_words()
        # self.words = self.parser.find_words() # force update
        words = self.words
        lines = group_words_into_lines(words, y_threshold=50)
        line_idx, word_idx = find_insert_line_and_index(new_word.bbox, lines)

        # Determine insertion line number in source
        lines_in_source = self.source_editor.toPlainText().splitlines()
        word_to_line = {}
        # TODO better
        for idx, line in enumerate(lines_in_source):
            for w in words:
                if w.id in line:
                    word_to_line[w.id] = idx

        # TODO better. the new word should be inserted between old words in the same line
        if lines:
            line_words = lines[line_idx]
            if word_idx == 0:
                # Insert before first word in line
                insert_line = word_to_line.get(line_words[0].id, len(lines_in_source))
            else:
                # Insert after previous word
                insert_line = word_to_line.get(line_words[word_idx - 1].id, len(lines_in_source))
                insert_line += 1
        else:
            insert_line = 0

        new_span_line = f"      <span class='ocrx_word' id='{new_id}' title='bbox {x0} {y0} {x1} {y1}'></span>"

        lines_in_source.insert(insert_line, new_span_line)
        new_source = "\n".join(lines_in_source)
        self.source_editor.setPlainText(new_source)
        self.parser.set_source(new_source)
        self.refresh_page_view()

        # Place cursor inside new span
        cursor = self.source_editor.textCursor()
        # TODO better. use lines_in_source and insert_line
        pos = new_source.find(new_span_line) + len(new_span_line) - len("</span>")
        cursor.setPosition(pos)
        self.source_editor.setTextCursor(cursor)
        self.source_editor.setFocus()

    @print_exceptions
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

    @print_exceptions
    def save_hocr_as(self):
        """Save to new file via dialog."""
        filename, _ = QFileDialog.getSaveFileName(self, "Save HOCR File", "", "HOCR Files (*.hocr *.html *.xhtml);;All Files (*)")
        if filename:
            self.hocr_file = filename
            self.save_hocr()


def group_words_into_lines(words, y_threshold=10):
    """
    Group words into lines based on vertical proximity.
    y_threshold: maximum vertical distance to consider words on same line.
    Returns list of lines, each line is a list of Word objects sorted by x.
    """
    sorted_words = sorted(words, key=lambda w: w.bbox[1])  # sort by y
    lines = []
    for w in sorted_words:
        added = False
        for line in lines:
            # Compare with the first word of the line
            if abs(w.bbox[1] - line[0].bbox[1]) <= y_threshold:
                line.append(w)
                added = True
                break
        if not added:
            lines.append([w])
    # Sort each line by x
    for line in lines:
        line.sort(key=lambda w: w.bbox[0])
    return lines


def find_insert_line_and_index(new_bbox, lines, line_y_tolerance=50):
    """
    Find the line and position inside that line where the new word should go.
    Returns (line_index, word_index_inside_line)
    """
    debug = False
    # debug = True
    y0, x0 = new_bbox[1], new_bbox[0]
    debug and print("new_bbox y0", y0)

    # Find nearest line by vertical position
    line_index = len(lines)
    for i, line in enumerate(lines):
        line_y = sum(w.bbox[1] for w in line) / len(line)  # avg y
        debug and print(f"line {i}: line_y", line_y, "text", repr(" ".join(w.text for w in line)))
        if y0 < (line_y + line_y_tolerance):
            line_index = i
            break
    if line_index == len(lines):
        line_index = len(lines) - 1 if lines else 0

    debug and print("new_bbox x0", x0)

    # Within the line, find nearest word by x
    line = lines[line_index]
    word_index = 0
    for i, w in enumerate(line):
        debug and print(f"word {i}: word_x", w.bbox[0], "text", repr(w.text))
        if x0 < w.bbox[0]:
            word_index = i
            break
        word_index = i + 1
    return line_index, word_index


def main():
    parser = argparse.ArgumentParser(description="HOCR Editor")
    parser.add_argument("hocr_file", help="Path to HOCR file")
    parser.add_argument(
        "--overlay-color",
        default=None,
        help="Overlay color (color name or #RRGGBB)",
    )
    args = parser.parse_args()

    # handle Ctrl+C from terminal
    signal.signal(signal.SIGINT, signal.SIG_DFL)

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

    editor.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
