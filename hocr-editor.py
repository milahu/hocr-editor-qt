#!/usr/bin/env python3
"""
Minimal-Diff HOCR Editor
-----------------------

A single-file prototype of an HOCR editor that:
- Loads an hOCR (HTML) file
- Parses it with tree-sitter-html for concrete syntax tree locations
- Renders it in a Qt WebEngine view (so you can reuse existing hOCR JS libs later)
- Lets you click ocrx_word spans, edit their text and bbox/x_wconf
- Applies *minimal-diff* edits by replacing only the exact byte ranges of the
  targeted nodes/attributes in the original source buffer via tree-sitter ranges
- Saves back to disk

Requirements (install via pip):
    pip install PySide6 PySide6-Qt6-Addons PySide6-Qt6-WebEngine tree_sitter tree_sitter_languages

If your environment bundles QtWebEngine in PySide6, you may not need the extra packages
listed above (the exact names vary by platform). On most systems just do:
    pip install PySide6 tree_sitter tree_sitter_languages

Note: tree_sitter_languages ships prebuilt HTML grammar so you don't need to compile.

This is a *prototype* meant to be a solid base for iteration:
- The DOM bridge uses QWebChannel to send element clicks back to Python
- The editor panel shows text, bbox, and x_wconf fields
- Edits are applied using tree-sitter byte ranges to keep the rest of the file untouched
- Rendering is plain HTML for now; you can plug in hocrjs/scribeocr in the injected HTML
  or swap the view for a Qt-native overlay later.

Author: (you)
License: MIT
"""
from __future__ import annotations

import sys
import re
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QObject, Signal, Slot, QUrl
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
    QHBoxLayout,
    QMessageBox,
    QStatusBar,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

# Tree-sitter
from tree_sitter import Parser
# from tree_sitter_languages import get_language # broken
from tree_sitter_language_pack import get_language

HTML_LANGUAGE = get_language("html")


@dataclass
class WordNode:
    id: str
    # byte ranges in the source buffer for minimal diffs
    inner_text_range: Tuple[int, int]
    title_value_range: Tuple[int, int]  # the value part (between quotes)
    full_element_range: Tuple[int, int]  # start->end tag (for safety)

    # parsed values for convenience
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
        # AttributeError: 'tree_sitter.Parser' object has no attribute 'set_language'
        # self.parser = Parser()
        # self.parser.set_language(HTML_LANGUAGE)
        self.parser = Parser(HTML_LANGUAGE)
        self.tree = self.parser.parse(self.source_bytes)

    # --- Traversal helpers -------------------------------------------------
    def _node_text(self, node) -> str:
        b = self.source_bytes[node.start_byte:node.end_byte]
        return b.decode("utf-8", errors="replace")

    def _iter_elements(self, node=None):
        if node is None:
            node = self.tree.root_node
        # HTML grammar uses node.type == 'element' for tags with start+end
        # and 'self_closing_tag' for <br/> etc.
        if node.type == 'element':
            yield node
        for child in node.children:
            yield from self._iter_elements(child)

    def _find_attribute(self, start_tag_node, name: str):
        for c in start_tag_node.children:
            if c.type == 'attribute':
                # children: attribute_name, '=', attribute_value
                if len(c.children) >= 3 and c.children[0].type == 'attribute_name':
                    n = self._node_text(c.children[0]).strip()
                    if n == name:
                        return c
        return None

    def _attribute_value_text_and_range(self, attr_node) -> Tuple[str, Tuple[int, int]]:
        # attr_node children: attribute_name, '=', attribute_value
        if attr_node is None:
            return "", (0, 0)
        val_node = None
        for ch in attr_node.children:
            # print("ch.type", repr(ch.type))
            # if ch.type == 'attribute_value':
            if ch.type == 'quoted_attribute_value':
                val_node = ch
                break
        if val_node is None:
            return "", (attr_node.start_byte, attr_node.start_byte)
        # attribute_value in HTML grammar includes the quotes, e.g. '"ocrx_word"'
        raw = self._node_text(val_node)
        # compute inner range excluding the quotes if present
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
        # element structure: start_tag, (text|element)*, end_tag
        # get start_tag
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
        # class attribute contains ocrx_word
        class_attr = self._find_attribute(start_tag, 'class')
        class_val, _ = self._attribute_value_text_and_range(class_attr)
        # print("class_val", repr(class_val))
        # normalize quotes and whitespace
        cv = class_val.strip().strip('\"\'')
        # print("cv", repr(cv))
        if 'ocrx_word' not in cv.split():
            return None
        # id attribute
        id_attr = self._find_attribute(start_tag, 'id')
        id_val, _ = self._attribute_value_text_and_range(id_attr)
        if not id_val:
            return None
        # title attribute
        title_attr = self._find_attribute(start_tag, 'title')
        title_val, title_range = self._attribute_value_text_and_range(title_attr)
        # inner text: find first text node directly under element
        inner_text = ""
        inner_range = None
        for ch in element_node.children:
            if ch.type == 'text':
                inner_text = self._node_text(ch)
                inner_range = (ch.start_byte, ch.end_byte)
                break
        if inner_range is None:
            # empty or nested span; fallback to zero-length range before end_tag
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
        # Re-parse incrementally by providing an edit to the tree (optional here)
        # For simplicity we reparse the whole document; tree-sitter is fast enough for hOCR sizes.
        self.tree = self.parser.parse(self.source_bytes)


# --- hOCR title parser/formatter -------------------------------------------

TITLE_BBOX_RE = re.compile(r"bbox\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)")
TITLE_XWCONF_RE = re.compile(r"x_wconf\s+(\d+)")


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
    d: Dict[str, str] = {}
    rest: List[str] = []
    for p in parts:
        if p.startswith('bbox'):
            continue
        if p.startswith('x_wconf'):
            continue
        rest.append(p)
    if bbox:
        rest.insert(0, f"bbox {bbox[0]} {bbox[1]} {bbox[2]} {bbox[3]}")
    if xw is not None:
        rest.append(f"x_wconf {xw}")
    return '; '.join(rest)


# --- Qt Bridge objects ------------------------------------------------------

class Bridge(QObject):
    # Emitted when JS reports a clicked word span
    wordClicked = Signal(str, str, str)  # (id, text, title)

    @Slot(str, str, str)
    def onWordClicked(self, el_id: str, text: str, title: str):
        self.wordClicked.emit(el_id, text, title)


INJECTED_JS = r"""
(() => {
  if (window._hocrInjected) return;
  window._hocrInjected = true;

  function ensureChannel(cb){
    if (window.qt && window.qt.webChannelTransport) {
      new QWebChannel(window.qt.webChannelTransport, function(channel){
        cb(channel.objects.bridge);
      });
    } else {
      setTimeout(()=>ensureChannel(cb), 50);
    }
  }

  function setupClicks(bridge){
    document.addEventListener('click', function(ev){
      let el = ev.target;
      while (el && el !== document.body) {
        if (el.classList && el.classList.contains('ocrx_word')) {
          const id = el.getAttribute('id') || '';
          const title = el.getAttribute('title') || '';
          const text = el.textContent || '';
          bridge.onWordClicked(id, text, title);
          ev.preventDefault();
          return false;
        }
        el = el.parentElement;
      }
    }, true);
  }

  ensureChannel(setupClicks);
})();
"""

INJECTED_HTML_HELP = """
<style>
  .ocrx_word { outline-offset: 1px; cursor: pointer; }
  .ocrx_word:hover { outline: 1px dashed; }
</style>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>%(JS)s</script>
""".replace("%(JS)s", INJECTED_JS)


class PropertyPanel(QWidget):
    applyRequested = Signal(dict)  # {text, bbox, x_wconf}

    def __init__(self):
        super().__init__()
        self.current_id: Optional[str] = None
        self._build_ui()

    def _build_ui(self):
        form = QFormLayout()
        self.id_label = QLabel("-")
        self.text_edit = QLineEdit()
        self.bbox_edit = QLineEdit()
        self.conf_edit = QLineEdit()
        self.apply_btn = QPushButton("Apply (Minimal Diff)")
        self.apply_btn.clicked.connect(self._emit_apply)

        form.addRow("ID:", self.id_label)
        form.addRow("Text:", self.text_edit)
        form.addRow("bbox x0 y0 x1 y1:", self.bbox_edit)
        form.addRow("x_wconf:", self.conf_edit)
        form.addRow(self.apply_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addStretch(1)

    def load_word(self, wid: str, text: str, title_value: str):
        self.current_id = wid
        self.id_label.setText(wid)
        self.text_edit.setText(text)
        bbox, xw = parse_title(title_value)
        if bbox:
            self.bbox_edit.setText(" ".join(map(str, bbox)))
        else:
            self.bbox_edit.clear()
        if xw is not None:
            self.conf_edit.setText(str(xw))
        else:
            self.conf_edit.clear()

    def _emit_apply(self):
        if not self.current_id:
            return
        bbox = None
        bbox_text = self.bbox_edit.text().strip()
        if bbox_text:
            nums = bbox_text.split()
            if len(nums) == 4 and all(n.isdigit() for n in nums):
                bbox = tuple(map(int, nums))  # type: ignore
            else:
                QMessageBox.warning(self, "Invalid bbox", "bbox must be 4 integers")
                return
        xw = None
        conf_text = self.conf_edit.text().strip()
        if conf_text:
            if conf_text.isdigit():
                xw = int(conf_text)
            else:
                QMessageBox.warning(self, "Invalid x_wconf", "x_wconf must be an integer")
                return
        self.applyRequested.emit({
            'id': self.current_id,
            'text': self.text_edit.text(),
            'bbox': bbox,
            'x_wconf': xw,
        })


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Minimal-Diff HOCR Editor (PySide6 + Tree-sitter)")
        self.resize(1200, 800)

        self._setup_actions()
        self._setup_ui()

        self.current_path: Optional[str] = None
        self.syntax: Optional[HocrSyntax] = None
        self.word_index: Dict[str, WordNode] = {}

    # --- UI setup ----------------------------------------------------------
    def _setup_actions(self):
        open_act = QtGui.QAction("&Open…", self)
        open_act.triggered.connect(self.open_file)
        save_act = QtGui.QAction("&Save", self)
        save_act.triggered.connect(self.save_file)
        saveas_act = QtGui.QAction("Save &As…", self)
        saveas_act.triggered.connect(self.save_file_as)
        reload_act = QtGui.QAction("&Reload", self)
        reload_act.triggered.connect(self.reload_view)

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

        # Web view
        self.web = QWebEngineView()
        self.split.addWidget(self.web)

        # Right panel
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.panel = PropertyPanel()
        right_layout.addWidget(self.panel)
        self.split.addWidget(right)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 1)

        # WebChannel bridge
        self.channel = QWebChannel()
        self.bridge = Bridge()
        self.channel.registerObject('bridge', self.bridge)
        self.web.page().setWebChannel(self.channel)
        self.bridge.wordClicked.connect(self.on_word_clicked)

        # Respond to panel apply
        self.panel.applyRequested.connect(self.on_apply_edit)

    # --- File ops ----------------------------------------------------------
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

        # Inject helper CSS/JS into a copy for display; keep original buffer for editing
        display_html = self._inject_helpers(self.syntax.source)
        self.web.setHtml(display_html, baseUrl=QUrl.fromLocalFile(path) if path else QUrl("about:blank"))

    def _inject_helpers(self, html: str) -> str:
        # Insert just before </head> if present, else at top
        inj = INJECTED_HTML_HELP
        if '</head>' in html:
            return html.replace('</head>', inj + '</head>')
        else:
            return inj + html

    def save_file(self):
        if not self.syntax:
            return
        if not self.current_path:
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

    def reload_view(self):
        if not self.syntax:
            return
        display_html = self._inject_helpers(self.syntax.source)
        self.web.setHtml(display_html, baseUrl=QUrl.fromLocalFile(self.current_path) if self.current_path else QUrl("about:blank"))

    # --- Interaction -------------------------------------------------------
    def on_word_clicked(self, el_id: str, text: str, title: str):
        self.panel.load_word(el_id, text, title)
        # if the word exists in index, ensure ranges are up-to-date
        if self.syntax:
            self.word_index = self.syntax.index_words()

    def on_apply_edit(self, payload: dict):
        if not self.syntax:
            return
        wid = payload['id']
        if wid not in self.word_index:
            QMessageBox.warning(self, "Not found", f"Word id '{wid}' not found in index")
            return
        node = self.word_index[wid]
        new_text = payload['text']
        # First, update text minimal-diff
        self.syntax.apply_text_change(node, new_text)
        # Re-index after text change to get fresh ranges
        self.word_index = self.syntax.index_words()
        node = self.word_index.get(wid, node)

        # Then, update title with bbox/x_wconf (merge into existing title value)
        # Need the current title value to merge other fields
        title_current_segment = self.syntax.source[node.title_value_range[0]:node.title_value_range[1]]
        new_title_value = format_title(title_current_segment, payload['bbox'], payload['x_wconf'])
        self.syntax.apply_title_change(node, new_title_value)

        # Refresh index and view
        self.word_index = self.syntax.index_words()
        self.reload_view()
        self.status.showMessage(f"Applied minimal-diff edit to '{wid}'")


# --- Main ------------------------------------------------------------------

def main(argv=None):
    app = QApplication(argv or sys.argv)
    win = MainWindow()
    win.show()

    # If a file path is passed as first arg, open it
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
