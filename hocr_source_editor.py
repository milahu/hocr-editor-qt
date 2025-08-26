from PySide6.QtWidgets import (
    QPlainTextEdit,
)
from PySide6.QtGui import (
    QWheelEvent,
    QFont,
    QShortcut,
    QKeySequence,
    QTextCursor,
)
from PySide6.QtCore import (
    Qt,
    QTimer,
)
from hocr_parser import HocrParser, Word


# Operation types
INSERT = 1
REMOVE = 2

# Chunk modes
CHUNK_NORMAL = 0
CHUNK_TYPING = 1
CHUNK_DELETE = 2


class HocrSourceEditor(QPlainTextEdit):
    """Editable HOCR source view with sync callback"""

    TYPING_TIMEOUT_MS = 500
    DELETE_TIMEOUT_MS = 500

    def __init__(self, parser: HocrParser, update_page_cb, parent=None):
        super().__init__(parent)
        self.parser = parser
        self.update_page_cb = update_page_cb  # callback to refresh page view
        self.setPlainText(parser.source)
        self.textChanged.connect(self.on_text_changed)
        self._updating = False  # avoid recursive updates
        self.setUndoRedoEnabled(False)

        # Font / zoom
        self.default_font = QFont("Courier New", 12)  # readable monospace
        self.setFont(self.default_font)
        self.current_font_size = self.default_font.pointSize()

        # keyboard shortcuts
        QShortcut(QKeySequence("Ctrl++"), self, activated=self.zoom_in)
        QShortcut(QKeySequence("Ctrl+="), self, activated=self.zoom_in)  # some keyboards
        QShortcut(QKeySequence("Ctrl+-"), self, activated=self.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self, activated=self.reset_zoom)

        # stacks hold list-of-ops (a chunk)
        self.undo_stack: list[list[tuple]] = []
        self.redo_stack: list[list[tuple]] = []

        # coalescing state
        self._current_typing_chunk: list[tuple] | None = None
        self._typing_timer = QTimer(self)
        self._typing_timer.setSingleShot(True)
        self._typing_timer.timeout.connect(self._commit_typing_chunk)

        self._current_delete_chunk: list[tuple] | None = None
        self._delete_timer = QTimer(self)
        self._delete_timer.setSingleShot(True)
        self._delete_timer.timeout.connect(self._commit_delete_chunk)

    # ---- low-level apply helpers ----
    def _apply_insert(self, pos: int, text: str):
        cur = self.textCursor()
        cur.setPosition(pos)
        cur.insertText(text)

    def _apply_remove(self, pos: int, length: int) -> str:
        cur = self.textCursor()
        cur.setPosition(pos)
        cur.setPosition(pos + length, QTextCursor.KeepAnchor)
        removed = cur.selectedText().replace("\u2029", "\n")
        cur.removeSelectedText()
        return removed

    # ---- commit helpers ----
    def _commit_typing_chunk(self):
        if self._current_typing_chunk:
            self.undo_stack.append(self._current_typing_chunk)
            self.redo_stack.clear()
            self._current_typing_chunk = None

    def _commit_delete_chunk(self):
        if self._current_delete_chunk:
            self.undo_stack.append(self._current_delete_chunk)
            self.redo_stack.clear()
            self._current_delete_chunk = None

    def _commit_all_chunks(self):
        self._commit_typing_chunk()
        self._commit_delete_chunk()

    # ---- stack logic ----
    def _push_chunk(self, ops: list[tuple], mode: int = CHUNK_NORMAL):
        if mode == CHUNK_TYPING:
            self._commit_delete_chunk()  # can’t mix with delete
            if self._current_typing_chunk is None:
                self._current_typing_chunk = []
            self._current_typing_chunk.extend(ops)
            self._typing_timer.start(self.TYPING_TIMEOUT_MS)
        elif mode == CHUNK_DELETE:
            self._commit_typing_chunk()  # can’t mix with typing
            if self._current_delete_chunk is None:
                self._current_delete_chunk = []
            self._current_delete_chunk.extend(ops)
            self._delete_timer.start(self.DELETE_TIMEOUT_MS)
        else:  # CHUNK_NORMAL
            self._commit_all_chunks()
            self.undo_stack.append(ops)
            self.redo_stack.clear()

    def undo_op(self):
        self._commit_all_chunks()
        if not self.undo_stack:
            return
        chunk = self.undo_stack.pop()
        inverse_chunk: list[tuple] = []
        self._updating = True
        try:
            for op_type, pos, text in reversed(chunk):
                if op_type == INSERT:
                    # undo insert → remove
                    self._apply_remove(pos, len(text))
                    inverse_chunk.append((INSERT, pos, text))
                elif op_type == REMOVE:
                    # undo remove → insert
                    self._apply_insert(pos, text)
                    inverse_chunk.append((REMOVE, pos, text))
        finally:
            self._updating = False
        self._sync_parser_and_page()
        self.redo_stack.append(chunk)

    def redo_op(self):
        self._commit_all_chunks()
        if not self.redo_stack:
            return
        chunk = self.redo_stack.pop()
        self._updating = True
        try:
            for op_type, pos, text in chunk:
                if op_type == INSERT:
                    self._apply_insert(pos, text)
                elif op_type == REMOVE:
                    self._apply_remove(pos, len(text))
        finally:
            self._updating = False
        self._sync_parser_and_page()
        self.undo_stack.append(chunk)

    def _sync_parser_and_page(self):
        new_source = self.toPlainText()
        self.parser.set_source(new_source)
        self.update_page_cb()

    # ---- record ops ----
    def _record_replace_selection(self, cur, doc_text) -> list[tuple]:
        if not cur.hasSelection():
            return []
        start, end = sorted([cur.selectionStart(), cur.selectionEnd()])
        removed_text = doc_text[start:end]
        self._apply_remove(start, end - start)
        return [(REMOVE, start, removed_text)]

    # ---- key / paste overrides ----
    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Undo): # Ctrl+Z
            self.undo_op()
            return
        if event.matches(QKeySequence.Redo) or (
            event.key() == Qt.Key_Z and event.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier)
        ) or (
            event.key() == Qt.Key_Y and event.modifiers() == Qt.ControlModifier # Ctrl+Y
        ):
            self.redo_op()
            return

        if self._updating:
            return super().keyPressEvent(event)

        cur = self.textCursor()
        doc_text = self.toPlainText()
        chunk: list[tuple] = []

        key = event.key()
        text = event.text()

        # Cut
        if event.matches(QKeySequence.Cut) and cur.hasSelection():
            chunk.extend(self._record_replace_selection(cur, doc_text))
            self._push_chunk(chunk, mode=CHUNK_DELETE)
            self._sync_parser_and_page()
            return

        # Backspace/Delete
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            if cur.hasSelection():
                # selection delete = one big op, flush immediately
                chunk.extend(self._record_replace_selection(cur, doc_text))
                if chunk:
                    self._push_chunk(chunk, mode=CHUNK_NORMAL)
                    self._sync_parser_and_page()
                return
            else:
                if key == Qt.Key_Backspace and cur.position() > 0:
                    pos = cur.position() - 1
                    removed = doc_text[pos:pos+1]
                    self._apply_remove(pos, 1)
                    chunk.append((REMOVE, pos, removed))
                elif key == Qt.Key_Delete and cur.position() < len(doc_text):
                    pos = cur.position()
                    removed = doc_text[pos:pos+1]
                    self._apply_remove(pos, 1)
                    chunk.append((REMOVE, pos, removed))
            if chunk:
                self._push_chunk(chunk, mode=CHUNK_DELETE)
                self._sync_parser_and_page()
            return

        # Typing characters
        if text and not (event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier)):
            insert_pos = cur.position()
            ops: list[tuple] = []
            ops.extend(self._record_replace_selection(cur, doc_text))
            self._apply_insert(insert_pos, text)
            ops.append((INSERT, insert_pos, text))
            self._push_chunk(ops, mode=CHUNK_TYPING)
            self._sync_parser_and_page()
            return

        super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        if self._updating:
            return super().insertFromMimeData(source)
        cur = self.textCursor()
        doc_text = self.toPlainText()
        pasted = source.text()
        if not pasted:
            return
        chunk: list[tuple] = []
        chunk.extend(self._record_replace_selection(cur, doc_text))
        pos = cur.position()
        self._apply_insert(pos, pasted)
        chunk.append((INSERT, pos, pasted))
        self._push_chunk(chunk)
        self._sync_parser_and_page()

    # ---- sync from page ----
    def on_text_changed(self):
        if self._updating:
            return
        self._updating = True
        try:
            self.parser.set_source(self.toPlainText())
            self.update_page_cb()
        finally:
            self._updating = False

    def update_from_page(self):
        if self._updating:
            return
        self._updating = True
        try:
            self.setPlainText(self.parser.source)
        finally:
            self._updating = False

    # ---- zoom handlers ----
    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

    def zoom_in(self):
        self.current_font_size = min(self.current_font_size + 1, 72)
        self.apply_zoom()

    def zoom_out(self):
        self.current_font_size = max(self.current_font_size - 1, 6)
        self.apply_zoom()

    def reset_zoom(self):
        self.current_font_size = self.default_font.pointSize()
        self.apply_zoom()

    def apply_zoom(self):
        font = QFont(self.default_font)
        font.setPointSize(self.current_font_size)
        self.setFont(font)
