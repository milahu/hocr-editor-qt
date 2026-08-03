#!/usr/bin/env python3

import os
import sys
import re
import io
import argparse
import signal
import random
import string
import traceback
import shutil
import subprocess
import PIL.Image
if os.name == "nt":
    import winreg
from typing import (
    Optional,
    Tuple,
    Any,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QGraphicsScene,
    QGraphicsRectItem, QGraphicsTextItem, QGraphicsItem,
    QGraphicsSimpleTextItem, QGraphicsProxyWidget,
    QWidget, QVBoxLayout, QLabel, QLineEdit, QSplitter,
    QTabWidget,
    QGraphicsEllipseItem,
    QDockWidget,
    QFileDialog,
    QMessageBox,
    QStyleOptionGraphicsItem,
    QStyle,
    QPlainTextEdit,
    QColorDialog,
    QStyleFactory,
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
    QPalette,
)
from PySide6.QtCore import QRectF, Qt, QPointF
from PySide6.QtCore import (
    QTimer,
    QSize,
    QSizeF,
    QTranslator,
    QLocale,
    QLibraryInfo,
    QEvent,
    QSignalBlocker,
)

from hocr_parser import HocrParser, Word
from hocr_parser import print_exceptions
from hocr_parser import debug, debug_word_id
from epub_fxl_parser import EpubFxlParser, EpubFxlWord
from hocr_source_editor import HocrSourceEditor
from resizable_rect_item import ResizableRectItem
import git_helpers
import color_helpers

bbox_re = re.compile(r"bbox (\d+) (\d+) (\d+) (\d+)")
xwconf_re = re.compile(r"x_wconf (\d+)")


# --- utilities for images / dark mode ---
def _extract_image_from_title(title: bytes) -> Optional[bytes]:
    m = re.search(rb'image\s+"([^"]+)"', title)
    return m.group(1) if m else None

def _is_dark_mode(widget: QWidget) -> bool:
    """
    Cross-platform luminance-based dark mode check.
    Uses the application's palette rather than an individual widget.
    """
    # fix: the "is dark mode" check only works in one direction
    # dark -> light: ok
    # light -> dark: fail
    if os.name == "nt":
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
        except OSError:
            return False
    pal = widget.palette()
    bg = pal.color(QPalette.Window)
    luminance = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
    return luminance < 128

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
            self.text_item = QGraphicsSimpleTextItem(word.text_bytes, self)
            self._update_text_position()
        else:
            # disable text overlay
            self.text_item = None

    @print_exceptions
    def __str__(self):
        try:
            pos = self.scenePos()
            pos = (pos.x(), pos.y())
        except RuntimeError:
            # Internal C++ object (WordItem) already deleted.
            pos = "?"
        return (
            f"WordItem(" +
            f"span_range={self.word.span_range!r}" +
            f", id_bytes={self.word.id_bytes!r}" +
            f", text_bytes={self.word.text_bytes!r}" +
            f", bbox={self.word.bbox!r}" +
            f", pos={pos!r}" +
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
            if debug_word_id and debug_word_id == self.word.id_bytes:
                print(f"word {self.word.id_bytes}: update_word_bbox: {old_bbox} -> {new_bbox}")
            self.word.bbox = new_bbox
            self.word_changed_cb(
                self.word.id_bytes,
                bbox=new_bbox,
                span_start=self.word.span_range[0],
            )
        else:
            if debug_word_id and debug_word_id == self.word.id_bytes:
                print(f"word {self.word.id_bytes}: update_word_bbox: no change")

    @print_exceptions
    def mouseDoubleClickEvent(self, event):
        if self.editor is None:
            line_edit = QLineEdit(self.word.text_bytes)
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
        # print(f"commit_text: word.text_bytes {self.word.text_bytes!r} -> {new_text!r}")
        self.word.text_bytes = new_text
        if self.text_item:
            self.text_item.setText(new_text)
        if debug_word_id and debug_word_id == self.word.id_bytes:
            print(f"word {self.word.id_bytes}: commit_text: new_text={new_text!r}")
        self.word_changed_cb(
            self.word.id_bytes, new_text,
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
            if new_text != self.word.text_bytes:
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
            editor: "HocrEditor",
            add_new_word_cb: Any,
        ):
        super().__init__(editor.scene)
        self.editor = editor
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

    # TODO remove
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

            # crop + OCR
            cropped = self._crop_pixmap(rect)
            hocr_bytes = None
            if cropped:
                try:
                    hocr_bytes = self._ocr_image(cropped)
                except subprocess.TimeoutExpired as exc:
                    print(f"mouseReleaseEvent: _ocr_image failed: {exc}")
            if hocr_bytes:
                # TODO parse hocr, merge with self.parser.source_bytes
                # similar to add_new_word_cb -> add_new_word_from_page_view
                # print("mouseReleaseEvent: hocr_bytes:\n" + hocr_bytes.decode("utf8"))
                parser = HocrParser(hocr_bytes)
                parse_id = get_random_bytestring()
                # rect is QRectF of user selection in scene/pixmap coordinates
                x_offset, y_offset = rect.x(), rect.y()
                scale_x = rect.width() / cropped.width()
                scale_y = rect.height() / cropped.height()
                for word in parser.find_words():
                    # expand word.id_bytes to avoid collisions
                    # assume that word.id_bytes has the pattern "word_[0-9]+_[0-9]+"
                    word.id_bytes = word.id_bytes[:5] + parse_id + word.id_bytes[4:]
                    (x0, y0, x1, y1) = word.bbox
                    # scale bbox from cropped-image space to scene/pixmap space
                    old_bbox = word.bbox
                    word.bbox = (
                        int(x0 * scale_x + x_offset),
                        int(y0 * scale_y + y_offset),
                        int(x1 * scale_x + x_offset),
                        int(y1 * scale_y + y_offset),
                    )
                    # FIXME update the range values in add_new_word_cb
                    word.byte_range = (0, 0)
                    word.title_value_range = (0, 0)
                    word.id_bytes_value_range = (0, 0)
                    word.element_range = (0, 0)
                    word.span_range = (0, 0)
                    self.add_new_word_cb(word=word)
            else:
                self.add_new_word_cb(rect=rect)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _crop_pixmap(self, rect: QRectF) -> QImage:
        if not self.editor.page_pixmap:
            return None
        # Clamp rect to image bounds
        img_rect = QRectF(self.editor.page_pixmap.rect())
        rect = rect.intersected(img_rect)
        if rect.isEmpty():
            return None
        return self.editor.page_pixmap.copy(rect.toRect()).toImage()

    def _qimage_to_pil(self, qimage: QImage) -> PIL.Image.Image:
        qimage = qimage.convertToFormat(QImage.Format_RGBA8888)
        width, height = qimage.width(), qimage.height()
        ptr = qimage.bits()
        buf = bytes(ptr)
        img = PIL.Image.frombuffer("RGBA", (width, height), buf, "raw", "RGBA", 0, 1)
        return img.convert("RGB")

    def _ocr_image(self, qimage: QImage, langs: Optional[str] = None, timeout: int = 30) -> bytes:
        if shutil.which(self.editor.args.tesseract_command) is None:
            # tesseract is not installed
            return None
        langs = langs or self.editor.ocr_langs
        pil_img = self._qimage_to_pil(qimage)
        # pytesseract creates PNG tempfiles in /tmp/
        # https://github.com/madmaze/pytesseract/issues/172
        # https://stackoverflow.com/questions/34248492
        # TODO? use https://github.com/sirfz/tesserocr
        tiff_bytes = pil_to_tiff_bytes(pil_img)
        args = [
            self.editor.args.tesseract_command,
            "-", # input: stdin
            "-", # output: stdout
            "-l", langs,
            "-c", "tessedit_create_hocr=1",
            # TODO get dpi value from hocr file
            # <div class='ocr_page' id='page_1' title='...; scan_res 300 300'>
            # "--dpi", "300",
            "--loglevel", "WARN", # ALL, TRACE, DEBUG, INFO, WARN, ERROR, FATAL, OFF
        ]
        if self.editor.args.tessdata_dir:
            args += [
                "--oem", "1",
                "--psm", "6",
                "--tessdata-dir", self.editor.args.tessdata_dir,
            ]
        hocr_bytes = subprocess.check_output(args, input=tiff_bytes, timeout=timeout)
        return hocr_bytes


def pil_to_tiff_bytes(img: PIL.Image.Image) -> bytes:
    # why? TIFF is faster than PNG
    buf = io.BytesIO()
    img.save(buf, format="tiff")
    return buf.getvalue()


class HocrEditor(QMainWindow):
    @print_exceptions
    def __init__(self, args: Any):
        super().__init__()
        self.args = args
        self.hocr_file = args.hocr_file  # remember original filename

        self._hocr_editor = self

        self.overlay_color = None
        if args.overlay_color:
            overlay_color = QColor(args.overlay_color)
            if overlay_color.isValid():
                self.overlay_color = overlay_color
            else:
                print(f"Warning: invalid overlay color {args.overlay_color}")

        self.scene = QGraphicsScene()

        # TODO update self.modified from page_view and source_editor
        self.modified = False
        self.modified = True # always ask to save before exit # TODO remove

        # TODO rename to self.page_view
        self.view = PageView(
            self,
            add_new_word_cb=self.add_new_word_from_page_view,
        )
        self.page_view = self.view

        self.setWindowTitle(f"{os.path.basename(self.hocr_file)} - HOCR Editor")
        self.setWindowIcon(QIcon(os.path.dirname(__file__) + "/Eo_circle_blue_letter-h.2.png"))

        # track chosen overlay color
        self.overlay_color = QColor("black")

        # Prevent recursive updates between:
        #   plain text editor
        #   HOCR source editor
        #   page view
        # TODO reduce this to one or two variables
        # one variable: self._updating_views
        # two variables: self._updating_plain_text, self._updating_hocr_source
        self._updating_views = False
        self._updating_plain_text = False
        self._updating_hocr_source = False

        self._syncing_from_parser = False

        # TODO reduce this to one variable?
        # in hocr_source_editor.py we have this:
        # self._updating = False  # avoid recursive updates

        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(2000)
        self._rebuild_timer.timeout.connect(self._rebuild_hocr_model)

        # plain text editor
        # from plain_text_editor import PlainTextEditor
        # self.plain_text_editor = PlainTextEditor()
        self.plain_text_editor = QPlainTextEdit()
        self.plain_text_editor.document().contentsChange.connect(
            self.on_plain_text_contents_change
        )
        # self.plain_text_editor.document().contentsChange.connect(
        #     self.on_plain_text_changed
        # )
        font = self.plain_text_editor.font()
        if font.pointSizeF() > 0:
            # increase the font size to 200%
            font.setPointSizeF(font.pointSizeF() * 2.0)
            self.plain_text_editor.setFont(font)

        # load words into scene
        # set self.parser
        self.words: list[Word] = []
        self.word_items: dict[str, list[WordItem]] = {}
        self.page_pixmap = None
        self.ocr_langs = "eng"
        self.load_hocr(self.hocr_file)

        # no, this is redundant
        # TODO where do we call parser._build_model()
        # # Build the paragraph/line/word model used by
        # # the plain-text projection.
        # self.parser.rebuild_model()

        # no, this requires self.bottom_tabs -> move down
        # self.plain_text_editor.setPlainText(
        #     self.parser.get_plain_text()
        # )

        self.changed_word_id: Optional[bytes] = None

        # HOCR source editor dock
        # TODO rename to self.hocr_source_editor
        self.source_editor = HocrSourceEditor(
            self.parser,
            update_page_cb=self.refresh_page_view,
            cursor_sync_cb=self.on_code_cursor_changed,
            parent=self,
        )
        self.hocr_source_editor = self.source_editor

        # TODO what
        self.hocr_source_editor.editor.document().setDocumentMargin(4)

        self.hocr_source_editor.editor.setLineWrapMode(QPlainTextEdit.NoWrap)

        if debug:
            print("HocrEditor.__init__: self.bottom_tabs = QTabWidget()")
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.addTab(
            self.plain_text_editor,
            "Text",
        )
        self.bottom_tabs.addTab(
            self.source_editor,
            "HOCR",
        )
        self.bottom_tabs.setCurrentWidget(
            self.plain_text_editor
        )
        self.bottom_tabs.currentChanged.connect(
            self.on_bottom_tab_changed
        )

        # Splitter to control widths
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.view)
        splitter.addWidget(self.bottom_tabs)

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

        # give more height to source_editor
        view_height, source_editor_height = 100, 200
        splitter.setSizes([view_height, source_editor_height])
        splitter.setStretchFactor(0, view_height)  # self.view
        splitter.setStretchFactor(1, source_editor_height)  # self.source_editor

        # TODO better
        for delay in [1, 10, 20, 50, 100, 200, 500]:
            QTimer.singleShot(delay, self.view.fit_width)  # fit width after layout

        self._syncing_from_parser = True
        if debug:
            print("HocrEditor.__init__: calling self.plain_text_editor.setPlainText")
        try:
            self.plain_text_editor.setPlainText(
                self.parser.get_plain_text()
            )

            if debug:
                print("HocrEditor.__init__: calling self.hocr_source_editor.editor.setPlainText")
            self.hocr_source_editor.editor.setPlainText(
                self.parser.get_source_string()
            )
        finally:
            self._syncing_from_parser = False

    @print_exceptions
    def on_bottom_tab_changed(self, index):
        print("HocrEditor.on_bottom_tab_changed: current_widget = self.bottom_tabs.widget(index)")

        current_widget = self.bottom_tabs.widget(index)

        if current_widget is self.plain_text_editor:
            self._updating_plain_text = True

            try:
                print("HocrEditor.on_bottom_tab_changed: calling self.plain_text_editor.setPlainText")
                self.plain_text_editor.setPlainText(
                    self.parser.get_plain_text()
                )
            finally:
                self._updating_plain_text = False

        elif current_widget is self.hocr_source_editor:
            self._updating_hocr_source = True
            self._updating_views = True

            try:
                print("HocrEditor.on_bottom_tab_changed: calling self.hocr_source_editor.editor.setPlainText")
                self.hocr_source_editor.editor.setPlainText(
                    self.parser.get_source_string()
                )
            finally:
                self._updating_hocr_source = False
                self._updating_views = False

    @print_exceptions
    def _rebuild_hocr_model(self):
        print("HocrEditor._rebuild_hocr_model: calling self.parser.rebuild_model")
        self.parser.rebuild_model()
        self.reconcile_page_items()
        self.refresh_plain_text_editor()
        self.refresh_source_editor()

    @print_exceptions
    def on_plain_text_contents_change_zzzzzzzzz(
        self,
        position,
        chars_removed,
        chars_added,
    ):
        # if self._updating_views:
        if self._updating_plain_text:
            return
        if self._syncing_from_parser:
            return
        inserted_text = ""
        if chars_added:
            cursor = self.plain_text_editor.textCursor()
            # contentsChange is emitted after the document has changed,
            # so reconstruct the inserted range.
            print(f"line 640: setPosition {position} {position + chars_added} # chars_removed={chars_removed!r} # chars_added={chars_added!r}")
            cursor.setPosition(position)
            # FIXME QTextCursor::setPosition: Position '1' out of range
            cursor.setPosition(
                position + chars_added,
                QTextCursor.MoveMode.KeepAnchor,
            )
            inserted_text = cursor.selectedText()
            # Qt represents newline characters in QTextDocument
            # as paragraph separators.
            inserted_text = inserted_text.replace(
                "\u2029",
                "\n",
            )
        success = self.parser.apply_plain_text_edit(
            position=position,
            chars_removed=chars_removed,
            inserted_text=inserted_text,
        )
        if success:
            self.schedule_model_rebuild_check()
        else:
            self.schedule_full_rebuild()

    @print_exceptions
    def on_plain_text_contents_change_zzzzzzzzzz(
        self,
        position,
        chars_removed,
        chars_added,
    ):
        # if self._updating_views:
        if self._updating_plain_text:
            return
        print(
            f"contentsChange: "
            f"position={position}, "
            f"removed={chars_removed}, "
            f"added={chars_added}"
        )

        # Process after Qt has finished modifying the document.
        QTimer.singleShot(
            0,
            lambda: self.process_plain_text_change(
                position,
                chars_removed,
                chars_added,
            ),
        )

    @print_exceptions
    def on_plain_text_contents_change_zzzzzzzz(
        self,
        position,
        chars_removed,
        chars_added,
    ):
        if self._updating_plain_text:
            return

        # Wait until Qt has finished applying the edit.
        # Process after Qt has finished modifying the document.
        QTimer.singleShot(
            0,
            lambda: self.process_plain_text_change(
                position,
                chars_removed,
                chars_added,
            ),
        )

    def on_plain_text_contents_change(
        self,
        position,
        chars_removed,
        chars_added,
    ):
        if self._updating_plain_text:
            return
        if self._syncing_from_parser:
            return

        old_text = self.parser.get_plain_text()

        # IMPORTANT:
        # old_text is the parser's current model,
        # not the already-modified QTextDocument.

        cursor = self.plain_text_editor.textCursor()

        new_text = self.plain_text_editor.toPlainText()

        # For the first working prototype:
        if debug:
            print("HocrEditor.on_plain_text_contents_change: calling self.parser.replace_plain_text")
        self.parser.replace_plain_text(new_text)

        if debug:
            print("HocrEditor.on_plain_text_contents_change: calling self.refresh_from_parser")
        self.refresh_from_parser()

    @print_exceptions
    def process_plain_text_change_zzzzzzzzz(
        self,
        position,
        chars_removed,
        chars_added,
    ):
        document = self.plain_text_editor.document()

        # QTextDocument character count.
        document_length = document.characterCount()

        # debug
        print(
            f"process_plain_text_change:",
            f"position={position}",
            f"removed={chars_removed}",
            f"added={chars_added}",
            f"document_length={document_length}",
        )

        # Defensive clamping.
        start_char = max(
            0,
            min(position, document_length),
        )

        end_char = max(
            start_char,
            min(
                position + chars_added,
                document_length,
            ),
        )

        cursor = QTextCursor(document)

        cursor.setPosition(
            start_char,
            QTextCursor.MoveMode.MoveAnchor,
        )

        cursor.setPosition(
            end_char,
            QTextCursor.MoveMode.KeepAnchor,
        )

        inserted_text = cursor.selectedText()

        print(f"inserted_text: {inserted_text!r}")

    @print_exceptions
    def process_plain_text_change_zzzzzzzzz(
        self,
        position,
        chars_removed,
        chars_added,
    ):
        # if self._updating_views:
        if self._updating_plain_text:
            return

        new_text = self.plain_text_editor.toPlainText()

        inserted_text = new_text[
            position:position + chars_added
        ]

        print(
            "position:",
            position,
            "removed:",
            chars_removed,
            "added:",
            chars_added,
            "inserted:",
            repr(inserted_text),
            # inserted_text,
        )

    @print_exceptions
    def process_plain_text_change(
        self,
        position,
        chars_removed,
        chars_added,
    ):
        if self._updating_plain_text:
            return
        if self._syncing_from_parser:
            return

        new_text = self.plain_text_editor.toPlainText()

        print(
            "plain text changed:",
            repr(new_text),
        )

        try:
            self.parser.replace_plain_text(
                new_text,
            )

        except NotImplementedError as exc:
            print(
                "Plain-text edit not yet supported:",
                exc,
            )
            return

        # Update all views from parser.
        self.refresh_from_parser()

    @print_exceptions
    def refresh_from_parser(self):
        """
        Synchronize the page view and the currently visible bottom view
        from the parser.

        The inactive bottom-tab is deliberately not updated here.
        """

        # The page view is always visible.
        self.rebuild_page_view()

        # print("HocrEditor.refresh_from_parser: current_widget = self.bottom_tabs.currentWidget()")

        debug = 0
        if debug:
            print("\n========== refresh_from_parser ==========")
            parser_source = self.parser.get_source_string()
            print("PARSER source length:", len(parser_source))
            print("PARSER source bytes length:", len(self.parser.source_bytes))
            print("EDITOR source length BEFORE:", len(self.hocr_source_editor.editor.toPlainText()))
            print("EDITOR == PARSER BEFORE:", self.hocr_source_editor.editor.toPlainText() == parser_source)

        # Only refresh the currently visible bottom tab.
        current_widget = self.bottom_tabs.currentWidget()

        if debug:
            print(
                "current_widget:",
                current_widget,
            )

        if current_widget is self.plain_text_editor:

            editor = self.plain_text_editor

            new_text = self.parser.get_plain_text()
            # Avoid unnecessary document replacement.
            if editor.toPlainText() == new_text:
                return

            self._updating_plain_text = True

            if debug:
                print("refreshing plain text editor: calling self.plain_text_editor.setPlainText")

            # Save cursor state before replacing the document.
            cursor = editor.textCursor()
            cursor_position = cursor.position()
            anchor_position = cursor.anchor()

            # save scroll position
            vertical_scroll_value = editor.verticalScrollBar().value()

            try:
                editor.setPlainText(new_text)
            finally:
                self._updating_plain_text = False

            # Restore selection/cursor.
            new_cursor = editor.textCursor()
            max_position = len(new_text)
            cursor_position = min(cursor_position, max_position)
            anchor_position = min(anchor_position, max_position)
            new_cursor.setPosition(anchor_position, QTextCursor.MoveMode.MoveAnchor)
            new_cursor.setPosition(cursor_position, QTextCursor.MoveMode.KeepAnchor)
            editor.setTextCursor(new_cursor)

            # Restore scroll position.
            editor.verticalScrollBar().setValue(vertical_scroll_value)

        elif current_widget is self.hocr_source_editor:
            self._updating_hocr_source = True
            self._updating_views = True

            if debug:
                print("refreshing HOCR source editor: calling self.hocr_source_editor.editor.setPlainText")

            try:
                self.hocr_source_editor.editor.setPlainText(
                    self.parser.get_source_string()
                )
            finally:
                self._updating_hocr_source = False
                self._updating_views = False

            if debug:
                # ---------------------------------------------------------
                # Diagnostics AFTER update
                # ---------------------------------------------------------
                print("EDITOR source length AFTER:", len(self.hocr_source_editor.editor.toPlainText()))
                print("EDITOR == PARSER AFTER:", self.hocr_source_editor.editor.toPlainText() == parser_source)
                print("========================================\n")

    @print_exceptions
    def schedule_full_rebuild(self):
        self._rebuild_timer.start()

    @print_exceptions
    def on_plain_text_changed(self):
        # if self._updating_views:
        if self._updating_plain_text:
            return
        if self._syncing_from_parser:
            return

        new_plain_text = (
            self.plain_text_editor.toPlainText()
        )

        try:
            # self._updating_views = True
            self._updating_plain_text = True

            new_source = (
                self.parser.rebuild_hocr_from_plain_text(
                    new_plain_text
                )
            )

            self.parser.set_source_string(
                new_source
            )

            print("HocrEditor.on_plain_text_changed: calling self.parser.rebuild_model")
            self.parser.rebuild_model()

            print("HocrEditor.on_plain_text_changed: calling self.hocr_source_editor.editor.setPlainText")
            # Update source editor.
            self.hocr_source_editor.editor.setPlainText(
                self.parser.get_source_string()
            )

            # Rebuild graphics.
            self.rebuild_page_view()

        except Exception as exc:
            print(
                "Plain text update failed:",
                exc,
            )

        finally:
            # self._updating_views = False
            self._updating_plain_text = False

    @print_exceptions
    def on_hocr_source_changed(self):
        if self._updating_views:
            return

        new_source = (
            self.hocr_source_editor.toPlainText()
        )

        try:
            self._updating_views = True

            self.parser.set_source_string(
                new_source
            )

            print("HocrEditor.on_hocr_source_changed: calling self.parser.rebuild_model")
            self.parser.rebuild_model()

            # Update plain text.
            print("HocrEditor.on_hocr_source_changed: calling self.plain_text_editor.setPlainText")
            self.plain_text_editor.setPlainText(
                self.parser.get_plain_text()
            )

            # Rebuild page.
            self.rebuild_page_view()

        except Exception as exc:
            print(
                "HOCR source update failed:",
                exc,
            )

        finally:
            self._updating_views = False

    # FIXME refactor load_words and rebuild_page_view
    @print_exceptions
    def rebuild_page_view(self):
        # Remove existing word items.
        self.scene.clear()

        # Rebuild from parser's current model.
        for paragraph in self.parser.paragraphs:
            for line in paragraph.lines:
                for word in line.words:

                    item = WordItem(
                        word,
                        self.on_word_selected,
                        self.on_word_changed,
                    )

                    self.scene.addItem(
                        item
                    )

        # Re-add page background if you have one.
        self.load_page_background()

    def source_byte_to_qt_position(self, byte_offset: int) -> int:
        """
        Convert a UTF-8 byte offset in parser.source_bytes into
        a QTextCursor character position.

        QTextCursor positions are character offsets in the QString,
        whereas Tree-sitter ranges are byte offsets in UTF-8.
        """

        source_bytes = self.parser.source_bytes

        # Clamp to valid range.
        byte_offset = max(
            0,
            min(
                byte_offset,
                len(source_bytes),
            ),
        )

        # Decode only the prefix.
        #
        # This gives the number of Unicode characters before the
        # requested UTF-8 byte offset.
        #
        # errors="replace" guarantees that the result is always
        # decodable, although normally the offset should be on a
        # UTF-8 character boundary.
        prefix = source_bytes[
            :byte_offset
        ].decode(
            self.parser.source_encoding,
            errors="replace",
        )

        return len(prefix)

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
        with open(hocr_file, "rb") as f:
            source_bytes = f.read()

        # normalize line endings for QPlainTextEdit
        # fix: wrong position mappings between page view and code view
        # note: we dont restore the original line endings in save_hocr
        # because git will normalize line endings anyway
        # so different line endings dont show up in "git diff"
        source_bytes = source_bytes.replace(b"\r\n", b"\n")

        if b'<meta name="generator" content="hocr-to-epub-fxl" />' in source_bytes:
            # FIXME convert epub-fxl to hocr = inverse of hocr-to-epub-fxl
            return self.load_epub_fxl_bytes(source_bytes)

        # get tesseract languages parameter value from hocr file
        # TODO https://github.com/tesseract-ocr/tesseract/issues/4455
        # TODO https://github.com/tesseract-ocr/tesseract/issues/4591
        # FIXME use tree-sitter to parse the lang attributes
        ocr_par_lang_regex = rb"<p class='ocr_par' id='[^']+' lang='([^']+)'"
        ocrx_word_lang_regex = rb"<span class='ocrx_word' id='[^']+' title='[^']+' lang='([^']+)'"
        langs = []
        if match := re.search(ocr_par_lang_regex, source_bytes):
            main_lang = match.group(1).decode("utf8")
            debug and print(f"load_hocr: found main language {main_lang!r}")
            langs.append(main_lang)
        else:
            debug and print(f"load_hocr: not found main language")
        extra_langs = dict() # emulate OrderedSet
        for match in re.finditer(ocrx_word_lang_regex, source_bytes):
            extra_lang = match.group(1).decode("utf8")
            extra_langs[extra_lang] = None
        extra_langs = list(extra_langs.keys())
        debug and print(f"load_hocr: found extra languages {extra_langs!r}")
        langs += extra_langs
        self.ocr_langs = "+".join(langs)
        print(f"load_hocr: found ocr_langs {self.ocr_langs!r}")

        self.parser = HocrParser(source_bytes)

        if debug:
            print("HocrEditor.load_hocr: calling self.parser.find_words")
        self.words = self.parser.find_words()
        # print("self.words", self.words)

        # # Build the paragraph/line/word model used by
        # # the plain-text projection.
        # self.parser.rebuild_model()

        # # Populate plain-text editor
        # self._updating_plain_text = True
        # try:
        #     self.plain_text_editor.setPlainText(
        #         self.parser.get_plain_text()
        #     )
        # finally:
        #     self._updating_plain_text = False

        # # Populate source editor
        # self._updating_hocr_source = True
        # try:
        #     self.hocr_source_editor.editor.setPlainText(
        #         self.parser.get_source_string()
        #     )
        # finally:
        #     self._updating_hocr_source = False

        if debug:
            print("HocrEditor.load_hocr: calling self.load_words")
        self.load_words()

        # QTimer.singleShot(0, self.view.fit_width)  # fit width after layout

    @print_exceptions
    def load_epub_fxl_bytes(self, source_bytes):
        # TODO get tesseract languages parameter value from hocr file
        # TODO https://github.com/tesseract-ocr/tesseract/issues/4455

        self.parser = EpubFxlParser(source_bytes)
        self.words = self.parser.find_words()
        # print("self.words", self.words)

        self.load_words()

    def load_page_background(self):
        # --- add page images ---
        for page in self.parser.find_pages():
            # print("page", page)
            img_path = _extract_image_from_title(page.title_value)
            img_path = img_path.decode(self.parser.source_encoding, errors="replace")
            img_path = os.path.join(
                os.path.dirname(self.hocr_file),
                img_path
            )
            if img_path and os.path.exists(img_path):
                pixmap = QPixmap(img_path)
                if _is_dark_mode(self.view):
                    pixmap = _invert_pixmap(pixmap)
                self.page_pixmap = pixmap
                self.scene.addPixmap(pixmap).setZValue(-1)
            # FIXME support hocr files with multiple pages
            break # stop after first page

    # FIXME refactor load_words and rebuild_page_view
    @print_exceptions
    def load_words(self):
        """Populate the scene with WordItems from parser"""
        self.load_page_background()

        for word in self.parser.find_words():
            item = WordItem(
                word,
                word_selected_cb=self.on_word_selected,
                word_changed_cb=self.on_word_changed,
            )
            self.scene.addItem(item)
            if not word.id_bytes in self.word_items:
                self.word_items[word.id_bytes] = list()
            self.word_items[word.id_bytes].append(item)

    @print_exceptions
    def refresh_page_view(self, force=False):
        """Update words from parser"""
        if force:
            # slow non-incremental update
            self.scene.clear()
            self.load_words()
            return
        new_words = dict()
        for word in self.parser.find_words():
            if not word.id_bytes in new_words:
                new_words[word.id_bytes] = list()
            new_words[word.id_bytes].append(word)
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
                    if item.word.text_bytes != word.text_bytes:
                        # FIXME this is rarely (never?) reached
                        # because item.word.text_bytes was already updated somewhere else
                        if item.text_item:
                            if debug_word_id and debug_word_id == wid:
                                print(f"word {wid}: refresh_page_view: updating item text: {item.text_item.text()!r} -> {word.text_bytes!r}")
                            item.text_item.setText(word.text_bytes)
                        else:
                            if debug_word_id and debug_word_id == wid:
                                print(f"word {wid}: refresh_page_view: not updating item text: no item.text_item")
                    else:
                        if debug_word_id and debug_word_id == wid:
                            print(f"word {wid}: refresh_page_view: not updating item text: no change: {word.text_bytes!r}")
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
            if word_item.word.id_bytes == word_id:
                return word_item

    @print_exceptions
    def on_word_selected(self, word_item: WordItem):
        if self.source_editor.editor.hasFocus():
            return

        debug = False
        # debug = True

        if debug:
            word = word_item.word
            print("\n========== on_word_selected ==========")
            print("id(word):", id(word))
            print("word.id_bytes:", word.id_bytes)
            print("word.text_bytes:", repr(word.text_bytes))
            print("word.byte_range:", word.byte_range)
            print("id(self.parser.source_bytes):", id(self.parser.source_bytes))
            print("len(self.parser.source_bytes):", len(self.parser.source_bytes))
            editor_text = self.hocr_source_editor.editor.toPlainText()
            print("len(editor_text):", len(editor_text))
            print("self.parser.get_source_string() == editor_text:", self.parser.get_source_string() == editor_text)
            # Show the text around the parser's expected range.
            start_byte, end_byte = word.byte_range
            print("self.parser.source_bytes[start_byte:end_byte]:", repr(self.parser.source_bytes[start_byte:end_byte]))
            # Show the same range in the Qt editor.
            print("editor_text[start_byte:end_byte]:", repr(editor_text[start_byte:end_byte]))
            print("word._debug_source_id:", getattr(word, "_debug_source_id", None))
            print("word._debug_source_len:", getattr(word, "_debug_source_len", None))
            print("word._debug_source_slice:", getattr(word, "_debug_source_slice", None))

        if 1:
            word = word_item.word
            word_id = word.id_bytes
            print("received word:")
            print("  word.id_bytes:", word.id_bytes)
            print("  word.text_bytes:", repr(word.text_bytes))
            print("  word.byte_range:", word.byte_range)
            # IMPORTANT:
            # Look up the word again in the CURRENT parser model.
            current_word = self.parser.get_word_by_id(word_id)
            if current_word is None:
                print(f"ERROR: word {word_id!r} not found in current parser model")
                return
            print("current parser word:")
            print("  current_word.id_bytes:", current_word.id_bytes)
            print("  current_word.text_bytes:", repr(current_word.text_bytes))
            print("  current_word.byte_range:", current_word.byte_range)
            start_byte, end_byte = current_word.byte_range
            editor_text = self.hocr_source_editor.editor.toPlainText()
            print("len(self.parser.get_source_string()):", len(self.parser.get_source_string()))
            print("len(editor_text):", len(editor_text))
            print("self.parser.get_source_string() == editor_text:", self.parser.get_source_string() == editor_text)
            print("self.parser.source_bytes[start_byte:end_byte]:", self.parser.source_bytes[start_byte:end_byte])
            start_char = self.source_byte_offset_to_char_offset(start_byte)
            end_char = self.source_byte_offset_to_char_offset(end_byte)
            print("editor_text[start_char:end_char]:", repr(editor_text[start_char:end_char]))

            word = current_word

        if 0:
            # wrong?
            # Convert byte offsets to character offsets
            start_char = len(self.parser.source_bytes[:word_item.word.byte_range[0]].decode(
                self.parser.source_encoding, errors="replace"))
            end_char = start_char + len(word_item.word.text_bytes.decode(
                self.parser.source_encoding, errors="replace"))
        elif 1:
            start_byte, end_byte = word.byte_range
            start_char = self.source_byte_offset_to_char_offset(start_byte)
            end_char = self.source_byte_offset_to_char_offset(end_byte)
            if debug:
                print(
                    "WORD RANGE:",
                    repr(word.text_bytes),
                    "byte_range =",
                    (start_byte, end_byte),
                    "char_range =",
                    (start_char, end_char),
                )

        if debug:
            selected_text = self.hocr_source_editor.editor.toPlainText()[start_char:end_char]
            print(
                "SELECT RANGE:",
                f"start_char={start_char}",
                f"end_char={end_char}",
                f"selected_text={selected_text!r}",
            )

        # Set selection
        cursor = self.source_editor.editor.textCursor()
        print(f"line 1020: setPosition {start_char} {end_char}")
        cursor.setPosition(start_char)
        cursor.setPosition(end_char, QTextCursor.KeepAnchor)
        self.source_editor.editor.setTextCursor(cursor)

        # center the cursor
        self.source_editor.editor.centerCursor()

        self.source_editor.editor.setFocus()

    @print_exceptions
    def source_byte_offset_to_char_offset(
        self,
        byte_offset: int,
    ) -> int:
        """
        Convert a byte offset from parser.source_bytes
        to a character offset used by QTextCursor.

        tree-sitter / HocrParser:
            UTF-8 byte offsets

        QTextCursor:
            QString character offsets
        """

        source_bytes = self.parser.source_bytes

        # Clamp to valid byte range.
        byte_offset = max(
            0,
            min(
                byte_offset,
                len(source_bytes),
            ),
        )

        # Decode the prefix up to the requested byte offset.
        prefix = source_bytes[:byte_offset].decode(
            self.parser.source_encoding,
            errors="replace",
        )

        return len(prefix)

    @print_exceptions
    def on_word_selected_zzzzzzzzzz(self, word):
        """
        Select the corresponding word text in the HOCR source editor.
        """

        if word is None:
            return

        # The parser uses UTF-8 byte offsets.
        # QTextCursor uses QString character offsets.
        start_pos = self.source_byte_to_qt_position(
            # FIXME AttributeError: 'WordItem' object has no attribute 'byte_range'
            word.byte_range[0]
        )

        end_pos = self.source_byte_to_qt_position(
            word.byte_range[1]
        )

        document = self.hocr_source_editor.editor.document()

        # Defensive bounds checking.
        start_pos = max(
            0,
            min(
                start_pos,
                document.characterCount() - 1,
            ),
        )

        end_pos = max(
            start_pos,
            min(
                end_pos,
                document.characterCount() - 1,
            ),
        )

        cursor = self.hocr_source_editor.editor.textCursor()

        cursor.setPosition(
            start_pos
        )

        cursor.setPosition(
            end_pos,
            QTextCursor.MoveMode.KeepAnchor,
        )

        self.hocr_source_editor.editor.setTextCursor(
            cursor
        )

        self.hocr_source_editor.editor.ensureCursorVisible()

        self.hocr_source_editor.editor.setFocus()

    @print_exceptions
    def on_word_changed(
            self,
            word_id: bytes,
            new_text: Optional[bytes] = None,
            bbox: Optional[Tuple[int, int, int, int]] = None,
            span_start: Optional[int] = None,
        ):
        assert isinstance(word_id, bytes)
        if new_text != None:
            assert isinstance(new_text, bytes)
        """Called when WordItem text changes.

        If the caller provides span_start (byte offset of the span's start tag),
        we call parser.update_by_span() to avoid id collisions. Otherwise fallback
        to the old parser.update(word_id, ...).
        """
        if debug_word_id and debug_word_id == word_id:
            print(f"word {word_id!r}: on_word_changed: new_text={new_text!r}, bbox={bbox}, span_start={span_start}")
        # Prefer span-based update (disambiguates duplicate ids)
        if span_start is not None:
            ok = self.parser.update_by_span(span_start, text=new_text, bbox=bbox)
        else:
            ok = self.parser.update(word_id, text=new_text, bbox=bbox)

        # reflect changed source immediately in code editor + redraw
        self.source_editor.editor.update_from_page()
        # update the word positions
        # TODO incremental update
        # no. RuntimeError: Internal C++ object (WordItem) already deleted.
        # self.changed_word_id = word_id
        self.changed_word_id = bytes(word_id) # force-copy value
        self.refresh_page_view()

    @print_exceptions
    def on_code_cursor_changed(self, pos: int):
        # 1. Find which word covers this pos
        word = self.parser.find_word_at_offset(pos)
        if not word:
            return

        # 2. Get the corresponding WordItem
        items = self.word_items.get(word.id_bytes)
        if not items:
            return

        if len(items) > 1:
            print(f"on_code_cursor_changed: FIXME collision in word id {word.id_bytes!r}")
            for item in items:
                print(f"  item {item}")

        item = items[0]

        # 3. Center page view on that word
        self.page_view.centerOn(item)
        item.setSelected(True)

    @print_exceptions
    def add_new_word_from_page_view(
            self,
            rect: Optional[QRectF] = None,
            word: Optional[Word] = None,
        ):
        assert rect or word
        if rect:
            x0, y0 = int(rect.x()), int(rect.y())
            x1, y1 = int(rect.x() + rect.width()), int(rect.y() + rect.height())
            new_id = b"word_" + get_random_bytestring()
        # FIXME EpubFxlWord
        new_word = word or Word(
            id_bytes=new_id,
            text=b"",
            bbox=(x0, y0, x1, y1),
            x_wconf=None,
            title_value=None,
            byte_range=(0, 0),
            title_value_range=(0, 0),
            id_value_range=(0, 0),
            element_range=(0, 0),
            span_range=(0, 0),
        )
        if word:
            pass
            # FIXME update the range values
            # this is not done in refresh_page_view
            # word.byte_range = (0, 0)
            # word.title_value_range = (0, 0)
            # word.id_bytes_value_range = (0, 0)
            # word.element_range = (0, 0)
            # word.span_range = (0, 0)
        # force update
        # this is needed to actually remove words
        # TODO incremental update
        self.words = self.parser.find_words()
        words = self.words
        lines = group_words_into_lines(words, y_threshold=50)
        line_idx, word_idx = find_insert_line_and_index(new_word.bbox, lines)

        # Determine insertion line number in source
        # TODO avoid splitlines. use word byte positions
        lines_in_source = self.source_editor.editor.toBytes().splitlines()
        word_to_line = {}
        # TODO better
        for idx, line in enumerate(lines_in_source):
            for w in words:
                if w.id_bytes in line:
                    word_to_line[w.id_bytes] = idx

        # TODO create a new line if the word does not fit into existing lines
        # TODO better. the new word should be inserted between old words in the same line
        if lines:
            line_words = lines[line_idx]
            if word_idx == 0:
                # Insert before first word in line
                insert_line = word_to_line.get(line_words[0].id_bytes, len(lines_in_source))
            else:
                # Insert after previous word
                insert_line = word_to_line.get(line_words[word_idx - 1].id_bytes, len(lines_in_source))
                insert_line += 1
        else:
            insert_line = 0

        (x0, y0, x1, y1) = new_word.bbox

        new_span_line = (
            b"      <span class='ocrx_word' id='" + new_word.id_bytes +
            b"' title='bbox " + str(x0).encode("ascii") + b" " + str(y0).encode("ascii") + b" " +
            str(x1).encode("ascii") + b" " + str(y1).encode("ascii") + b"'>" + new_word.text_bytes + b"</span>"
        )

        lines_in_source.insert(insert_line, new_span_line)
        new_source = b"\n".join(lines_in_source)
        self.source_editor.editor.setBytes(new_source)
        self.parser.set_source_bytes(new_source)
        self.refresh_page_view()

        # Place cursor inside new span
        cursor = self.source_editor.editor.textCursor()
        pos = len((
            b"\n".join(lines_in_source[:insert_line]) +
            new_span_line[:-len("</span>")+1]
        ).decode(self.parser.source_encoding, errors="replace"))
        print(f"line 1170: setPosition {pos}")
        cursor.setPosition(pos)
        self.source_editor.editor.setTextCursor(cursor)
        self.source_editor.editor.setFocus()

    def closeEvent(self, event):
        if not self.modified:
            event.accept()
            return
        # ask to save before exit
        reply = QMessageBox(self)
        reply.setWindowTitle(self.tr("Save changes?"))
        reply.setText(self.tr("The document has been modified.\nDo you want to save your changes?"))
        reply.setStandardButtons(
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel
        )
        reply.setDefaultButton(QMessageBox.Save)
        choice = reply.exec()
        if choice == QMessageBox.Save:
            self.save_hocr()
            event.accept()
        elif choice == QMessageBox.Discard:
            event.accept()
        else:  # Cancel
            event.ignore()

    @print_exceptions
    def closeEvent(self, event):
        if not self.modified:
            event.accept()
            return
        # ask to save before exit
        reply = QMessageBox(self)
        reply.setWindowTitle(self.tr("Save changes?"))
        reply.setText(self.tr("The document has been modified.\nDo you want to save your changes?"))
        reply.setStandardButtons(QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        reply.setDefaultButton(QMessageBox.Save)

        git_root = None
        relpath = None
        commit_btn = None

        if shutil.which("git") and self.hocr_file:
            git_root = git_helpers.find_git_root(self.hocr_file)
            if git_root:
                relpath = os.path.relpath(self.hocr_file, git_root)
                if git_helpers.is_file_tracked(git_root, relpath):
                    commit_btn = reply.addButton("Commit", QMessageBox.AcceptRole)

        choice = reply.exec()

        if choice == QMessageBox.Save:
            self.save_hocr()
            event.accept()
        elif choice == QMessageBox.Discard:
            event.accept()
        elif git_root and reply.clickedButton() and reply.clickedButton().text() == "Commit":
            self.save_hocr()
            print(f"committing hocr file {relpath!r} in git repo {git_root!r}")
            git_helpers.git_commit(self.hocr_file, git_root, relpath)
            event.accept()
        else:  # Cancel
            event.ignore()

    @print_exceptions
    def save_hocr(self):
        """Save to original file."""
        if not self.hocr_file:
            self.save_hocr_as()
            return
        try:
            with open(self.hocr_file, "wb") as f:
                f.write(self.parser.source_bytes)
                if not self.parser.source_bytes.endswith(b"\n"):
                    f.write(b"\n")
            # QMessageBox.information(self, "Saved", f"File saved to {self.hocr_file}")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save file:\n{exc}")
            raise

    @print_exceptions
    def save_hocr_as(self):
        """Save to new file via dialog."""
        filename, _ = QFileDialog.getSaveFileName(self, "Save HOCR File", "", "HOCR Files (*.hocr *.html *.xhtml);;All Files (*)")
        if filename:
            self.hocr_file = filename
            self.save_hocr()

    @print_exceptions
    def on_plain_text_edit(
        self,
        position: int,
        chars_removed: int,
        inserted_text: str,
    ):
        span = self.parser.span_at(position)
        if span is None:
            return
        if (
            span.kind == "word"
            and chars_removed > 0
        ):
            self.handle_word_edit(
                position,
                chars_removed,
                inserted_text,
            )


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
        debug and print(f"line {i}: line_y", line_y, "text", repr(" ".join(w.text_bytes.decode("utf8") for w in line)))
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
        debug and print(f"word {i}: word_x", w.bbox[0], "text", repr(w.text_bytes))
        if x0 < w.bbox[0]:
            word_index = i
            break
        word_index = i + 1
    return line_index, word_index


def get_random_bytestring(k=8) -> bytes:
    chars = random.choices(string.ascii_letters + string.digits, k=k)
    return "".join(chars).encode("ascii")


def get_random_word_id() -> bytes:
    chars = random.choices(string.ascii_letters + string.digits, k=8)
    return b"word_" + "".join(chars).encode("ascii")


class HocrEditorApp(QApplication):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.apply_palette()

    def event(self, e):
        # update self.palette() for _is_dark_mode
        handled = super().event(e)
        if e.type() == QEvent.ApplicationPaletteChange:
            # print("System theme changed — new palette detected")
            self.apply_palette()
        return handled

    def apply_palette(self):
        # use high contrast in darkmode on windows
        # workaround for Qt bug:
        # low contrast in darkmode on windows
        # https://forum.qt.io/topic/101391/windows-10-dark-theme/4
        if os.name == "nt":
            self.setStyle(QStyleFactory.create("Fusion"))
            if _is_dark_mode(self):
                # print("apply_palette: dark mode")
                color_helpers.apply_dark_palette(self)
            else:
                # print("apply_palette: light mode")
                color_helpers.apply_light_palette(self)

    def _apply_dark_palette(self):
        self.setStyle(QStyleFactory.create("Fusion"))

        self.setPalette(pal)


def main():
    parser = argparse.ArgumentParser(description="HOCR Editor")
    parser.add_argument("hocr_file", help="Path to HOCR file")
    parser.add_argument(
        "--overlay-color",
        default=None,
        help="Overlay color (color name or #RRGGBB)",
    )
    parser.add_argument(
        "--tesseract-command",
        default="tesseract",
    )
    parser.add_argument(
        "--tessdata-dir",
        default=None,
        help="usually a git clone of https://github.com/tesseract-ocr/tessdata_best",
    )
    args = parser.parse_args()

    # handle Ctrl+C from terminal
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = HocrEditorApp(sys.argv)

    # TODO add translation files
    # pyside6-lupdate hocr-editor.py -ts translations_de.ts
    # # translate translations_de.ts
    # pyside6-lrelease translations_de.ts -qm translations_de.qm
    r"""
    # Load Qt translations for i18n
    translator = QTranslator()
    locale = QLocale.system()
    qt_translations = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    translator.load(locale, "qtbase", "_", qt_translations)
    translator.load("translations_de.qm")
    app.installTranslator(translator)
    """

    editor = HocrEditor(args)

    editor.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
