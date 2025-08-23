#!/usr/bin/env python3
"""
Minimal-Diff HOCR Editor — Native Qt Overlay
-------------------------------------------

This version replaces the WebEngine preview with a **Qt-native** renderer using
`QGraphicsView/QGraphicsScene` and custom items. Each word is rendered as a
rectangular box with its text; you can:

- **Drag** the *top-left* handle to move the word box
- **Resize** using the *bottom-right* handle
- **Double-click** the word text to edit it *in place*

Edits are applied to the underlying hOCR HTML using **tree-sitter** with
*minimal-diff* byte-range replacements (word text and the `bbox` inside the
`title` attribute). The file can be saved back to disk.

Install:
    pip install PySide6 tree_sitter tree_sitter_languages

Run:
    python hocr_editor_qt_native.py path/to/file.hocr.html

Notes:
- If the page image path is present in the `ocr_page`'s `title` as
  `image "..."`, we load it as a background. If missing/unavailable, we still
  show word boxes against a white canvas sized to the page bbox.
- This is a compact prototype aimed at clarity and hackability.
"""
from __future__ import annotations

import sys
import re
import os
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QObject, Signal, Slot, QRectF, QPointF
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QFileDialog,
    QWidget,
    QSplitter,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QLabel,
    QMessageBox,
    QStatusBar,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsObject,
    QGraphicsTextItem,
)

# Tree-sitter
from tree_sitter import Parser
# from tree_sitter_languages import get_language # broken
from tree_sitter_language_pack import get_language

HTML_LANGUAGE = get_language("html")


# --- hOCR parsing helpers ---------------------------------------------------
TITLE_BBOX_RE = re.compile(r"bbox\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)")
TITLE_XWCONF_RE = re.compile(r"x_wconf\s+(\d+)")
TITLE_IMAGE_RE = re.compile(r"image\s+[\"']([^\"']+)[\"']")


def parse_title(title_value: str) -> Tuple[Optional[Tuple[int, int, int, int]], Optional[int]]:
    bbox = None
    m = TITLE_BBOX_RE.search(title_value)
    if m:
        bbox = tuple(map(int, m.groups()))  # type: ignore
    xw = None
    m2 = TITLE_XWCONF_RE.search(title_value)
    if m2:
        xw = int(m2.group(1))
    return bbox, xw


def format_title(existing: str, bbox: Optional[Tuple[int, int, int, int]], xw: Optional[int]) -> str:
    # Keep any other semicolon-separated fields intact; replace/insert bbox and x_wconf
    parts = [p.strip() for p in existing.split(';') if p.strip()]
    rest: List[str] = []
    for p in parts:
        if p.startswith('bbox') or p.startswith('x_wconf'):
            continue
        rest.append(p)
    if bbox:
        rest.insert(0, f"bbox {bbox[0]} {bbox[1]} {bbox[2]} {bbox[3]}")
    if xw is not None:
        rest.append(f"x_wconf {xw}")
    return '; '.join(rest)


@dataclass
class WordNode:
    id: str
    inner_text_range: Tuple[int, int]
    title_value_range: Tuple[int, int]
    full_element_range: Tuple[int, int]
    text: str
    bbox: Optional[Tuple[int, int, int, int]]
    x_wconf: Optional[int]


class HocrSyntax:
    """HTML CST helper around tree-sitter for finding and editing ocrx_word spans."""

    def __init__(self, source_html: str):
        self.set_source(source_html)

    def set_source(self, source_html: str):
        self.source = source_html
        self.source_bytes = source_html.encode("utf-8")
        # self.parser = Parser()
        # self.parser.set_language(HTML_LANGUAGE)
        self.parser = Parser(HTML_LANGUAGE)
        self.tree = self.parser.parse(self.source_bytes)

    def _node_text(self, node) -> str:
        b = self.source_bytes[node.start_byte:node.end_byte]
        return b.decode("utf-8", errors="replace")

    def _iter_elements(self, node=None):
        if node is None:
            node = self.tree.root_node
        if node.type == 'element':
            yield node
        for child in node.children:
            yield from self._iter_elements(child)

    def _find_attribute(self, start_tag_node, name: str):
        for c in start_tag_node.children:
            if c.type == 'attribute':
                if len(c.children) >= 3 and c.children[0].type == 'attribute_name':
                    n = self._node_text(c.children[0]).strip()
                    if n == name:
                        return c
        return None

    def _attribute_value_text_and_range(self, attr_node) -> Tuple[str, Tuple[int, int]]:
        if attr_node is None:
            return "", (0, 0)
        val_node = None
        for ch in attr_node.children:
            # Some grammars use 'quoted_attribute_value'
            if ch.type in ('attribute_value', 'quoted_attribute_value'):
                val_node = ch
                break
        if val_node is None:
            return "", (attr_node.start_byte, attr_node.start_byte)
        raw = self._node_text(val_node)
        # Strip quotes if present and compute inner byte-range
        if raw.startswith(('"', "'")) and raw.endswith(('"', "'")) and len(raw) >= 2:
            inner_start = val_node.start_byte + 1
            inner_end = val_node.end_byte - 1
            text = raw[1:-1]
        else:
            inner_start = val_node.start_byte
            inner_end = val_node.end_byte
            text = raw
        return text, (inner_start, inner_end)

    def _collect_span_info(self, element_node) -> Optional[WordNode]:
        if not element_node.children:
            return None
        start_tag = element_node.children[0]
        if start_tag.type != 'start_tag':
            return None
        # tag name
        tag_name = None
        for ch in start_tag.children:
            if ch.type == 'tag_name':
                tag_name = self._node_text(ch)
                break
        if tag_name != 'span':
            return None
        # class contains ocrx_word
        class_attr = self._find_attribute(start_tag, 'class')
        class_val, _ = self._attribute_value_text_and_range(class_attr)
        cv = class_val.strip().strip('\"\'')
        if 'ocrx_word' not in cv.split():
            return None
        # id
        id_attr = self._find_attribute(start_tag, 'id')
        id_val, _ = self._attribute_value_text_and_range(id_attr)
        if not id_val:
            return None
        # title
        title_attr = self._find_attribute(start_tag, 'title')
        title_val, title_range = self._attribute_value_text_and_range(title_attr)
        # inner text (first text node)
        inner_text = ""
        inner_range = None
        for ch in element_node.children:
            if ch.type == 'text':
                inner_text = self._node_text(ch)
                inner_range = (ch.start_byte, ch.end_byte)
                break
        if inner_range is None:
            end_tag = element_node.children[-1]
            inner_range = (end_tag.start_byte, end_tag.start_byte)
        bbox, xw = parse_title(title_val)
        return WordNode(
            id=id_val,
            inner_text_range=inner_range,
            title_value_range=title_range,
            full_element_range=(element_node.start_byte, element_node.end_byte),
            text=inner_text,
            bbox=bbox,
            x_wconf=xw,
        )

    def index_words(self) -> Dict[str, WordNode]:
        words: Dict[str, WordNode] = {}
        for el in self._iter_elements():
            info = self._collect_span_info(el)
            if info:
                words[info.id] = info
        return words

    def find_page_image_and_bbox(self) -> Tuple[Optional[str], Optional[Tuple[int, int, int, int]]]:
        # Find first div.ocr_page and parse its title
        root = self.tree.root_node
        for el in self._iter_elements(root):
            # ensure it's a div with class 'ocr_page'
            start_tag = el.children[0] if el.children else None
            if not start_tag or start_tag.type != 'start_tag':
                continue
            tag_name = None
            for ch in start_tag.children:
                if ch.type == 'tag_name':
                    tag_name = self._node_text(ch)
                    break
            if tag_name != 'div':
                continue
            class_attr = self._find_attribute(start_tag, 'class')
            class_val, _ = self._attribute_value_text_and_range(class_attr)
            cv = class_val.strip().strip('\"\'')
            if 'ocr_page' not in cv.split():
                continue
            title_attr = self._find_attribute(start_tag, 'title')
            title_val, _ = self._attribute_value_text_and_range(title_attr)
            img = None
            m = TITLE_IMAGE_RE.search(title_val)
            if m:
                img = m.group(1)
            bbox, _ = parse_title(title_val)
            return img, bbox
        return None, None

    # --- Minimal-diff edit operations --------------------------------------
    def apply_text_change(self, node: WordNode, new_text: str):
        self._replace_range(node.inner_text_range, new_text)

    def apply_title_change(self, node: WordNode, new_title_value: str):
        self._replace_range(node.title_value_range, new_title_value)

    def _replace_range(self, byte_range: Tuple[int, int], new_content_utf8: str):
        start, end = byte_range
        before = self.source_bytes[:start]
        after = self.source_bytes[end:]
        insert = new_content_utf8.encode('utf-8')
        self.source_bytes = before + insert + after
        self.source = self.source_bytes.decode('utf-8', errors='replace')
        self.tree = self.parser.parse(self.source_bytes)


# --- Graphics Items ---------------------------------------------------------
class EditableTextItem(QGraphicsTextItem):
    commit = Signal(str)  # emits new text when editing finishes

    def focusOutEvent(self, event: QtGui.QFocusEvent) -> None:
        super().focusOutEvent(event)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.commit.emit(self.toPlainText())

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)


class WordItem(QGraphicsObject):
    requestCommit = Signal(str, str, QtCore.QRectF)  # id, text, rect

    HANDLE = 8.0

    def __init__(self, wid: str, rect: QRectF, text: str, parent=None):
        super().__init__(parent)
        self.wid = wid
        self._rect = QRectF(rect)
        self._drag_mode = None  # 'move-tl' or 'resize-br'
        self.setFlags(
            QGraphicsObject.ItemIsSelectable |
            QGraphicsObject.ItemSendsGeometryChanges
        )
        # text child
        self.text_item = EditableTextItem(text, self)
        self.text_item.setDefaultTextColor(Qt.black)
        self.text_item.setTextInteractionFlags(Qt.NoTextInteraction)
        self.text_item.commit.connect(self._on_text_commit)
        self._layout_text()

    # geometry
    def boundingRect(self) -> QRectF:
        pad = 2
        return self._rect.adjusted(-pad, -pad, pad, pad)

    def shape(self) -> QtGui.QPainterPath:
        p = QtGui.QPainterPath()
        p.addRect(self._rect)
        # Expand clickable area for handles
        tl = self._top_left_handle_rect()
        br = self._bottom_right_handle_rect()
        p.addRect(tl)
        p.addRect(br)
        return p

    def paint(self, painter: QtGui.QPainter, option, widget=None):
        pen = QtGui.QPen(Qt.red)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(self._rect)
        painter.fillRect(self._top_left_handle_rect(), Qt.red)
        painter.fillRect(self._bottom_right_handle_rect(), Qt.red)

    def _top_left_handle_rect(self) -> QRectF:
        return QRectF(self._rect.topLeft(), QtCore.QSizeF(self.HANDLE, self.HANDLE))

    def _bottom_right_handle_rect(self) -> QRectF:
        br = self._rect.bottomRight() - QPointF(self.HANDLE, self.HANDLE)
        return QRectF(br, QtCore.QSizeF(self.HANDLE, self.HANDLE))

    def _layout_text(self):
        margin = 2
        self.text_item.setPos(self._rect.left() + margin, self._rect.top() + margin)
        # constrain text width to box width
        self.text_item.setTextWidth(max(1.0, self._rect.width() - 2*margin))

    # interactions
    def mouseDoubleClickEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if self._rect.contains(event.pos()):
            self.text_item.setTextInteractionFlags(Qt.TextEditorInteraction)
            self.text_item.setFocus(Qt.MouseFocusReason)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        pos = event.pos()
        if self._top_left_handle_rect().contains(pos):
            self._drag_mode = 'move-tl'
            self._press_offset = pos - self._rect.topLeft()
            event.accept()
            return
        if self._bottom_right_handle_rect().contains(pos):
            self._drag_mode = 'resize-br'
            self._press_offset = self._rect.bottomRight() - pos
            event.accept()
            return
        self._drag_mode = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if not self._drag_mode:
            super().mouseMoveEvent(event)
            return
        pos = event.pos()
        r = QRectF(self._rect)
        if self._drag_mode == 'move-tl':
            new_tl = pos - self._press_offset
            # keep bottom-right fixed
            br = r.bottomRight()
            r.setTopLeft(new_tl)
            # enforce min size
            if r.width() < 5:
                r.setLeft(br.x() - 5)
            if r.height() < 5:
                r.setTop(br.y() - 5)
        elif self._drag_mode == 'resize-br':
            new_br = pos + self._press_offset
            r.setBottomRight(new_br)
            if r.width() < 5:
                r.setRight(r.left() + 5)
            if r.height() < 5:
                r.setBottom(r.top() + 5)
        if r != self._rect:
            self.prepareGeometryChange()
            self._rect = r
            self._layout_text()
        event.accept()

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if self._drag_mode:
            self._drag_mode = None
            self.requestCommit.emit(self.wid, self.text_item.toPlainText(), self._rect)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _on_text_commit(self, new_text: str):
        self.requestCommit.emit(self.wid, new_text, self._rect)

    # helpers
    def setRectAndText(self, rect: QRectF, text: str):
        self.prepareGeometryChange()
        self._rect = QRectF(rect)
        self.text_item.setPlainText(text)
        self._layout_text()


# --- Scene/View -------------------------------------------------------------
class PageScene(QGraphicsScene):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.background_pix: Optional[QtGui.QPixmap] = None

    def drawBackground(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        painter.fillRect(rect, Qt.white)
        if self.background_pix:
            painter.drawPixmap(0, 0, self.background_pix)


class PageView(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if event.modifiers() & Qt.ControlModifier:
            angle = event.angleDelta().y()
            factor = 1.0015 ** angle
            self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)


# --- Property Panel (kept for IDs/debug) -----------------------------------
class PropertyPanel(QWidget):
    applyRequested = Signal(dict)

    def __init__(self):
        super().__init__()
        form = QFormLayout(self)
        self.id_label = QLabel('-')
        self.text_edit = QLineEdit()
        self.bbox_edit = QLineEdit()
        self.conf_edit = QLineEdit()
        self.apply_btn = QPushButton("Apply (Minimal Diff)")
        self.apply_btn.clicked.connect(self._emit)
        form.addRow("ID:", self.id_label)
        form.addRow("Text:", self.text_edit)
        form.addRow("bbox x0 y0 x1 y1:", self.bbox_edit)
        form.addRow("x_wconf:", self.conf_edit)
        form.addRow(self.apply_btn)

    def load_word(self, wid: str, text: str, bbox: Optional[Tuple[int,int,int,int]], xw: Optional[int]):
        self.id_label.setText(wid)
        self.text_edit.setText(text)
        self.bbox_edit.setText("" if not bbox else " ".join(map(str, bbox)))
        self.conf_edit.setText("" if xw is None else str(xw))

    def _emit(self):
        wid = self.id_label.text()
        if not wid or wid == '-':
            return
        bbox = None
        if self.bbox_edit.text().strip():
            nums = self.bbox_edit.text().split()
            if len(nums) == 4 and all(n.isdigit() for n in nums):
                bbox = tuple(map(int, nums))  # type: ignore
            else:
                QMessageBox.warning(self, "Invalid bbox", "bbox must be 4 integers")
                return
        xw = None
        if self.conf_edit.text().strip():
            if self.conf_edit.text().isdigit():
                xw = int(self.conf_edit.text())
            else:
                QMessageBox.warning(self, "Invalid x_wconf", "x_wconf must be an integer")
                return
        self.applyRequested.emit({
            'id': wid,
            'text': self.text_edit.text(),
            'bbox': bbox,
            'x_wconf': xw,
        })


# --- Main Window ------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Minimal-Diff HOCR Editor — Native Qt")
        self.resize(1300, 850)
        self._setup_actions()
        self._setup_ui()
        self.current_path: Optional[str] = None
        self.syntax: Optional[HocrSyntax] = None
        self.word_index: Dict[str, WordNode] = {}
        self.items_by_id: Dict[str, WordItem] = {}

    def _setup_actions(self):
        open_act = QtGui.QAction("&Open…", self)
        open_act.triggered.connect(self.open_file)
        save_act = QtGui.QAction("&Save", self)
        save_act.triggered.connect(self.save_file)
        saveas_act = QtGui.QAction("Save &As…", self)
        saveas_act.triggered.connect(self.save_file_as)
        reload_act = QtGui.QAction("&Reload", self)
        reload_act.triggered.connect(self.reload_scene_from_model)
        m = self.menuBar().addMenu("File")
        m.addAction(open_act)
        m.addAction(save_act)
        m.addAction(saveas_act)
        m.addSeparator()
        m.addAction(reload_act)
        self.status = QStatusBar()
        self.setStatusBar(self.status)

    def _setup_ui(self):
        self.split = QSplitter()
        self.setCentralWidget(self.split)
        # Scene/View
        self.scene = PageScene()
        self.view = PageView(self.scene)
        self.split.addWidget(self.view)
        # Right panel
        self.panel = PropertyPanel()
        self.panel.applyRequested.connect(self.on_apply_from_panel)
        right_wrap = QWidget()
        lay = QVBoxLayout(right_wrap)
        lay.addWidget(self.panel)
        lay.addStretch(1)
        self.split.addWidget(right_wrap)
        self.split.setStretchFactor(0, 4)
        self.split.setStretchFactor(1, 1)

    # --- File I/O -----------------------------------------------------------
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open hOCR HTML", filter="HTML Files (*.html *.htm)")
        if not path:
            return
        with open(path, 'r', encoding='utf-8') as f:
            html = f.read()
        self.load_html(path, html)

    def load_html(self, path: Optional[str], html: str):
        self.current_path = path
        self.syntax = HocrSyntax(html)
        self.word_index = self.syntax.index_words()
        self.status.showMessage(f"Loaded {len(self.word_index)} ocrx_word spans")
        self._build_scene()

    def save_file(self):
        if not (self.syntax and self.current_path):
            return self.save_file_as()
        with open(self.current_path, 'w', encoding='utf-8') as f:
            f.write(self.syntax.source)
        self.status.showMessage(f"Saved to {self.current_path}")

    def save_file_as(self):
        if not self.syntax:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save hOCR HTML As", filter="HTML Files (*.html *.htm)")
        if not path:
            return
        self.current_path = path
        self.save_file()

    # --- Scene construction -------------------------------------------------
    def _build_scene(self):
        self.scene.clear()
        self.items_by_id.clear()
        if not self.syntax:
            return
        # background image & page bbox
        img_rel, page_bbox = self.syntax.find_page_image_and_bbox()
        page_w = page_h = None
        if page_bbox:
            x0, y0, x1, y1 = page_bbox
            page_w = x1 - x0
            page_h = y1 - y0
        # try to load image
        pix = None
        if img_rel and self.current_path:
            base_dir = os.path.dirname(self.current_path)
            img_path = os.path.normpath(os.path.join(base_dir, img_rel))
            if os.path.exists(img_path):
                pix = QtGui.QPixmap(img_path)
        if pix and not pix.isNull():
            self.scene.background_pix = pix
            self.scene.setSceneRect(0, 0, pix.width(), pix.height())
        else:
            self.scene.background_pix = None
            if page_w and page_h:
                self.scene.setSceneRect(0, 0, page_w, page_h)
            else:
                self.scene.setSceneRect(0, 0, 1600, 2400)
        # add word items
        for wid, node in self.word_index.items():
            if not node.bbox:
                continue
            x0, y0, x1, y1 = node.bbox
            rect = QRectF(x0, y0, x1 - x0, y1 - y0)
            item = WordItem(wid, rect, node.text)
            item.requestCommit.connect(self.on_item_commit)
            self.scene.addItem(item)
            self.items_by_id[wid] = item
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def reload_scene_from_model(self):
        if not self.syntax:
            return
        self.word_index = self.syntax.index_words()
        # Update existing items where possible; add/remove as needed
        existing = set(self.items_by_id.keys())
        current = set(self.word_index.keys())
        # remove
        for wid in existing - current:
            it = self.items_by_id.pop(wid)
            self.scene.removeItem(it)
        # update & add
        for wid in current:
            node = self.word_index[wid]
            if not node.bbox:
                continue
            rect = QRectF(node.bbox[0], node.bbox[1], node.bbox[2]-node.bbox[0], node.bbox[3]-node.bbox[1])
            if wid in self.items_by_id:
                self.items_by_id[wid].setRectAndText(rect, node.text)
            else:
                it = WordItem(wid, rect, node.text)
                it.requestCommit.connect(self.on_item_commit)
                self.scene.addItem(it)
                self.items_by_id[wid] = it
        self.status.showMessage(f"Scene refreshed: {len(self.items_by_id)} items")

    # --- Apply edits --------------------------------------------------------
    def on_item_commit(self, wid: str, new_text: str, rect: QRectF):
        if not self.syntax:
            return
        node = self.word_index.get(wid)
        if not node:
            return
        # 1) Minimal-diff update for inner text
        if new_text != node.text:
            self.syntax.apply_text_change(node, new_text)
            self.word_index = self.syntax.index_words()
            node = self.word_index.get(wid, node)
        # 2) Minimal-diff update for bbox in title
        title_current = self.syntax.source[node.title_value_range[0]:node.title_value_range[1]]
        new_bbox = (int(rect.left()), int(rect.top()), int(rect.right()), int(rect.bottom()))
        # keep x_wconf
        _, xw = parse_title(title_current)
        new_title_value = format_title(title_current, new_bbox, xw)
        self.syntax.apply_title_change(node, new_title_value)
        # reindex and update panel display
        self.word_index = self.syntax.index_words()
        node2 = self.word_index.get(wid)
        if node2:
            self.panel.load_word(wid, node2.text, node2.bbox, node2.x_wconf)
        self.status.showMessage(f"Applied minimal-diff edit to '{wid}'")

    def on_apply_from_panel(self, payload: dict):
        if not self.syntax:
            return
        wid = payload['id']
        node = self.word_index.get(wid)
        if not node:
            QMessageBox.warning(self, "Not found", f"Word id '{wid}' not found")
            return
        # text
        self.syntax.apply_text_change(node, payload['text'])
        self.word_index = self.syntax.index_words()
        node = self.word_index.get(wid, node)
        # title
        title_current = self.syntax.source[node.title_value_range[0]:node.title_value_range[1]]
        new_title = format_title(title_current, payload['bbox'] or node.bbox, payload['x_wconf'] if payload['x_wconf'] is not None else node.x_wconf)
        self.syntax.apply_title_change(node, new_title)
        self.word_index = self.syntax.index_words()
        # reflect on scene
        node2 = self.word_index.get(wid)
        if node2 and node2.bbox:
            rect = QRectF(node2.bbox[0], node2.bbox[1], node2.bbox[2]-node2.bbox[0], node2.bbox[3]-node2.bbox[1])
            self.items_by_id[wid].setRectAndText(rect, node2.text)
        self.status.showMessage(f"Applied changes to '{wid}' via panel")


# --- Main ------------------------------------------------------------------

def main(argv=None):
    app = QApplication(argv or sys.argv)
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1:
        try:
            with open(sys.argv[1], 'r', encoding='utf-8') as f:
                html = f.read()
            win.load_html(sys.argv[1], html)
        except Exception as e:
            QMessageBox.critical(win, "Error", str(e))
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
