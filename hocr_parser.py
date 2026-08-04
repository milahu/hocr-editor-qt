"""
HOCR Parser (HTML + XHTML) with Minimal-Diff Updates
====================================================

This module parses hOCR in either HTML or XHTML form using tree-sitter
(`tree-sitter-html` or `tree-sitter-xml`) and provides **minimal-diff** update
operations for word text, bbox, x_wconf, and id. It returns precise byte ranges
so only the changed spans are rewritten.

Requirements (pip):
    pip install tree_sitter tree_sitter_language_pack

Usage:
    from hocr_parser import HocrParser
    src = Path("doc.hocr.html").read()
    hp = HocrParser(src)
    words = hp.find_words()  # list[Word]
    hp.update(word_id=words[0].id_bytes, text_bytes=b"NEW")
    hp.update(word_id=words[0].id_bytes, bbox=(10,20,100,60), x_wconf=95)
    new_src = hp.source_bytes  # updated HTML/XML bytestring

Notes:
- Robust to both grammars' node/field name differences.
- For HTML, accepts attribute_value variants: 'quoted_attribute_value',
  'attribute_value', 'unquoted_attribute_value'.
- For XHTML/XML, reads 'AttValue'.
- Class matching checks tokens (so 'ocrx_word other' works).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import (
    field,
)
from typing import Dict, Iterable, List, Optional, Tuple
from typing import (
    Any,
    Literal,
)
import re
import traceback

from tree_sitter import Parser
from tree_sitter_language_pack import get_language

debug = False
# debug = True

debug_word_id = None
# debug_word_id = b"word_1_15"

HTML_LANG = get_language("html")
XML_LANG = get_language("xml")

# ------------------------ utilities ------------------------


_TITLE_BBOX_RE = re.compile(rb"bbox\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)", re.IGNORECASE)
_TITLE_XWCONF_RE = re.compile(rb"x_wconf\s+(-?\d+)", re.IGNORECASE)


# dont let qt swallow python exceptions
def print_exceptions(func):
    def print_exceptions_wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            def filter_frame(frame):
                remove_frame_suffix_list = [
                    "Traceback (most recent call last):\n",
                    # remove wrapper frames
                    ", in print_exceptions_wrapper\n    return func(*args, **kwargs)\n",
                    ", in print_exceptions_wrapper\n    stack = traceback.format_stack()\n",
                    # remove main frames
                    ", in main\n    sys.exit(app.exec())\n",
                    ", in <module>\n    main()\n",
                ]
                for frame_suffix in remove_frame_suffix_list:
                    if frame.endswith(frame_suffix):
                        return False
                return True
            print("Traceback (most recent call last):")
            # Incoming Python stack (where this call came from)
            stack = traceback.format_stack()
            if 0:
                # debug: print all frames
                for frame in stack:
                    print("frame", repr(frame))
            stack = filter(filter_frame, stack)
            print("".join(stack), end="")
            # Exception traceback with locals
            capture_locals = False
            # capture_locals = True # also print function parameters. noisy but maybe helpful
            stack = traceback.TracebackException.from_exception(exc, capture_locals=capture_locals).format()
            stack = filter(filter_frame, stack)
            print("".join(stack), end="")
    return print_exceptions_wrapper


def _parse_title(title_value: bytes):
    """Return (bbox_tuple_or_None, x_wconf_or_None) from the raw 'title' value (no quotes)."""
    s = (title_value or b"").strip()
    bbox = None
    xw = None

    m = _TITLE_BBOX_RE.search(s)
    if m:
        try:
            bbox = tuple(map(int, m.groups()))
        except Exception:
            bbox = None

    m2 = _TITLE_XWCONF_RE.search(s)
    if m2:
        try:
            xw = int(m2.group(1))
        except Exception:
            xw = None

    # Fallback: token scan if regex failed
    if bbox is None and b"bbox" in s.lower():
        try:
            parts = re.split(rb"[;\s]+", s)
            for i, p in enumerate(parts):
                if p.lower() == b"bbox" and i + 4 < len(parts):
                    bx = tuple(map(int, parts[i+1:i+5]))
                    if len(bx) == 4:
                        bbox = bx
                        break
        except Exception:
            pass

    return bbox, xw


@print_exceptions
def _format_title(
        existing: bytes,
        **kwargs
    ) -> bytes:
    """Merge new title values (bbox, ...) into an existing semicolon-separated title value.
    Preserves unknown fields and order.
    """
    assert isinstance(existing, bytes)
    # print("_format_title existing", repr(existing))
    existing = existing or b""
    # title_items = [] # preserve duplicate keys
    title_dict = {}
    for part in existing.split(b";"):
        key_val = re.split(rb"\s+", part.strip(), 1)
        if len(key_val) == 1:
            if key_val[0] == b"": continue # both key and val are empty
            key_val.append(b"")
        key, val = key_val
        # title_items.append((key, val))
        title_dict[key] = val
    # import json
    # print("_format_title title_dict", json.dumps(title_dict, indent=2))
    # print("_format_title kwargs", json.dumps(kwargs, indent=2))
    @print_exceptions
    def encode_val(val):
        if isinstance(val, bytes):
            return val
        return str(val).encode("utf8")
    for key, val in kwargs.items():
        key = encode_val(key)
        if isinstance(val, (list, tuple)):
            val = b" ".join(map(encode_val, val))
        val = encode_val(val)
        title_dict[key] = val
    new_title = b"; ".join([(key + b" " + val) for key, val in title_dict.items()])
    if debug:
        print(f"_format_title: {existing!r} -> {new_title!r}")
    return new_title


@dataclass
class Word:
    id_bytes: bytes
    text_bytes: bytes
    bbox: Optional[Tuple[int, int, int, int]]
    x_wconf: Optional[int]
    # raw title value (without surrounding quotes)
    title_value: Optional[bytes]
    # precise byte ranges (start_byte, end_byte) in source_bytes
    byte_range: Tuple[int, int]
    title_value_range: Tuple[int, int]
    id_value_range: Tuple[int, int]
    element_byte_range: Tuple[int, int]
    span_range: Tuple[int, int]
    # @print_exceptions
    # def __init__(self, *a, **k):
    #     super().__init__(*a, **k)
    #     assert isinstance(self.id_bytes, bytes)
    #     assert isinstance(self.text_bytes, bytes)
    #     assert isinstance(self.title_value, bytes)


@dataclass
class ProjectionSegment:
    """
    Maps a range in the plain-text projection to the HOCR source.

    plain_start_char/plain_end_char are Python string character offsets.

    source_start_byte/source_end_byte are byte offsets into the original UTF-8
    source bytes.

    word_id is set for text belonging to an ocrx_word.
    """
    # char offsets
    plain_start_char: int
    plain_end_char: int

    # byte offsets
    source_start_byte: int
    source_end_byte: int

    kind: Literal[
        "word",
        "space",
        "line_break",
        "paragraph_break",
    ]

    word_id: Optional[str] = None


@dataclass
class PlainTextProjection:
    """
    A rendered plain-text representation of the HOCR document.
    """
    text_str: str
    segments: list[ProjectionSegment]

    @print_exceptions
    def segment_at(self, char_position: int) -> Optional[ProjectionSegment]:
        for segment in self.segments:
            if segment.plain_start_char <= char_position < segment.plain_end_char:
                return segment
        return None

    @print_exceptions
    def word_segments(self) -> list[ProjectionSegment]:
        return [
            segment
            for segment in self.segments
            if segment.kind == "word"
        ]


def build_plain_text_projection(self) -> PlainTextProjection:
    # FIXME use io.StringIO
    str_parts = []
    segments = []

    plain_char_pos = 0

    paragraphs = self.find_paragraphs()

    for paragraph_index, paragraph in enumerate(paragraphs):

        for line_index, line in enumerate(paragraph.lines):

            for word_index, word in enumerate(line.words):

                if word_index > 0:
                    str_parts.append(" ")

                    segments.append(
                        ProjectionSegment(
                            plain_start_char=plain_char_pos,
                            plain_end_char=plain_char_pos + 1,
                            source_start_byte=word.byte_range[0],
                            source_end_byte=word.byte_range[0],
                            kind="space",
                        )
                    )

                    plain_char_pos += 1

                text_bytes = word.text_bytes
                text_str = text_bytes.decode(self.source_encoding)

                # str_parts.append(text_bytes)
                str_parts.append(text_str)

                segments.append(
                    ProjectionSegment(
                        plain_start_char=plain_char_pos,
                        # plain_end_char=plain_char_pos + len(text_bytes),
                        plain_end_char=plain_char_pos + len(text_str),
                        source_start_byte=word.byte_range[0],
                        source_end_byte=word.byte_range[1],
                        kind="word",
                        word_id=word.id_bytes,
                    )
                )

                # plain_char_pos += len(text_bytes)
                plain_char_pos += len(text_str)

            if line_index < len(paragraph.lines) - 1:
                str_parts.append("\n")

                segments.append(
                    ProjectionSegment(
                        plain_start_char=plain_char_pos,
                        plain_end_char=plain_char_pos + 1,
                        source_start_byte=0,
                        source_end_byte=0,
                        kind="line_break",
                    )
                )

                plain_char_pos += 1

        if paragraph_index < len(paragraphs) - 1:
            str_parts.append("\n\n")

            segments.append(
                ProjectionSegment(
                    plain_start_char=plain_char_pos,
                    plain_end_char=plain_char_pos + 2,
                    source_start_byte=0,
                    source_end_byte=0,
                    kind="paragraph_break",
                )
            )

            plain_char_pos += 2

    return PlainTextProjection(
        text_str="".join(str_parts),
        segments=segments,
    )


def word_at_plain_position(
    self,
    projection: PlainTextProjection,
    char_position: int,
) -> Optional[Word]:

    r"""
    find the Word corresponding to a plain-text position

    Eventually, we should optimize this with a dictionary:

    words_by_id = {
        word.id_bytes: word
        for word in self.find_words()
    }
    """

    segment = projection.segment_at(char_position)

    if segment is None:
        return None

    if segment.kind != "word":
        return None

    for word in self.find_words():
        if word.id_bytes == segment.word_id:
            return word

    return None


r'''
@dataclass
class HocrWord:
    id_bytes: bytes
    text_bytes: bytes
    bbox: tuple[int, int, int, int] | None
    x_wconf: int | None

    # byte range of the complete word element
    element_byte_range: tuple[int, int]

    # byte range of the word text
    byte_range: tuple[int, int]
'''


@dataclass
class HocrLine:
    id_bytes: bytes
    words: list[Word] = field(default_factory=list)

    # byte range of the complete line element
    element_byte_range: tuple[int, int] = (0, 0)


@dataclass
class HocrParagraph:
    id_bytes: bytes
    lines: list[HocrLine] = field(default_factory=list)

    # byte range of the complete paragraph element
    element_byte_range: tuple[int, int] = (0, 0)


@dataclass
class TextSpan:
    """
    One logical region of the plain-text representation.

    Example:

        hello world

        [hello][ ][world]
           W1    S    W2

    """

    start_char: int
    end_char: int

    kind: Literal[
        "word",
        "space",
        "line_break",
        "paragraph_break",
        "markup",
    ]

    word: Optional[Word] = None
    word_id: Optional[bytes] = None

    left_word: Optional[Word] = None
    right_word: Optional[Word] = None

    left_line: Optional[HocrLine] = None
    right_line: Optional[HocrLine] = None

    left_paragraph: Optional[HocrParagraph] = None
    right_paragraph: Optional[HocrParagraph] = None

    @property
    def char_length(self) -> int:
        return self.end_char - self.start_char


@dataclass
class ParsedLine:
    id_bytes: bytes
    words: list[Word]
    # byte range of the complete line element
    element_byte_range: tuple[int, int]


@dataclass
class ParsedParagraph:
    id_bytes: bytes
    lines: list[ParsedLine]
    # byte range of the complete paragraph element
    element_byte_range: tuple[int, int]


class HocrParser:
    @print_exceptions
    def __init__(self, source_bytes: bytes):
        # tree-sitter setup
        self.tree = None

        # Persistent document model
        self.paragraphs: list[HocrParagraph] = []

        self.words_by_id: dict[bytes, Word] = {}
        self.lines_by_id: dict[bytes, HocrLine] = {}
        self.paragraphs_by_id: dict[bytes, HocrParagraph] = {}

        # Plain-text position index
        self.text_spans: list[TextSpan] = []

        self._model_initialized = False

        # this also calls self._build_model()
        self.set_source_bytes(source_bytes)

    # ------------------------ public API ------------------------

    @property
    def is_xml(self) -> bool:
        return self._lang == "xml"

    @print_exceptions
    def find_words(self) -> List[Word]:
        return list(self._index_words().values())

    @print_exceptions
    def find_pages(self) -> List[Word]:
        pages = []
        stack = [self.tree.root_node]
        sb = self.source_bytes
        while stack:
            n = stack.pop()
            # Only consider element nodes
            if n.type in ("element", "html_element", "div"):
                page = self._extract_page_node(n, sb)
                if page:
                    pages.append(page)
            stack.extend(n.children)
        return pages

    @print_exceptions
    def _extract_page_node(self, element, sb: bytes) -> Optional[Word]:
        # Get start tag or STag
        if self._lang == "html":
            start_tag = next((c for c in element.children if c.type=="start_tag"), None)
            if not start_tag:
                return None
            attrs: dict[bytes, Tuple[bytes, Tuple[int, int]]] = {}
            for c in start_tag.children:
                if c.type=="attribute":
                    n,v,vr = self._read_html_attribute(c, sb)
                    if n: attrs[n] = (v,vr)
            cls_val = attrs.get(b"class", (b"", (0,0)))[0]
            if b"ocr_page" not in cls_val.split(): return None
            title_val, title_range = attrs.get(b"title", (b"", (0,0)))
            bbox,_ = _parse_title(title_val)
            if not bbox: bbox=(0,0,0,0)
            # TODO dont use "class Word" here
            return Word(
                id_bytes=attrs.get(b"id", (b"", (0,0)))[0],
                text_bytes=b"",
                bbox=bbox,
                x_wconf=None,
                title_value=title_val,
                byte_range=(0,0),
                title_value_range=title_range,
                id_value_range=attrs.get(b"id",(b"",(0,0)))[1],
                element_byte_range=(element.start_byte, element.end_byte),
                span_range=(0,0),
            )
        else:  # xml / xhtml
            # TODO rename to start_tags
            stags = [c for c in element.children if c.type=="STag"]
            if not stags: return None
            # TODO rename to start_tag
            st = stags[0]
            attrs: dict[bytes, Tuple[bytes, Tuple[int, int]]] = {}
            for c in st.children:
                if c.type=="Attribute":
                    n,v,vr = self._read_xml_attribute(c, sb)
                    if n: attrs[n]=(v,vr)
            cls_val = attrs.get(b"class", (b"", (0,0)))[0]
            if b"ocr_page" not in cls_val.split(): return None
            title_val, title_range = attrs.get(b"title", (b"", (0,0)))
            bbox,_ = _parse_title(title_val)
            if not bbox: bbox=(0,0,0,0)
            # TODO dont use "class Word" here
            return Word(
                id_bytes=attrs.get(b"id", (b"", (0,0)))[0],
                text_bytes=b"",
                bbox=bbox,
                x_wconf=None,
                title_value=title_val,
                byte_range=(0,0),
                title_value_range=title_range,
                id_value_range=attrs.get(b"id",(None,(0,0)))[1],
                element_byte_range=(element.start_byte, element.end_byte),
                span_range=(0,0),
            )

    @print_exceptions
    def get_word(self, word_id: str) -> Optional[Word]:
        return self._index_words().get(word_id)

    @print_exceptions
    def update(self,
               word_id: str,
               *,
               text_str: Optional[str] = None,
               bbox: Optional[Tuple[int, int, int, int]] = None,
               x_wconf: Optional[int] = None,
               new_id: Optional[str] = None) -> bool:
        """Apply one or more changes to a word by id using minimal diffs.
        Returns True if the word was found and something changed.
        """
        # print(f"parser.update: text_str {text_str!r} bbox {bbox!r}")
        idx = self._index_words()
        node = idx.get(word_id)
        if not node:
            return False

        changed = False

        assert isinstance(text_str, str)

        # 1) text
        if text_str is not None and node.byte_range:
            if debug_word_id and debug_word_id == word_id:
                old_text = self.source_bytes[node.byte_range[0]:node.byte_range[1]]
                print(f"word {word_id}: update: update text: {old_text!r} -> {text_str}")
            self._replace_range(node.byte_range, text_str)
            changed = True
            # reindex to refresh ranges after _replace_range
            idx = self._index_words()
            node = idx.get(word_id) or node

        # 2) title merge (bbox/x_wconf)
        if bbox is not None or x_wconf is not None:
            current_title = self.source_bytes[node.title_value_range[0]:node.title_value_range[1]]
            kwargs: dict[str, Any] = dict()
            if bbox is not None: kwargs["bbox"] = bbox
            if x_wconf is not None: kwargs["x_wconf"] = x_wconf
            new_title = _format_title(current_title, **kwargs)
            if debug_word_id and debug_word_id == word_id:
                if current_title == new_title:
                    print(f"word {word_id}: update: update title: no change in attribute @ {node.title_value_range}: title = {current_title!r}")
                else:
                    print(f"word {word_id}: update: update title: attribute @ {node.title_value_range}: title = {current_title!r}")
                    print(f"word {word_id}: update: update title: {current_title!r} -> {new_title!r}")
            # FIXME preserve the old x_wconf value (and all other semicolon-separated values in title)
            if new_title != current_title:
                self._replace_range(node.title_value_range, new_title)
                changed = True
                # reindex to refresh ranges after _replace_range
                idx = self._index_words()
                node = idx.get(word_id) or node

        # 3) id change
        if new_id is not None and new_id != node.id_bytes:
            self._replace_range(node.id_value_range, new_id)
            changed = True

        return changed

    @print_exceptions
    def update_by_span(
            self,
            span_start: int,
            *,
            text_str: Optional[str] = None,
            bbox: Optional[Tuple[int, int, int, int]] = None,
            x_wconf: Optional[int] = None,
            new_id: Optional[str] = None
        ) -> bool:
        """Update a word identified by its span start byte offset instead of id.

        This avoids collisions when multiple elements share the same id value.
        """
        word = self.find_word_by_span_start(span_start)
        if not word:
            return False

        changed = False

        assert isinstance(text_str, str)

        # 1) text
        if text_str is not None and word.byte_range:
            old_text = self.source_bytes[word.byte_range[0]:word.byte_range[1]]
            print(f"word {word.id_bytes}: update_by_span: update text (by span): {old_text!r} -> {text_str!r}")
            self._replace_range(word.byte_range, text_str)
            changed = True
            # re-find word after parse
            word = self.find_word_by_span_start(span_start) or word

        # 2) title merge (bbox/x_wconf)
        if bbox is not None or x_wconf is not None:
            current_title = self.source_bytes[word.title_value_range[0]:word.title_value_range[1]]
            kwargs: dict[str, Any] = {}
            if bbox is not None:
                kwargs["bbox"] = bbox
            if x_wconf is not None:
                kwargs["x_wconf"] = x_wconf
            new_title = _format_title(current_title, **kwargs)
            if current_title == new_title:
                if debug_word_id and debug_word_id == word.id_bytes:
                    print(f"word {word.id_bytes}: update_by_span: update title: no change in attribute @ {word.title_value_range}: title = {current_title!r}")
            else:
                if debug_word_id and debug_word_id == word.id_bytes:
                    print(f"word {word.id_bytes}: update_by_span: update title: attribute @ {word.title_value_range}: title = {current_title!r}")
                    print(f"word {word.id_bytes}: update_by_span: update title: {current_title!r} -> {new_title!r}")
                self._replace_range(word.title_value_range, new_title)
                changed = True
                word = self.find_word_by_span_start(span_start) or word

        # 3) id change
        if new_id is not None and new_id != word.id_bytes:
            self._replace_range(word.id_bytes_value_range, new_id)
            changed = True

        return changed

    @print_exceptions
    def find_word_by_span_start(self, span_start: int) -> Optional[Word]:
        """Return the Word whose span_range[0] equals span_start (or None)."""
        for w in self.find_words():
            if w.span_range and w.span_range[0] == span_start:
                return w
        return None

    # ------------------------ core ------------------------

    @print_exceptions
    def set_source_bytes(self, source_bytes: bytes, source_encoding="utf-8"):
        assert isinstance(source_bytes, bytes)
        self.source_bytes = source_bytes
        self.source_encoding = source_encoding
        self._lang = _detect_lang(self.source_bytes)
        lang = XML_LANG if self._lang == "xml" else HTML_LANG
        self.parser = Parser(lang)

        self.tree = self.parser.parse(self.source_bytes)

        r'''
        self.source_str = self.source_bytes.decode(self.source_encoding)
        # TypeError: source must be a bytestring or a callable, not str
        # self.tree = self.parser.parse(self.source_str)
        def read_source_str(byte_offset, point):
            # row, column = point
            # TypeError: read callable must return a bytestring
            # return self.source_str[byte_offset:]
            return self.source_bytes[byte_offset:]
        # fix: ValueError: encoding must be 'utf8', 'utf16', 'utf16le', or 'utf16be', not 'utf-8'
        encoding = self.source_encoding.lower().replace("-", "")
        self.tree = self.parser.parse(read_source_str, encoding=encoding)
        '''

        self._cached_index: Optional[Dict[bytes, Word]] = None

        # Rebuild the logical model immediately.
        debug = 0
        if debug:
            print(f"HocrParser.set_source_bytes: calling self._build_model")
        self._build_model()
        self._model_initialized = True

    def byte_offset_to_char_offset(self, byte_offset: int) -> int:
        return len(self.source_bytes[:byte_offset].decode(self.source_encoding))

    @print_exceptions
    def set_source_string(self, source: str, encoding=None):
        self.source_encoding = encoding or self.source_encoding
        assert isinstance(source, str)
        source_bytes = source.encode(self.source_encoding, errors="replace")
        self.set_source_bytes(source_bytes)

    @print_exceptions
    def get_source_string(self, encoding=None) -> str:
        encoding = encoding or self.source_encoding
        source = self.source_bytes.decode(encoding, errors="replace")
        return source

    @print_exceptions
    def _index_words(self) -> Dict[bytes, Word]:
        if self._cached_index is not None:
            return self._cached_index
        words: dict[bytes, Word] = {}
        root = self.tree.root_node
        stack = [root]
        sb = self.source_bytes
        while stack:
            n = stack.pop()
            if self._lang == "html":
                if n.type == "element":
                    w = self._extract_word_html(n, sb)
                    if w:
                        words[w.id_bytes] = w
            else:  # xml
                if n.type == "element":
                    w = self._extract_word_xml(n, sb)
                    if w:
                        words[w.id_bytes] = w
            # DFS
            stack.extend(n.children)
        self._cached_index = words
        return words

    # ------------------------ extraction: HTML ------------------------

    @print_exceptions
    def _extract_word_html(self, element, sb: bytes) -> Optional[Word]:
        # element = start_tag, (text|element)*, end_tag
        # Find start_tag
        if not element.children or element.children[0].type != "start_tag":
            return None
        start_tag = element.children[0]

        tag_name = None
        attrs: dict[bytes, Tuple[bytes, Tuple[int, int]]] = {}

        for ch in start_tag.children:
            t = ch.type
            if t == "tag_name":
                tag_name = sb[ch.start_byte:ch.end_byte]
            elif t == "attribute":
                n, v, vr = self._read_html_attribute(ch, sb)
                if n:
                    attrs[n] = (v, vr)

        if (tag_name or b"").lower() != b"span":
            return None
        cls_val = attrs.get(b"class", (b"", (0, 0)))[0]
        if not _class_has(cls_val, b"ocrx_word"):
            return None

        # id & title
        id_val, id_range = attrs.get(b"id", (b"", (0, 0)))
        title_val, title_range = attrs.get(b"title", (b"", (0, 0)))

        if debug_word_id and debug_word_id == id_val:
            for n, (v, vr) in attrs.items():
                print(f"_extract_word_html: attribute @ {vr}: {n} = {v!r}")

        # inner text: first 'text' child directly under element
        text_node = None
        for ch in element.children:
            if ch.type == "text":
                text_node = ch
                break
        end_tag = element.children[-1]
        if text_node is not None:
            text_bytes = sb[text_node.start_byte:text_node.end_byte]
            byte_range = (text_node.start_byte, text_node.end_byte)
        else:
            # empty span: zero-length before end_tag
            text_bytes = b""
            byte_range = (end_tag.start_byte, end_tag.start_byte)

        bbox, xw = _parse_title(title_val)
        if bbox is None:
            print(f"word {id_val!r}: failed to parse bbox from title {title_val!r}")
            return None
        assert not (bbox is None), f"word {id_val!r}: failed to parse bbox from title {title_val!r}"

        debug = 0
        debug = 1
        if debug:
            print("\n========== _extract_word_html ==========")

            print(
                "id:",
                repr(id_val),
            )

            print(
                "text_bytes:",
                repr(text_bytes),
            )

            print(
                "byte_range:",
                byte_range,
            )

            print(
                "element range:",
                (
                    element.start_byte,
                    element.end_byte,
                ),
            )

            if text_node is not None:
                print(
                    "text_node:",
                    text_node,
                )

                print(
                    "text_node.type:",
                    text_node.type,
                )

                print(
                    "text_node.start_byte:",
                    text_node.start_byte,
                )

                print(
                    "text_node.end_byte:",
                    text_node.end_byte,
                )

                print(
                    "source_bytes[text_node range]:",
                    repr(
                        sb[
                            text_node.start_byte:
                            text_node.end_byte
                        ]
                    ),
                )

            print(
                "source_bytes[byte_range]:",
                repr(
                    sb[
                        byte_range[0]:
                        byte_range[1]
                    ]
                ),
            )

            print(
                "element source:",
                repr(
                    sb[
                        element.start_byte:
                        element.end_byte
                    ]
                )[:500],
            )

            print(
                "========================================"
            )

        return Word(
            id_bytes=id_val,
            text_bytes=text_bytes,
            bbox=bbox,
            x_wconf=xw,
            title_value=title_val,
            byte_range=byte_range,
            title_value_range=title_range,
            id_value_range=id_range,
            element_byte_range=(element.start_byte, element.end_byte),
            span_range=(start_tag.start_byte, end_tag.end_byte),
        )

    @print_exceptions
    def _read_html_attribute(self, attr_node, sb: bytes) -> Tuple[Optional[bytes], bytes, Tuple[int, int]]:
        """
        Returns (name, value_without_quotes, inner_range) for HTML grammar.
        Handles multiple possible child node type names across html grammars.
        """
        name_node = getattr(attr_node, "child_by_field_name", lambda *_: None)("name")
        value_node = getattr(attr_node, "child_by_field_name", lambda *_: None)("value")

        if not name_node or not value_node:
            # Fallback: scan children for common node type names
            for c in attr_node.children:
                if not name_node and c.type in ("attribute_name", "property_identifier", "attribute_name_identifier", "name"):
                    name_node = c
                if not value_node and c.type in ("quoted_attribute_value", "attribute_value", "unquoted_attribute_value", "string"):
                    value_node = c

        if not name_node or not value_node:
            return None, b"", (attr_node.start_byte, attr_node.start_byte)

        name = sb[name_node.start_byte:name_node.end_byte]
        raw = sb[value_node.start_byte:value_node.end_byte]

        inner_start, inner_end = _strip_quote_range(value_node.start_byte, value_node.end_byte, raw)
        value = sb[inner_start:inner_end]
        return name, value, (inner_start, inner_end)

    # ------------------------ extraction: XML ------------------------

    @print_exceptions
    def _extract_word_xml(self, element, sb: bytes) -> Optional[Word]:
        # element -> STag, content?, ETag | EmptyElemTag
        # TODO rename to start_tags
        stags = [c for c in element.children if c.type == "STag"]
        if not stags:
            return None
        # TODO rename to start_tag
        st = stags[0]

        tag_name = None
        attrs: dict[bytes, Tuple[bytes, Tuple[int, int]]] = {}
        for c in st.children:
            if c.type == "Name" and tag_name is None:
                tag_name = sb[c.start_byte:c.end_byte]
            elif c.type == "Attribute":
                n, v, vr = self._read_xml_attribute(c, sb)
                if n:
                    attrs[n] = (v, vr)

        if (tag_name or b"").lower() != b"span":
            return None
        cls_val = attrs.get(b"class", (b"", (0, 0)))[0]
        if not _class_has(cls_val, b"ocrx_word"):
            return None

        id_val, id_range = attrs.get(b"id", (b"", (0, 0)))
        title_val, title_range = attrs.get(b"title", (b"", (0, 0)))

        if debug_word_id and debug_word_id == id_val:
            for n, (v, vr) in attrs.items():
                print(f"_extract_word_xml: attribute @ {vr}: {n} = {v!r}")

        # content text
        text_bytes = b""
        byte_range: Tuple[int, int] = (element.start_byte, element.start_byte)
        contents = [c for c in element.children if c.type == "content"]
        if contents:
            # find first CharData as text node
            for sub in contents[0].children:
                if sub.type == "CharData":
                    text_bytes = sb[sub.start_byte:sub.end_byte]
                    byte_range = (sub.start_byte, sub.end_byte)
                    break

        end_tag = element.children[-1]
        if end_tag.type != "ETag":
            print(f"FIXME not found end_tag (ETag) in element.children:\n  {'\n  '.join(map(repr, element.children))}")
            return None

        bbox, xw = _parse_title(title_val)
        if bbox is None:
            print(f"word {id_val!r}: failed to parse bbox from title {title_val!r}")
            return None
        assert not (bbox is None), f"word {id_val!r}: failed to parse bbox from title {title_val!r}"

        debug = 0
        # debug = 1
        if debug:
            # FIXME why is this called so many times
            if id_val in (
                b"word_1_1",
                b"word_1_2",
                b"word_1_3",
            ):

                print(
                    "\n========== _extract_word_xml =========="
                )

                print(
                    "id:",
                    repr(id_val),
                )

                print(
                    "text_bytes:",
                    repr(text_bytes),
                )

                print(
                    "byte_range:",
                    byte_range,
                )

                print(
                    "element range:",
                    (
                        element.start_byte,
                        element.end_byte,
                    ),
                )

                print(
                    "start tag range:",
                    (
                        st.start_byte,
                        st.end_byte,
                    ),
                )

                print(
                    "char_data_node:",
                    char_data_node,
                )

                if char_data_node is not None:

                    print(
                        "char_data_node.type:",
                        char_data_node.type,
                    )

                    print(
                        "char_data_node.start_byte:",
                        char_data_node.start_byte,
                    )

                    print(
                        "char_data_node.end_byte:",
                        char_data_node.end_byte,
                    )

                    print(
                        "source_bytes[char_data_node range]:",
                        repr(
                            sb[
                                char_data_node.start_byte:
                                char_data_node.end_byte
                            ]
                        ),
                    )

                print(
                    "source_bytes[byte_range]:",
                    repr(
                        sb[
                            byte_range[0]:
                            byte_range[1]
                        ]
                    ),
                )

                print(
                    "id_range:",
                    id_range,
                )

                print(
                    "title_range:",
                    title_range,
                )

                print(
                    "title_value:",
                    repr(title_val),
                )

                print(
                    "element source:",
                    repr(
                        sb[
                            element.start_byte:
                            element.end_byte
                        ]
                    )[:500],
                )

                print(
                    "========================================"
                )

        return Word(
            id_bytes=id_val,
            text_bytes=text_bytes,
            bbox=bbox,
            x_wconf=xw,
            title_value=title_val,
            byte_range=byte_range,
            title_value_range=title_range,
            id_value_range=id_range,
            element_byte_range=(element.start_byte, element.end_byte),
            span_range=(st.start_byte, end_tag.end_byte),
        )

    @print_exceptions
    def _read_xml_attribute(self, attr_node, sb: bytes) -> Tuple[Optional[bytes], bytes, Tuple[int, int]]:
        """
        Returns (name, value_without_quotes, inner_range) for XML grammar (tree-sitter-xml).
        """
        name_node = None
        value_node = None

        if hasattr(attr_node, "child_by_field_name"):
            name_node = attr_node.child_by_field_name("name") or None
            value_node = attr_node.child_by_field_name("value") or None

        if not name_node or not value_node:
            for c in attr_node.children:
                if not name_node and c.type in ("Name",):
                    name_node = c
                if not value_node and c.type in ("AttValue", "AttributeValue"):
                    value_node = c

        if not name_node or not value_node:
            return None, b"", (attr_node.start_byte, attr_node.start_byte)

        name = sb[name_node.start_byte:name_node.end_byte]
        raw = sb[value_node.start_byte:value_node.end_byte]

        inner_start, inner_end = _strip_quote_range(value_node.start_byte, value_node.end_byte, raw)
        value = sb[inner_start:inner_end]
        return name, value, (inner_start, inner_end)

    # ------------------------ editing ------------------------

    @print_exceptions
    def _replace_range(self, byte_range: Tuple[int, int], new_bytes: bytes):
        if isinstance(new_bytes, str):
            new_bytes = new_bytes.encode(self.source_encoding)
        # assert isinstance(new_bytes, bytes)
        # assert isinstance(self.source_bytes, bytes)
        start_byte, end_byte = byte_range
        if debug:
            old_bytes = self.source_bytes[start_byte:end_byte]
            print(f"_replace_range: range {byte_range}: {old_bytes!r} -> {new_bytes!r}")

        self.source_bytes = (
            self.source_bytes[:start_byte]
            + new_bytes
            + self.source_bytes[end_byte:]
        )

        # FIXME use incremental parsing
        self.tree = self.parser.parse(self.source_bytes)
        self._cached_index = None

        # Keep the persistent model consistent.
        print(f"HocrParser._replace_range: calling self._build_model")
        self._build_model()

    @print_exceptions
    def find_word_at_offset(self, pos: int) -> Optional[Word]:
        for word in self.find_words():
            if word.span_range[0] <= pos < word.span_range[1]:
                return word
        return None

    @print_exceptions
    def apply_plain_text_edit(
        self,
            position: int,
            chars_removed: int,
            inserted_text: str,
        ) -> bool:
        """
        Apply a Qt plain-text editor edit incrementally.

        Returns:

            True
                The edit was understood and applied.

            False
                The edit could not be handled safely. The caller should
                schedule a debounced full rebuild.

        IMPORTANT:

        This method receives the document state BEFORE Qt has applied
        the change to the editor's underlying document.

        Therefore position/chars_removed refer to the old plain-text model.
        """

        if position < 0:
            return False

        if chars_removed < 0:
            return False

        if not isinstance(inserted_text, str):
            return False

        # ---------------------------------------------------------
        # 1. Edit contained entirely inside one existing word.
        # ---------------------------------------------------------

        if self._apply_word_text_edit(
            position,
            chars_removed,
            inserted_text,
        ):
            return True

        # ---------------------------------------------------------
        # 2. Remove a space between two words.
        # ---------------------------------------------------------

        if self._apply_word_merge(
            position,
            chars_removed,
            inserted_text,
        ):
            return True

        # ---------------------------------------------------------
        # 3. Insert a space inside one word.
        # ---------------------------------------------------------

        if self._apply_word_split(
            position,
            chars_removed,
            inserted_text,
        ):
            return True

        # ---------------------------------------------------------
        # TODO:
        #
        # 4. Remove one newline:
        #       merge lines
        #
        # 5. Insert one newline:
        #       split line
        #
        # 6. Remove two newlines:
        #       merge paragraphs
        #
        # 7. Insert two newlines:
        #       split paragraph
        #
        # ---------------------------------------------------------

        return False

    # Recovery / consistency:
    @print_exceptions
    def rebuild_model(self):
        """
        Full consistency rebuild.

        Slow path used by the initial plain-text editor prototype.
        """
        self._cached_index = None

        # Force fresh tree/index information.
        debug = 0
        if debug:
            print(f"HocrParser.rebuild_model: calling self._build_model")
        self._build_model()

    @print_exceptions
    def _build_model(self):
        self.paragraphs.clear()
        self.words_by_id.clear()
        self.lines_by_id.clear()
        self.paragraphs_by_id.clear()

        self._cached_index = None

        debug = 0
        if debug:
            self._debug_build_generation = getattr(self, "_debug_build_generation", 0) + 1
            build_generation = self._debug_build_generation
            print(f"\n========== _build_model generation {build_generation} ==========")
            print("source_bytes id:", id(self.source_bytes))
            print("source_bytes length:", len(self.source_bytes))
            print("source_bytes text length:", len(self.source_bytes.decode(self.source_encoding)))
            # no, this prints to stderr
            # print("traceback.print_stack():"); traceback.print_stack()
            print("traceback.format_stack():\n" + "".join(traceback.format_stack()))

        parsed_paragraphs = self.find_document_structure()
        if debug:
            print(f"parser._build_model: len(parsed_paragraphs)={len(parsed_paragraphs)}")

        sb = self.source_bytes

        for parsed_paragraph in parsed_paragraphs:
            paragraph = HocrParagraph(
                id_bytes=parsed_paragraph.id_bytes,
                element_byte_range=parsed_paragraph.element_byte_range,
            )

            self.paragraphs.append(paragraph)

            if paragraph.id_bytes:
                assert isinstance(paragraph.id_bytes, bytes)
                self.paragraphs_by_id[paragraph.id_bytes] = paragraph

            for parsed_line in parsed_paragraph.lines:
                line = HocrLine(
                    id_bytes=parsed_line.id_bytes,
                    words=parsed_line.words,
                    element_byte_range=parsed_line.element_byte_range,
                )

                paragraph.lines.append(line)

                if line.id_bytes:
                    assert isinstance(line.id_bytes, bytes)
                    self.lines_by_id[line.id_bytes] = line

                for word in line.words:
                    assert isinstance(word.id_bytes, bytes)
                    self.words_by_id[word.id_bytes] = word
                    debug = 1
                    if debug:
                        word._debug_source_id = id(sb)
                        word._debug_source_len = len(sb)
                        word._debug_source_slice = sb[
                            word.byte_range[0]:
                            word.byte_range[1]
                        ]

        self._build_text_index()

    @print_exceptions
    def rebuild_hocr_from_plain_text(self, plain_text: str) -> str:
        """
        Slow prototype implementation.

        Reconstruct the hOCR document from the current document model
        and a modified plain-text representation.

        This currently supports:

            spaces     -> word boundaries
            newline    -> line boundaries
            blank line -> paragraph boundaries

        HTML markup inside word text is preserved as part of the text.
        """

        lines = plain_text.split("\n")

        # Split paragraphs on blank lines.
        paragraphs = []

        current_paragraph = []

        for line in lines:
            if line.strip() == "":
                if current_paragraph:
                    paragraphs.append(current_paragraph)
                    current_paragraph = []
            else:
                current_paragraph.append(line)

        if current_paragraph:
            paragraphs.append(current_paragraph)

        # Flatten the current model so we can reuse its elements.
        old_paragraphs = self.paragraphs

        # For the prototype we require roughly the same structure.
        if len(paragraphs) != len(old_paragraphs):
            raise ValueError(
                "Paragraph insertion/removal is not yet supported "
                "by the prototype"
            )

        # Work on bytes.
        source = self.source_bytes

        replacements = []

        for paragraph_index, paragraph_lines in enumerate(paragraphs):

            old_paragraph = old_paragraphs[paragraph_index]

            if len(paragraph_lines) != len(old_paragraph.lines):
                raise ValueError(
                    "Line insertion/removal is not yet supported "
                    "by the prototype"
                )

            for line_index, new_line_text in enumerate(paragraph_lines):

                old_line = old_paragraph.lines[line_index]

                new_words = new_line_text.split(" ")

                if len(new_words) != len(old_line.words):
                    raise ValueError(
                        "Word insertion/removal is not yet supported "
                        "by the prototype"
                    )

                for word, new_word_text in zip(
                    old_line.words,
                    new_words,
                ):
                    replacements.append(
                        (
                            word.byte_range[0],
                            word.byte_range[1],
                            new_word_text.encode(
                                self.source_encoding
                            ),
                        )
                    )

        # Apply replacements backwards so byte offsets remain valid.
        for start_byte, end_byte, replacement_bytes in reversed(replacements):
            source = (
                source[:start_byte]
                + replacement_bytes
                + source[end_byte:]
            )

        return source.decode(
            self.source_encoding,
            errors="replace",
        )

    @print_exceptions
    def find_document_structure(self) -> list[ParsedParagraph]:
        """
        Build the logical HOCR document structure:

            ocr_par
                ocr_line
                    ocrx_word

        Returns paragraphs containing lines containing words.

        The structure is derived from the current tree-sitter tree.
        """
        paragraphs = []

        root = self.tree.root_node
        stack = [root]

        while stack:
            node = stack.pop()

            if self._is_element(node):
                classes = self._get_element_class(node)

                if b"ocr_par" in classes.split():
                    paragraph = self._extract_paragraph(node)

                    if paragraph is not None:
                        paragraphs.append(paragraph)

                    # Do not search nested paragraphs separately.
                    continue

            # DFS
            stack.extend(reversed(node.children))

        return paragraphs

    @print_exceptions
    def _extract_paragraph(self, paragraph_node) -> Optional[ParsedParagraph]:
        """
        Extract one ocr_par element.
        """

        paragraph_id = self._get_element_attribute(
            paragraph_node,
            b"id",
        ) or b""

        lines = []

        stack = [paragraph_node]

        while stack:
            node = stack.pop()

            if node is not paragraph_node and self._is_element(node):
                classes = self._get_element_class(node)

                if b"ocr_line" in classes.split():
                    line = self._extract_line(node)

                    if line is not None:
                        lines.append(line)

                    # Do not recursively find nested lines.
                    continue

            stack.extend(reversed(node.children))

        return ParsedParagraph(
            id_bytes=paragraph_id,
            lines=lines,
            element_byte_range=(
                paragraph_node.start_byte,
                paragraph_node.end_byte,
            ),
        )

    @print_exceptions
    def _extract_line(self, line_node) -> Optional[ParsedLine]:
        """
        Extract one ocr_line element and its ocrx_word children.
        """

        line_id = self._get_element_attribute(
            line_node,
            b"id",
        ) or b""

        words = []

        stack = [line_node]

        while stack:
            node = stack.pop()

            if node is not line_node and self._is_element(node):
                word = self._extract_word(node)

                debug = False
                if debug:
                    if word is not None and word.id_bytes in {
                        b"word_1_1",
                        b"word_1_2",
                        b"word_1_3",
                    }:
                        print("\n========== WORD AFTER EXTRACTION ==========")
                        print("id:", word.id_bytes)
                        print("word.text_bytes:", word.text_bytes)
                        print("word.byte_range:", word.byte_range)
                        sb = self.source_bytes
                        print(
                            "sb[word.byte_range]:",
                            repr(sb[word.byte_range[0]:word.byte_range[1]]),
                        )
                        print("word.element_byte_range:", word.element_byte_range)
                        print("word.span_range:", word.span_range)
                        build_generation = self._debug_build_generation
                        print(f"WORD {word.id_bytes!r} created in build generation {build_generation}")
                        print("============================================")

                if word is not None:
                    words.append(word)

                    # Don't descend into an ocrx_word.
                    continue

            stack.extend(reversed(node.children))

        return ParsedLine(
            id_bytes=line_id,
            words=words,
            element_byte_range=(
                line_node.start_byte,
                line_node.end_byte,
            ),
        )

    @print_exceptions
    def _extract_word(self, node) -> Optional[Word]:
        if self._lang == "html":
            return self._extract_word_html(
                node,
                self.source_bytes,
            )

        return self._extract_word_xml(
            node,
            self.source_bytes,
        )

    @print_exceptions
    def _is_element(self, node) -> bool:
        if self._lang == "html":
            return node.type == "element"

        return node.type == "element"

    @print_exceptions
    def _get_element_class(self, element) -> bytes:
        """
        Return the class attribute value without quotes.
        """

        if self._lang == "html":
            start_tag = next(
                (
                    child
                    for child in element.children
                    if child.type == "start_tag"
                ),
                None,
            )

            if start_tag is None:
                return b""

            for child in start_tag.children:
                if child.type == "attribute":
                    name, value, _ = self._read_html_attribute(
                        child,
                        self.source_bytes,
                    )

                    if name == b"class":
                        return value

        else:
            start_tag = next(
                (
                    child
                    for child in element.children
                    if child.type == "STag"
                ),
                None,
            )

            if start_tag is None:
                return b""

            for child in start_tag.children:
                if child.type == "Attribute":
                    name, value, _ = self._read_xml_attribute(
                        child,
                        self.source_bytes,
                    )

                    if name == b"class":
                        return value

        return b""

    @print_exceptions
    def _get_element_attribute(
            self,
            element,
            attribute_name: bytes,
        ) -> Optional[bytes]:
        """
        Return an element attribute value without surrounding quotes.
        """

        if self._lang == "html":
            start_tag = next(
                (
                    child
                    for child in element.children
                    if child.type == "start_tag"
                ),
                None,
            )

            if start_tag is None:
                return None

            for child in start_tag.children:
                if child.type != "attribute":
                    continue

                name, value, _ = self._read_html_attribute(
                    child,
                    self.source_bytes,
                )

                if name == attribute_name:
                    return value

        else:
            start_tag = next(
                (
                    child
                    for child in element.children
                    if child.type == "STag"
                ),
                None,
            )

            if start_tag is None:
                return None

            for child in start_tag.children:
                if child.type != "Attribute":
                    continue

                name, value, _ = self._read_xml_attribute(
                    child,
                    self.source_bytes,
                )

                if name == attribute_name:
                    return value

        return None

    @print_exceptions
    def _build_text_index(self):
        self.text_spans.clear()
        char_position = 0
        for paragraph_index, paragraph in enumerate(self.paragraphs):
            for line_index, line in enumerate(paragraph.lines):
                for word_index, word in enumerate(line.words):
                    # ---------------------------------------------
                    # Space between words
                    # ---------------------------------------------
                    if word_index > 0:
                        previous_word = line.words[word_index - 1]
                        self.text_spans.append(
                            TextSpan(
                                start_char=char_position,
                                end_char=char_position + 1,
                                kind="space",
                                left_word=previous_word,
                                right_word=word,
                            )
                        )
                        char_position += 1
                    # ---------------------------------------------
                    # Word
                    # ---------------------------------------------
                    word_text = word.text_bytes.decode(self.source_encoding)
                    char_start = char_position
                    char_end = char_start + len(word_text)
                    self.text_spans.append(
                        TextSpan(
                            start_char=char_start,
                            end_char=char_end,
                            kind="word",
                            word=word,
                        )
                    )
                    char_position = char_end
                # ---------------------------------------------
                # Line break
                # ---------------------------------------------
                if line_index < len(paragraph.lines) - 1:
                    next_line = paragraph.lines[line_index + 1]
                    self.text_spans.append(
                        TextSpan(
                            start_char=char_position,
                            end_char=char_position + 1,
                            kind="line_break",
                            left_line=line,
                            right_line=next_line,
                        )
                    )
                    char_position += 1
            # ---------------------------------------------
            # Paragraph break
            # ---------------------------------------------
            if paragraph_index < len(self.paragraphs) - 1:
                next_paragraph = self.paragraphs[paragraph_index + 1]
                self.text_spans.append(
                    TextSpan(
                        start_char=char_position,
                        end_char=char_position + 2,
                        kind="paragraph_break",
                        left_paragraph=paragraph,
                        right_paragraph=next_paragraph,
                    )
                )
                char_position += 2

    # Derived view:
    @print_exceptions
    def get_plain_text(self) -> str:
        # FIXME use io.StringIO
        if debug:
            print(f"get_plain_text: len(self.paragraphs)={len(self.paragraphs)}")
        parts = []
        for paragraph_index, paragraph in enumerate(self.paragraphs):
            for line_index, line in enumerate(paragraph.lines):
                if line_index > 0:
                    parts.append("\n")
                parts.append(
                    " ".join(
                        word.text_bytes.decode(self.source_encoding)
                        for word in line.words
                    )
                )
            if paragraph_index < len(self.paragraphs) - 1:
                parts.append("\n\n")
        return "".join(parts)

    @print_exceptions
    def span_at(self, position: int) -> Optional[TextSpan]:
        """
        Return the text span containing position.

        For insertion points, position == span.end_char is considered to belong
        to the following span. This is important because Qt reports insertion
        positions between characters.
        """
        for span in self.text_spans:
            if span.start_char <= position < span.end_char:
                return span

        # Allow insertion exactly at the end of the document.
        if self.text_spans:
            last = self.text_spans[-1]
            if position == last.end_char:
                return last

        return None

    @print_exceptions
    def spans_overlapping(
            self,
            start_char: int,
            end_char: int,
        ) -> list[TextSpan]:
        """
        Return all spans intersecting [start_char, end_char).

        Zero-length edits are handled separately by apply_plain_text_edit().
        """
        if start_char == end_char:
            return []

        return [
            span
            for span in self.text_spans
            if span.start_char < end_char and span.end_char > start_char
        ]

    @print_exceptions
    def span_index(self, span: TextSpan) -> int:
        return self.text_spans.index(span)

    @print_exceptions
    def _shift_spans_after(
            self,
            position: int,
            delta: int,
        ):
        """
        incremental position shifting
        """
        for span in self.text_spans:

            if span.start_char >= position:
                span.start_char += delta
                span.end_char += delta

            elif span.end_char > position:
                span.end_char += delta

    @print_exceptions
    def _replace_text_spans(
        self,
            start_char: int,
            end_char: int,
            new_spans: list[TextSpan],
        ):
        """
        Replace spans covering [start_char, end_char).
        """
        affected = self.spans_overlapping(start_char, end_char)
        if affected:
            first_index = self.text_spans.index(affected[0])
            last_index = self.text_spans.index(affected[-1])
            self.text_spans[first_index:last_index + 1] = new_spans
        else:
            # insertion between existing spans
            insert_index = 0
            while (
                insert_index < len(self.text_spans)
                and self.text_spans[insert_index].start_char < start_char
            ):
                insert_index += 1
            self.text_spans[insert_index:insert_index] = new_spans
        self._normalize_span_positions()

    @print_exceptions
    def _replace_source_range(
            self,
            byte_range: tuple[int, int],
            new_bytes: bytes,
        ):
        start_byte, end_byte = byte_range

        old_bytes = self.source_bytes[start_byte:end_byte]

        if old_bytes == new_bytes:
            return 0

        delta = len(new_bytes) - len(old_bytes)

        self.source_bytes = (
            self.source_bytes[:start_byte]
            + new_bytes
            + self.source_bytes[end_byte:]
        )

        # Keep the tree in sync.
        #
        # This does NOT rebuild the persistent model.
        self.tree = self.parser.parse(self.source_bytes)
        self._cached_index = None

        return delta

    @print_exceptions
    def replace_plain_text(self, new_text: str) -> bool:
        """
        Apply a change made in the plain-text editor to the HOCR model.

        This is the slow prototype implementation.

        It compares the current parser representation with the new
        plain text and identifies one structural edit:

            word text change
            word merge
            word split
            line merge
            line split
            paragraph merge
            paragraph split

        The actual structural operations currently rebuild the parser
        model after each operation.

        Returns True if the change was applied.
        """

        # Normalize Qt line endings.
        new_text = new_text.replace("\r\n", "\n")
        new_text = new_text.replace("\r", "\n")

        old_text = self.get_plain_text()

        if old_text == new_text:
            return False

        # ---------------------------------------------------------
        # Find common prefix
        # ---------------------------------------------------------

        prefix = 0

        max_prefix = min(
            len(old_text),
            len(new_text),
        )

        while (
            prefix < max_prefix
            and old_text[prefix] == new_text[prefix]
        ):
            prefix += 1

        debug = 0
        if debug:
            print(
                "replace_plain_text: OLD CONTEXT:",
                repr(
                    old_text[
                        max(0, prefix - 20):
                        min(len(old_text), prefix + 20)
                    ]
                )
            )
            print(
                "replace_plain_text: NEW CONTEXT:",
                repr(
                    new_text[
                        max(0, prefix - 20):
                        min(len(new_text), prefix + 20)
                    ]
                )
            )
            print(
                "replace_plain_text: old_text[prefix-2:prefix+2]:",
                repr(old_text[prefix - 2:prefix + 2]),
            )
            print(
                "replace_plain_text: new_text[prefix-2:prefix+2]:",
                repr(new_text[prefix - 2:prefix + 2]),
            )
            print("\n========== SPANS AROUND INSERTION ==========")
            for span in self.text_spans:
                if (
                    span.start_char <= prefix + 2
                    and span.end_char >= prefix - 2
                ):
                    print(
                        span.kind,
                        span.start_char,
                        span.end_char,
                        repr(
                            old_text[
                                span.start_char:
                                span.end_char
                            ]
                        ),
                        "word=",
                        span.word.id_bytes
                        if span.word
                        else None,
                    )
            print("============================================")

        # ---------------------------------------------------------
        # Find common suffix
        #
        # Do not overlap the changed prefix.
        # ---------------------------------------------------------

        old_end = len(old_text)
        new_end = len(new_text)

        while (
            old_end > prefix
            and new_end > prefix
            and old_text[old_end - 1] == new_text[new_end - 1]
        ):
            old_end -= 1
            new_end -= 1

        old_changed = old_text[prefix:old_end]
        new_changed = new_text[prefix:new_end]

        if debug:
            print(
                f"replace_plain_text: prefix={prefix}"
                f" old_changed={old_changed!r}"
                f" new_changed={new_changed!r}"
            )

        # ---------------------------------------------------------
        # Determine the affected text spans
        # ---------------------------------------------------------

        old_start = prefix
        old_stop = old_end

        # A deletion can have an empty changed range.
        # In that case, inspect the span immediately before
        # the deletion position.
        affected_spans = self.spans_overlapping(
            old_start,
            old_stop,
        )

        if not affected_spans:
            affected_span = self.span_at(
                max(0, old_start - 1),
            )
        else:
            affected_span = affected_spans[0]

        # ---------------------------------------------------------
        # 1. Paragraph merge
        #
        # A paragraph break consists of "\n\n".
        #
        # Important:
        #
        # When deleting one of the two newline characters, the
        # common-prefix/common-suffix calculation may produce:
        #
        #     old_changed = "\n"
        #     new_changed = ""
        #
        # rather than:
        #
        #     old_changed = "\n\n"
        #     new_changed = ""
        #
        # Therefore we must inspect the structural paragraph_break
        # span at the edit position instead of relying only on the
        # contents of old_changed.
        #
        # Example:
        #
        #     paragraph 1
        #     \n\n
        #     paragraph 2
        #
        # Delete one newline:
        #
        #     paragraph 1
        #     \n
        #     paragraph 2
        #
        # The two paragraphs must be joined.
        # ---------------------------------------------------------

        paragraph_merge_candidate = None

        # ---------------------------------------------------------
        # Case A:
        #
        # The changed range directly overlaps the paragraph break.
        #
        # This handles cases where old_changed contains "\n".
        # ---------------------------------------------------------

        for span in affected_spans:
            if span.kind != "paragraph_break":
                continue

            if (
                span.left_paragraph
                and span.right_paragraph
            ):
                paragraph_merge_candidate = span
                break

        # ---------------------------------------------------------
        # Case B:
        #
        # The deletion has an empty changed range, or the common
        # prefix/suffix calculation isolated only one newline.
        #
        # In either case, prefix can lie inside the paragraph_break
        # span.
        #
        # Use:
        #
        #     span.start_char <= prefix <= span.end_char
        #
        # rather than requiring an exact match.
        # ---------------------------------------------------------

        if paragraph_merge_candidate is None:
            for span in self.text_spans:
                if span.kind != "paragraph_break":
                    continue

                if not (
                    span.start_char
                    <= prefix
                    <= span.end_char
                ):
                    continue

                if (
                    span.left_paragraph
                    and span.right_paragraph
                ):
                    paragraph_merge_candidate = span
                    break

        # ---------------------------------------------------------
        # Case C:
        #
        # The edit position can land immediately before or after
        # the paragraph_break span depending on how the plain-text
        # representation and the common-prefix calculation align.
        #
        # Check the neighboring paragraph break as well.
        # ---------------------------------------------------------

        if paragraph_merge_candidate is None:
            for span in self.text_spans:
                if span.kind != "paragraph_break":
                    continue

                if (
                    span.end_char == prefix
                    or span.start_char == prefix
                ):
                    if (
                        span.left_paragraph
                        and span.right_paragraph
                    ):
                        paragraph_merge_candidate = span
                        break

        # ---------------------------------------------------------
        # Only treat this as a paragraph merge if a newline was
        # actually deleted.
        #
        # This prevents unrelated edits at a paragraph boundary
        # from accidentally triggering a merge.
        # ---------------------------------------------------------

        if (
            paragraph_merge_candidate is not None
            and "\n" in old_changed
            and "\n" not in new_changed
        ):
            span = paragraph_merge_candidate

            print(
                "PARAGRAPH MERGE: deleting newline "
                "from paragraph break"
            )

            print(
                "PARAGRAPH MERGE: left paragraph:",
                span.left_paragraph.id_bytes
                if span.left_paragraph
                else None,
            )

            print(
                "PARAGRAPH MERGE: right paragraph:",
                span.right_paragraph.id_bytes
                if span.right_paragraph
                else None,
            )

            print(
                "PARAGRAPH MERGE: paragraph break:",
                span.start_char,
                span.end_char,
                repr(
                    old_text[
                        span.start_char:
                        span.end_char
                    ]
                ),
            )

            return self.merge_paragraphs(
                span.left_paragraph,
                span.right_paragraph,
            )

        # ---------------------------------------------------------
        # 2. Paragraph split
        #
        # "\n\n" inserted.
        # ---------------------------------------------------------

        if (
            "\n\n" in new_changed
            and "\n\n" not in old_changed
        ):
            # Find the line break immediately around the insertion.
            #
            # We identify the paragraph using the old model.
            for span in self.text_spans:
                if (
                    span.kind == "line_break"
                    and span.start_char <= prefix <= span.end_char
                ):
                    line = span.left_line

                    if line is None:
                        continue

                    for paragraph in self.paragraphs:
                        if line in paragraph.lines:
                            position = (
                                paragraph.lines.index(line)
                                + 1
                            )

                            return self.split_paragraph(
                                paragraph,
                                position,
                            )

        # ---------------------------------------------------------
        # 3. Line merge
        #
        # A single "\n" was removed.
        # ---------------------------------------------------------

        if (
            "\n" in old_changed
            and "\n\n" not in old_changed
            and "\n" not in new_changed
        ):
            for span in affected_spans:
                if span.kind == "line_break":
                    if (
                        span.left_line
                        and span.right_line
                    ):
                        return self.merge_lines(
                            span.left_line,
                            span.right_line,
                        )

            # Deleted newline => changed range is empty.
            for span in self.text_spans:
                if (
                    span.kind == "line_break"
                    and span.start_char <= prefix <= span.end_char
                ):
                    if (
                        span.left_line
                        and span.right_line
                    ):
                        return self.merge_lines(
                            span.left_line,
                            span.right_line,
                        )


        # ---------------------------------------------------------
        # 4. Newline insertion
        #
        # A single "\n" was inserted.
        #
        # There are three possible cases:
        #
        #   A. Inside a word:
        #
        #       helloWORLD
        #       ->
        #       hello\nWORLD
        #
        #       split_word()
        #       then split_line()
        #
        #   B. Between words on the same line:
        #
        #       hello world
        #       ->
        #       hello\nworld
        #
        #       split_line()
        #
        #   C. At a line boundary:
        #
        #       hello\n
        #       world
        #
        #       or:
        #
        #       hello
        #       \nworld
        #
        #       split_paragraph()
        #
        #       In this case the existing line structure is already
        #       correct. We only need to move the lines into two
        #       separate paragraphs.
        # ---------------------------------------------------------

        if (
            "\n" in new_changed
            and "\n\n" not in new_changed
            and "\n" not in old_changed
        ):
            debug = 0
            if debug:
                print("\n========== NEWLINE INSERT DEBUG ==========")
                print("prefix:", prefix)
                print("old_changed:", repr(old_changed))
                print("new_changed:", repr(new_changed))

            # -----------------------------------------------------
            # Find the spans immediately surrounding the insertion.
            #
            # `prefix` is a character offset in the OLD plain text.
            #
            # We intentionally use:
            #
            #     previous_span.end_char == prefix
            #     next_span.start_char == prefix
            #
            # rather than `span_at(prefix - 1)` because the
            # insertion is between two existing characters/spans.
            # -----------------------------------------------------

            # previous_span = None
            # next_span = None
            # for span in self.text_spans:
            #     if span.end_char == prefix:
            #         previous_span = span
            #     if span.start_char == prefix:
            #         next_span = span

            # previous_span, current_span, next_span = (
            #     self._get_spans_around_position(prefix)
            # )

            previous_span = None
            current_span = None
            next_span = None
            for span in self.text_spans:
                if span.end_char <= prefix:
                    previous_span = span
                if (
                    span.start_char <= prefix
                    and prefix < span.end_char
                ):
                    current_span = span
                if (
                    span.start_char >= prefix
                    and next_span is None
                ):
                    next_span = span

            if debug:
                print(
                    "previous_span:",
                    (
                        previous_span.kind,
                        previous_span.start_char,
                        previous_span.end_char,
                    )
                    if previous_span
                    else None,
                )

                print(
                    "next_span:",
                    (
                        next_span.kind,
                        next_span.start_char,
                        next_span.end_char,
                    )
                    if next_span
                    else None,
                )

            # ---------------------------------------------------------
            # AA. Newline inserted at a space between two words
            # B0. Newline inserted at a space between two words
            #
            # Example:
            #
            #     hello world
            #          ^
            #
            # becomes:
            #
            #     hello
            #     world
            #
            # The newline replaces the logical space between the words.
            # ---------------------------------------------------------

            if (
                current_span is not None
                and current_span.kind == "space"
                and current_span.left_word is not None
                and current_span.right_word is not None
            ):
                left_word = current_span.left_word
                right_word = current_span.right_word

                line = None

                for paragraph in self.paragraphs:
                    for candidate_line in paragraph.lines:
                        if (
                            left_word in candidate_line.words
                            and right_word in candidate_line.words
                        ):
                            line = candidate_line
                            break

                    if line is not None:
                        break

                if line is not None:
                    left_index = line.words.index(
                        left_word
                    )

                    right_index = line.words.index(
                        right_word
                    )

                    if right_index == left_index + 1:
                        if debug:
                            print(
                                "NEWLINE INSERT: "
                                "replacing space between adjacent words"
                            )

                        return self.split_line(
                            line,
                            left_index + 1,
                        )

            # ---------------------------------------------------------
            # A. Newline inserted inside a word
            #
            # Example:
            #
            #     spezifische
            #          ^
            #
            # becomes:
            #
            #     spezi
            #     fische
            #
            # This is a compound operation:
            #
            #     1. split_word()
            #     2. rebuild_model()
            #     3. find the two newly-created words
            #     4. split_line()
            #
            # IMPORTANT:
            #
            # The insertion position is inside current_span.
            #
            # For:
            #
            #     Viele spezifische Reformvorschläge
            #
            # prefix = 11 produces:
            #
            #     previous_span = space 5..6
            #     current_span  = word 6..17
            #     next_span     = space 17..18
            #
            # Therefore the inside-word test MUST use current_span,
            # not previous_span / next_span.
            # ---------------------------------------------------------

            if (
                current_span is not None
                and current_span.kind == "word"
                and current_span.word is not None
                and current_span.start_char < prefix
                and prefix < current_span.end_char
            ):
                word = current_span.word

                word_text = word.text_bytes.decode(
                    self.source_encoding,
                    errors="replace",
                )

                offset = (
                    prefix
                    - current_span.start_char
                )

                left_text = word_text[:offset]
                right_text = word_text[offset:]

                if debug:
                    print(
                        "NEWLINE INSERT: inside word"
                    )

                    print(
                        "NEWLINE INSERT: word:",
                        word.id_bytes,
                        repr(word_text),
                    )

                    print(
                        "NEWLINE INSERT: word span:",
                        current_span.start_char,
                        current_span.end_char,
                    )

                    print(
                        "NEWLINE INSERT: prefix:",
                        prefix,
                    )

                    print(
                        "NEWLINE INSERT: word offset:",
                        offset,
                    )

                    print(
                        "NEWLINE INSERT: expected left:",
                        repr(left_text),
                    )

                    print(
                        "NEWLINE INSERT: expected right:",
                        repr(right_text),
                    )

                # -----------------------------------------------------
                # Sanity check
                # -----------------------------------------------------

                if (
                    not left_text
                    or not right_text
                ):
                    if debug:
                        print(
                            "NEWLINE INSERT: invalid word split position"
                        )
                    return False

                # -----------------------------------------------------
                # Split the word.
                #
                # This rebuilds the model.
                # -----------------------------------------------------

                if not self.split_word(
                    word,
                    offset,
                ):
                    print(
                        "NEWLINE INSERT: split_word failed"
                    )
                    return False

                # -----------------------------------------------------
                # Find the two newly-created words.
                #
                # DO NOT use the old TextSpan objects here.
                #
                # split_word() rebuilt the model, so all old spans are
                # stale.
                #
                # Instead, find adjacent words whose text is:
                #
                #     left_text
                #     right_text
                # -----------------------------------------------------

                left_word = None
                right_word = None
                line = None

                for paragraph in self.paragraphs:
                    for candidate_line in paragraph.lines:

                        for i, candidate_word in enumerate(
                            candidate_line.words
                        ):
                            candidate_text = (
                                candidate_word.text_bytes.decode(
                                    self.source_encoding,
                                    errors="replace",
                                )
                            )

                            if candidate_text != left_text:
                                continue

                            if (
                                i + 1
                                >= len(candidate_line.words)
                            ):
                                continue

                            candidate_right_word = (
                                candidate_line.words[i + 1]
                            )

                            candidate_right_text = (
                                candidate_right_word.text_bytes.decode(
                                    self.source_encoding,
                                    errors="replace",
                                )
                            )

                            if candidate_right_text != right_text:
                                continue

                            # -------------------------------------------------
                            # Found the two words created by split_word().
                            # -------------------------------------------------

                            left_word = candidate_word
                            right_word = candidate_right_word
                            line = candidate_line

                            break

                        if line is not None:
                            break

                    if line is not None:
                        break

                # ---------------------------------------------------------
                # Verify that split_word() produced the expected result.
                # ---------------------------------------------------------

                if (
                    left_word is None
                    or right_word is None
                    or line is None
                ):
                    print(
                        "NEWLINE INSERT: could not locate "
                        "split words after split_word"
                    )

                    print(
                        "NEWLINE INSERT: expected left:",
                        repr(left_text),
                    )

                    print(
                        "NEWLINE INSERT: expected right:",
                        repr(right_text),
                    )

                    return False

                if debug:
                    print(
                        "NEWLINE INSERT: split words found:",
                        left_word.id_bytes,
                        repr(
                            left_word.text_bytes.decode(
                                self.source_encoding,
                                errors="replace",
                            )
                        ),
                        right_word.id_bytes,
                        repr(
                            right_word.text_bytes.decode(
                                self.source_encoding,
                                errors="replace",
                            )
                        ),
                    )

                    print(
                        "NEWLINE INSERT: line:",
                        line.id_bytes,
                    )

                # ---------------------------------------------------------
                # Find the position between the two words.
                # ---------------------------------------------------------

                left_index = line.words.index(
                    left_word
                )

                right_index = line.words.index(
                    right_word
                )

                if right_index != left_index + 1:
                    if debug:
                        print(
                            "NEWLINE INSERT: split words are not adjacent"
                        )
                    return False

                split_position = (
                    left_index + 1
                )

                if debug:
                    print(
                        "NEWLINE INSERT: splitting line after "
                        f"word index {left_index}"
                    )
                    print(
                        "NEWLINE INSERT: split position:",
                        split_position,
                    )

                # ---------------------------------------------------------
                # Split the line.
                #
                # This completes:
                #
                #     split_word()
                #     +
                #     split_line()
                # ---------------------------------------------------------

                return self.split_line(
                    line,
                    split_position,
                )

            # ---------------------------------------------------------
            # B0. Newline inserted immediately after a space and before
            #     the next word.
            #
            # Example:
            #
            #     hello world
            #          ^
            #          cursor is here, after the space
            #
            #     spans:
            #
            #         word   0..5
            #         space  5..6
            #         word   6..11
            #
            #     prefix == 6
            #
            #     previous_span = space
            #     next_span     = word
            #
            #     The intended operation is:
            #
            #         hello world
            #
            #     ->
            #
            #         hello
            #         world
            #
            #     Therefore split the existing line between the two words.
            # ---------------------------------------------------------

            if (
                previous_span is not None
                and next_span is not None
                and previous_span.kind == "space"
                and next_span.kind == "word"
                and previous_span.left_word is not None
                and previous_span.right_word is not None
                and next_span.word is not None
            ):
                left_word = previous_span.left_word
                right_word = previous_span.right_word

                # Sanity check: the right side of the space must be
                # the same word as the next span.
                if right_word is not next_span.word:
                    print(
                        "NEWLINE INSERT: space/right_word does not match "
                        "next word"
                    )
                else:
                    line = None

                    for paragraph in self.paragraphs:
                        for candidate_line in paragraph.lines:
                            if (
                                left_word in candidate_line.words
                                and right_word in candidate_line.words
                            ):
                                line = candidate_line
                                break

                        if line is not None:
                            break

                    if line is not None:
                        left_index = line.words.index(
                            left_word
                        )

                        right_index = line.words.index(
                            right_word
                        )

                        if right_index == left_index + 1:
                            if debug:
                                print(
                                    "NEWLINE INSERT: "
                                    "space -> newline between adjacent words"
                                )

                                print(
                                    "left word:",
                                    left_word.id_bytes,
                                    repr(
                                        left_word.text_bytes.decode(
                                            self.source_encoding,
                                            errors="replace",
                                        )
                                    ),
                                )

                                print(
                                    "right word:",
                                    right_word.id_bytes,
                                    repr(
                                        right_word.text_bytes.decode(
                                            self.source_encoding,
                                            errors="replace",
                                        )
                                    ),
                                )

                                print(
                                    "line:",
                                    line.id_bytes,
                                )

                                print(
                                    "split position:",
                                    left_index + 1,
                                )

                            return self.split_line(
                                line,
                                left_index + 1,
                            )

            # -----------------------------------------------------
            # B. Newline inserted between two words on the same line
            #
            # Example:
            #
            #     hello world
            #
            # becomes:
            #
            #     hello
            #     world
            #
            # This is a normal line split.
            # -----------------------------------------------------

            if (
                previous_span is not None
                and next_span is not None
                and previous_span.kind == "word"
                and next_span.kind == "word"
                and previous_span.word is not None
                and next_span.word is not None
            ):
                previous_word = previous_span.word
                next_word = next_span.word

                line = None

                for paragraph in self.paragraphs:
                    for candidate_line in paragraph.lines:
                        if (
                            previous_word in candidate_line.words
                            and
                            next_word in candidate_line.words
                        ):
                            line = candidate_line
                            break

                    if line is not None:
                        break

                if line is not None:
                    previous_index = line.words.index(
                        previous_word
                    )

                    next_index = line.words.index(
                        next_word
                    )

                    # Make sure the words are adjacent.
                    if next_index == previous_index + 1:
                        print(
                            "NEWLINE INSERT: between words "
                            "on same line"
                        )

                        return self.split_line(
                            line,
                            previous_index + 1,
                        )

            # -----------------------------------------------------
            # C. Newline inserted immediately after an existing line
            #
            # Example:
            #
            #     line 1
            #     line 2
            #
            # becomes:
            #
            #     line 1
            #
            #     line 2
            #
            # The newline is inserted immediately before the first
            # word of line 2.
            #
            # This is NOT a line split.
            #
            # It is a paragraph split:
            #
            #     <p>
            #       line 1
            #     </p>
            #     <p>
            #       line 2
            #     </p>
            # -----------------------------------------------------

            if (
                previous_span is not None
                and next_span is not None
                and previous_span.kind == "line_break"
                and next_span.kind == "word"
                and next_span.word is not None
            ):
                next_word = next_span.word

                right_line = None

                for paragraph in self.paragraphs:
                    for candidate_line in paragraph.lines:
                        if next_word in candidate_line.words:
                            right_line = candidate_line
                            break

                    if right_line is not None:
                        break

                if right_line is not None:
                    print(
                        "NEWLINE INSERT: after existing line break"
                    )

                    print(
                        "NEWLINE INSERT: right line:",
                        right_line.id_bytes,
                    )

                    for paragraph in self.paragraphs:
                        if right_line in paragraph.lines:
                            line_index = paragraph.lines.index(
                                right_line
                            )

                            # If this is not the first line of the
                            # paragraph, split the paragraph before
                            # this line.
                            if line_index > 0:
                                print(
                                    "NEWLINE INSERT: "
                                    "splitting paragraph before line",
                                    right_line.id_bytes,
                                )

                                return self.split_paragraph(
                                    paragraph,
                                    line_index,
                                )

                            # The right line is already the first
                            # line of its paragraph.
                            #
                            # That means the newline was inserted
                            # immediately after the previous line,
                            # but the paragraph is already split
                            # there. There is nothing to do.
                            print(
                                "NEWLINE INSERT: "
                                "right line is already first "
                                "line of its paragraph"
                            )
                            return False

            # -----------------------------------------------------
            # D. Newline inserted immediately before the first word
            #    of a line.
            #
            # This is the symmetric case:
            #
            #     line 1
            #     line 2
            #
            # insertion:
            #
            #     line 1
            #     \nline 2
            #
            # The result is again a paragraph split.
            # -----------------------------------------------------

            if (
                previous_span is not None
                and next_span is not None
                and previous_span.kind == "word"
                and next_span.kind == "line_break"
            ):
                previous_word = previous_span.word

                if previous_word is not None:
                    left_line = None

                    for paragraph in self.paragraphs:
                        for candidate_line in paragraph.lines:
                            if previous_word in candidate_line.words:
                                left_line = candidate_line
                                break

                        if left_line is not None:
                            break

                    if left_line is not None:
                        for paragraph in self.paragraphs:
                            if left_line in paragraph.lines:
                                line_index = paragraph.lines.index(
                                    left_line
                                )

                                if (
                                    line_index
                                    < len(paragraph.lines) - 1
                                ):
                                    print(
                                        "NEWLINE INSERT: "
                                        "splitting paragraph after line",
                                        left_line.id_bytes,
                                    )

                                    return self.split_paragraph(
                                        paragraph,
                                        line_index + 1,
                                    )

            print(
                "NEWLINE INSERT: "
                "could not classify newline insertion"
            )




        # ---------------------------------------------------------
        # 5. Word merge
        #
        # A space was removed.
        #
        # Example:
        #
        #     hello world
        #
        # becomes:
        #
        #     helloworld
        # ---------------------------------------------------------

        if (
            " " in old_changed
            and " " not in new_changed
        ):
            for span in affected_spans:
                if (
                    span.kind == "space"
                    and span.left_word
                    and span.right_word
                ):
                    return self.merge_words(
                        span.left_word,
                        span.right_word,
                    )

            # Deleted space => empty changed range.
            for span in self.text_spans:
                if (
                    span.kind == "space"
                    and span.start_char <= prefix <= span.end_char
                ):
                    if (
                        span.left_word
                        and span.right_word
                    ):
                        return self.merge_words(
                            span.left_word,
                            span.right_word,
                        )

        # ---------------------------------------------------------
        # 6. Word split
        #
        # A space was inserted.
        #
        # Example:
        #
        #     helloworld
        #
        # becomes:
        #
        #     hello world
        # ---------------------------------------------------------

        if (
            " " in new_changed
            and " " not in old_changed
        ):
            # Find the old word containing the insertion.
            for span in self.text_spans:
                if span.kind != "word":
                    continue

                if not (
                    span.start_char
                    <= prefix
                    <= span.end_char
                ):
                    continue

                if span.word is None:
                    continue

                offset = (
                    prefix
                    - span.start_char
                )

                debug_2 = 0
                if debug_2:
                    word = span.word
                    print("\n========== split_word CALL ==========")
                    print("word.id_bytes:", word.id_bytes)
                    print("word.text_bytes:", repr(word.text_bytes))
                    print("word.text_bytes.decode():", repr(word.text_bytes.decode(self.source_encoding)))
                    print("offset:", offset)
                    print("len(word.text_bytes.decode()):", len(word.text_bytes.decode(self.source_encoding)))
                    print("len(word.text_bytes):", len(word.text_bytes))
                    if 0:
                        print("word characters:")
                        for i, char in enumerate(word_text):
                            print(
                                f"  char[{i}] = {char!r}, "
                                f"bytes={char.encode(self.source_encoding)!r}, "
                                f"bytes_len={len(char.encode(self.source_encoding))}"
                            )
                    print("=====================================")

                    print("\n========== SPAN COORDINATE DEBUG ==========")
                    print("offset:", offset)
                    print("prefix:", prefix)
                    print("span.start_char:", span.start_char)
                    print("span.end_char:", span.end_char)
                    # Calculate the actual character position of this word
                    # in the current plain text.
                    plain_text = self.get_plain_text()
                    word_text = word.text_bytes.decode(self.source_encoding)
                    actual_char_start = plain_text.find(word_text)
                    print("actual_char_start:", actual_char_start)
                    print("actual_char_end:", actual_char_start + len(word_text))
                    print("expected offset:", prefix - actual_char_start)
                    print("current offset:", prefix - span.start_char)
                    print("difference:", span.start_char - actual_char_start)
                    print("==========================================")

                return self.split_word(
                    span.word,
                    offset,
                )

        # ---------------------------------------------------------
        # 7. Simple word text change
        #
        # No structural separator changed.
        #
        # Example:
        #
        #     hello
        #
        # becomes:
        #
        #     Hallo
        # ---------------------------------------------------------

        if (
            "\n" not in old_changed
            and "\n" not in new_changed
            and " " not in old_changed
            and " " not in new_changed
        ):
            span = self.span_at(prefix)

            if (
                span is not None
                and span.kind == "word"
                and span.word is not None
            ):
                word = span.word

                # Replace the complete word text.
                replacement = new_text[
                    span.start_char:
                    span.start_char
                    + len(new_changed)
                ]

                self.update_word_text(
                    word,
                    replacement,
                )

                return True

        # ---------------------------------------------------------
        # 8. Fallback
        #
        # The edit is too complex for the current incremental
        # prototype.
        #
        # For now, rebuild the model from the source rather than
        # silently corrupting the document.
        # ---------------------------------------------------------

        print("replace_plain_text: unsupported complex edit:")
        print(f"  old_changed={old_changed!r}")
        print(f"  new_changed={new_changed!r}")
        return False

    def _get_spans_around_position(
            self,
            position: int,
        ):
        previous = None
        current = None
        next_span = None

        for span in self.text_spans:
            if span.end_char <= position:
                previous = span

            if (
                span.start_char <= position
                and position < span.end_char
            ):
                current = span

            if span.start_char >= position:
                next_span = span
                break

        return previous, current, next_span

    @print_exceptions
    def _get_word_element_bytes(self, word: Word) -> bytes:
        """
        Return the complete original <span class="ocrx_word">...</span>
        element as bytes.
        """
        start_byte, end_byte = word.element_byte_range
        return self.source_bytes[start_byte:end_byte]

    @print_exceptions
    def _replace_document_text_structure(
            self,
            new_structure,
            old_words,
        ):
        """
        Rebuild the textual structure of the HOCR document.

        Existing word elements are reused in document order.

        This implementation intentionally rebuilds the relevant
        document structure instead of trying to perform incremental
        byte edits.
        """

        # ---------------------------------------------------------
        # Collect old word element templates
        # ---------------------------------------------------------

        old_elements = []

        for word in old_words:
            old_elements.append(
                self._get_word_element_bytes(word)
            )

        # ---------------------------------------------------------
        # Build new word elements
        # ---------------------------------------------------------

        new_word_elements = []

        old_index = 0

        for paragraph in new_structure:
            for line in paragraph:
                for text_bytes in line:
                    if old_index < len(old_words):
                        old_word = old_words[old_index]
                        element = self._replace_word_element_text(
                            old_word,
                            text_bytes,
                        )
                        new_word_elements.append(element)
                        assert isinstance(text_bytes, bytes)
                        old_index += 1
                    else:
                        # No existing word to reuse.
                        #
                        # Use the last existing word as a template.
                        if old_words:
                            template = old_words[-1]
                            element = self._create_word_from_template(
                                template,
                                text_bytes,
                            )
                            new_word_elements.append(element)

        # ---------------------------------------------------------
        # Replace the actual HOCR structure
        # ---------------------------------------------------------

        self._rebuild_document_with_words(
            new_structure,
            new_word_elements,
            old_words,
        )

    @print_exceptions
    def _replace_word_element_text(
            self,
            word: Word,
            new_text: str,
        ) -> bytes:
        """
        Replace only the textual contents of an existing word element.
        """

        element = self._get_word_element_bytes(word)

        old_text = word.text_bytes.decode(
            self.source_encoding,
            errors="replace",
        )

        old_bytes = old_text.encode(
            self.source_encoding,
            errors="replace",
        )

        new_bytes = new_text.encode(
            self.source_encoding,
            errors="replace",
        )

        # Replace only the first occurrence of the old word text.
        pos = element.find(old_bytes)

        if pos < 0:
            # Fallback: insert before closing tag.
            close_pos = element.rfind(b"</span>")

            if close_pos >= 0:
                return (
                    element[:close_pos]
                    + new_bytes
                    + element[close_pos:]
                )

            return element

        return (
            element[:pos]
            + new_bytes
            + element[pos + len(old_bytes):]
        )

    @print_exceptions
    def _create_word_from_template(
            self,
            template_word: Word,
            text_str: str,
        ) -> bytes:
        """
        Create a new word element using an existing word as template.

        The bbox and x_wconf are initially copied.

        The bbox will be improved later when we implement proper
        split/merge geometry.
        """

        element = self._get_word_element_bytes(template_word)

        old_text = template_word.text_bytes.decode(
            self.source_encoding,
            errors="replace",
        )

        old_bytes = old_text.encode(
            self.source_encoding,
            errors="replace",
        )

        new_bytes = text_str.encode(
            self.source_encoding,
            errors="replace",
        )

        # Generate a unique ID.
        new_id = template_word.id_bytes + b"_new"

        # Replace ID.
        id_start, id_end = template_word.id_value_range

        # The ranges are absolute source offsets, so we cannot
        # directly use them against the element bytes.
        #
        # Instead find the old ID text.
        id_pos = element.find(old_id := template_word.id_bytes)

        if id_pos >= 0:
            element = (
                element[:id_pos]
                + new_id
                + element[id_pos + len(old_id):]
            )

        # Replace text.
        text_pos = element.find(old_bytes)

        if text_pos >= 0:
            element = (
                element[:text_pos]
                + new_bytes
                + element[text_pos + len(old_bytes):]
            )

        return element

    @print_exceptions
    def _rebuild_words_from_plain_text(
            self,
            new_paragraphs,
        ):
        """
        Slow prototype implementation.

        Reconstruct the text content of existing ocrx_word elements
        from the edited plain-text representation.

        Existing words are reused in document order.

        NOTE:
        This currently does not create/delete physical <span>
        elements. It only supports a fixed number of words.
        """

        old_words = []

        for paragraph in self.paragraphs:
            for line in paragraph.lines:
                old_words.extend(line.words)

        new_words = []

        for paragraph in new_paragraphs:
            for line in paragraph:
                new_words.extend(line)

        if len(old_words) != len(new_words):
            raise NotImplementedError(
                "Changing the number of words is not yet implemented"
            )

        for old_word, new_word in zip(
            old_words,
            new_words,
        ):
            old_text = old_word.text_bytes.decode(
                self.source_encoding,
                errors="replace",
            )

            if old_text != new_word:
                self.update_by_span(
                    old_word.span_range[0],
                    text_bytes=new_word,
                )

    @print_exceptions
    def _normalize_span_positions(self):
        """
        Ensure the text index has contiguous positions.

        This is local enough for the initial implementation,
        but can later be optimized.
        """
        position = 0
        for span in self.text_spans:
            length = span.end_char - span.start_char
            span.start_char = position
            span.end_char = position + length
            position += length

    @print_exceptions
    def _replace_word_text_bytes(
            self,
            word: Word,
            new_text: bytes,
        ) -> None:
        start_byte, end_byte = word.byte_range

        self._replace_range(
            (start_byte, end_byte),
            new_text,
        )

        # Update the persistent Word object itself.
        delta = len(new_text) - (end_byte - start_byte)

        word.text_bytes = new_text
        word.byte_range = (
            start_byte,
            start_byte + len(new_text),
        )

        # Update ranges of all subsequent words.
        for other in self.words_by_id.values():
            if other is word:
                continue

            if other.byte_range[0] >= end_byte:
                other.byte_range = (
                    other.byte_range[0] + delta,
                    other.byte_range[1] + delta,
                )

            if other.title_value_range[0] >= end_byte:
                other.title_value_range = (
                    other.title_value_range[0] + delta,
                    other.title_value_range[1] + delta,
                )

            if other.id_value_range[0] >= end_byte:
                other.id_value_range = (
                    other.id_value_range[0] + delta,
                    other.id_value_range[1] + delta,
                )

            if other.element_byte_range[0] >= end_byte:
                other.element_byte_range = (
                    other.element_byte_range[0] + delta,
                    other.element_byte_range[1] + delta,
                )

            if other.span_range[0] >= end_byte:
                other.span_range = (
                    other.span_range[0] + delta,
                    other.span_range[1] + delta,
                )

    @print_exceptions
    def _word_text_bytes(self, word: Word) -> bytes:
        return self.source_bytes[
            word.byte_range[0]:
            word.byte_range[1]
        ]

    @print_exceptions
    def _replace_word_text(
            self,
            word: Word,
            new_text: str,
        ) -> bool:
        """
        Replace the textual content of an existing Word.

        This is the low-level operation used by the plain-text incremental
        editor. The parser source and tree are updated, then the affected
        Word is refreshed.
        """
        new_bytes = new_text.encode(
            self.source_encoding,
            errors="replace",
        )

        old_bytes = self._word_text_bytes(word)

        if old_bytes == new_bytes:
            return False

        self._replace_range(
            word.byte_range,
            new_bytes,
        )

        return True

    @print_exceptions
    def _find_word_by_span(
            self,
            span: TextSpan,
        ) -> Optional[Word]:
        if span.kind != "word":
            return None

        return span.word

    @print_exceptions
    def _find_word_at_text_position(
            self,
            position: int,
        ) -> Optional[Word]:
        span = self.span_at(position)

        if span is None:
            return None

        if span.kind != "word":
            return None

        return span.word

    @print_exceptions
    def _get_plain_text_slice(
            self,
            start_char: int,
            end_char: int,
        ) -> str:
        """
        Return text from the current plain-text model.

        This is intentionally derived from the model rather than from the
        source HTML directly.
        """
        text_str = self.get_plain_text()
        return text_str[start_char:end_char]

    @print_exceptions
    def _apply_word_text_edit(
        self,
            position: int,
            chars_removed: int,
            inserted_text: str,
        ) -> bool:
        """
        Apply an edit entirely contained inside one word.

        Example:

            hello world
                ^^^
                |
                edit only affects "world"

        Returns True if the edit was handled incrementally.
        """

        if chars_removed == 0 and not inserted_text:
            return False

        edit_end = position + chars_removed

        affected = self.spans_overlapping(
            position,
            edit_end,
        )

        # Insertion has zero-width and therefore does not overlap anything.
        if chars_removed == 0:
            span = self.span_at(position)

            if span is None:
                return False

            if span.kind != "word":
                return False

            affected = [span]

        if len(affected) != 1:
            return False

        span = affected[0]

        if span.kind != "word":
            return False

        word = span.word

        if word is None:
            return False

        # The edit must stay inside this word.
        if position < span.start_char:
            return False

        if edit_end > span.end_char:
            return False

        old_plain_text = self.get_plain_text()

        new_plain_text = (
            old_plain_text[:position]
            + inserted_text
            + old_plain_text[edit_end:]
        )

        new_word_start = span.start_char

        new_word_end = (
            span.end_char
            - chars_removed
            + len(inserted_text)
        )

        new_word_text = new_plain_text[
            new_word_start:
            new_word_end
        ]

        # Update the actual HOCR source.
        self._replace_word_text(
            word,
            new_word_text,
        )

        # Rebuild the affected model locally for now.
        #
        # This is deliberately isolated here. Later this can be replaced
        # with a true local update of word.byte_range and TextSpan positions.
        print(f"HocrParser._apply_word_text_edit: calling self._build_model")
        self._build_model()

        return True

    @print_exceptions
    def _merge_words(
            self,
            left: Word,
            right: Word,
        ) -> bool:
        """
        Merge two adjacent HOCR words.

        The left Word survives.

        Example:

            <span ...>hello</span>
            <span ...>world</span>

        becomes:

            <span ...>helloworld</span>

        The right word element is removed.
        """

        if left is None or right is None:
            return False

        if not left.element_byte_range:
            return False

        if not right.element_byte_range:
            return False

        # Make sure they are actually adjacent in the plain-text model.
        left_text = left.text_bytes.decode(
            self.source_encoding,
            errors="replace",
        )

        right_text = right.text_bytes.decode(
            self.source_encoding,
            errors="replace",
        )

        merged_text = left_text + right_text

        # Replace left word text.
        self._replace_range(
            left.byte_range,
            merged_text.encode(
                self.source_encoding,
                errors="replace",
            ),
        )

        # Re-find right word after the first replacement because all byte
        # offsets after left.byte_range may have changed.
        right_after = self.find_word_by_span_start(
            right.span_range[0]
        )

        if right_after is None:
            return False

        # Remove the complete right word element.
        self._replace_range(
            right_after.element_byte_range,
            b"",
        )

        print(f"HocrParser._merge_words: calling self._build_model")
        self._build_model()

        return True

    @print_exceptions
    def _apply_word_merge(
        self,
            position: int,
            chars_removed: int,
            inserted_text: str,
        ) -> bool:
        """
        Handle deletion of the single space between two words.
        """

        if chars_removed != 1:
            return False

        if inserted_text:
            return False

        span = self.span_at(position)

        if span is None:
            return False

        if span.kind != "space":
            return False

        left = span.left_word
        right = span.right_word

        if left is None or right is None:
            return False

        return self._merge_words(
            left,
            right,
        )

    @print_exceptions
    def _generate_split_word_id(
            self,
            word: Word,
        ) -> str:
        base_id = word.id_bytes.decode(
            "ascii",
            errors="replace",
        )

        candidate = f"{base_id}_split_1"

        existing_ids = {
            existing.id_bytes.decode(
                "ascii",
                errors="replace",
            )
            for existing in self.find_words()
        }

        counter = 1

        while candidate in existing_ids:
            counter += 1
            candidate = f"{base_id}_split_{counter}"

        return candidate

    @print_exceptions
    def _split_word(
            self,
            word: Word,
            offset: int,
        ) -> bool:
        """
        Split one Word into two words.

        Example:

            helloworld

        offset = 5

            hello | world
        """

        text_str = word.text_bytes.decode(
            self.source_encoding,
            errors="replace",
        )

        if offset <= 0:
            return False

        if offset >= len(text_str):
            return False

        left_text = text_str[:offset]
        right_text = text_str[offset:]

        new_id = self._generate_split_word_id(
            word
        )

        # Approximate bbox split.
        #
        # A later refinement can use character-level width estimation,
        # but initially split proportionally by character count.
        if word.bbox:
            x0, y0, x1, y1 = word.bbox

            width = x1 - x0

            split_x = (
                x0
                + int(
                    width
                    * offset
                    / len(text_str)
                )
            )

            left_bbox = (
                x0,
                y0,
                split_x,
                y1,
            )

            right_bbox = (
                split_x,
                y0,
                x1,
                y1,
            )

        else:
            left_bbox = None
            right_bbox = None

        # Replace the original word text.
        self._replace_range(
            word.byte_range,
            left_text.encode(
                self.source_encoding,
                errors="replace",
            ),
        )

        # Re-find the word because ranges changed.
        left_word = self.find_word_by_span_start(
            word.span_range[0]
        )

        if left_word is None:
            return False

        # Update left bbox.
        if left_bbox is not None:
            self.update_by_span(
                left_word.span_range[0],
                bbox=left_bbox,
            )

        # Insert a second word immediately after the first word's text.
        right_element = (
            b'<span class="ocrx_word" '
            b'id="'
            + new_id.encode(
                self.source_encoding
            )
            + b'" title="bbox '
            + b" ".join(
                str(value).encode()
                for value in right_bbox
            )
            + b'">'
            + right_text.encode(
                self.source_encoding
            )
            + b"</span>"
        )

        insertion_point = left_word.element_byte_range[1]

        self._replace_range(
            (
                insertion_point,
                insertion_point,
            ),
            b" " + right_element,
        )

        print(f"HocrParser._split_word: calling self._build_model")
        self._build_model()

        return True

    @print_exceptions
    def _apply_word_split(
        self,
            position: int,
            chars_removed: int,
            inserted_text: str,
        ) -> bool:
        """
        Handle insertion of a single space inside a word.
        """

        if chars_removed != 0:
            return False

        if inserted_text != " ":
            return False

        span = self.span_at(position)

        if span is None:
            return False

        if span.kind != "word":
            return False

        word = span.word

        if word is None:
            return False

        offset = position - span.start_char

        return self._split_word(
            word,
            offset,
        )

    @print_exceptions
    def update_word_text_incremental(
            self,
            word: Word,
            new_text: str,
        ) -> bool:
        """
        Incrementally update the text of one existing Word.

        This updates:

            1. source_bytes
            2. the persistent Word object
            3. Word byte ranges
            4. text-span positions

        It does NOT rebuild the document model.

        Returns True if something changed.
        """

        new_bytes = new_text.encode(
            self.source_encoding,
            errors="replace",
        )

        old_bytes = word.text_bytes

        if old_bytes == new_bytes:
            return False

        start_byte, end_byte = word.byte_range

        old_length = end_byte - start_byte
        new_length = len(new_bytes)

        delta = new_length - old_length

        print(
            "update_word_text_incremental:",
            word.id_bytes,
            repr(old_bytes),
            "->",
            repr(new_bytes),
            "delta=",
            delta,
        )

        # --------------------------------------------------
        # 1. Update source bytes
        # --------------------------------------------------

        self._replace_source_range(
            (start_byte, end_byte),
            new_bytes,
        )

        # --------------------------------------------------
        # 2. Update the persistent Word object
        # --------------------------------------------------

        word.text_bytes = new_bytes

        word.byte_range = (
            start_byte,
            start_byte + new_length,
        )

        # --------------------------------------------------
        # 3. Shift source ranges of later objects
        # --------------------------------------------------

        for other in self.words_by_id.values():

            if other is word:
                continue

            def shift_range(
                value: tuple[int, int],
            ) -> tuple[int, int]:

                range_start, range_end = value

                if range_start >= end_byte:
                    return (
                        range_start + delta,
                        range_end + delta,
                    )

                if range_end > end_byte:
                    return (
                        range_start,
                        range_end + delta,
                    )

                return value

            other.byte_range = shift_range(
                other.byte_range
            )

            other.title_value_range = shift_range(
                other.title_value_range
            )

            other.id_value_range = shift_range(
                other.id_value_range
            )

            other.element_byte_range = shift_range(
                other.element_byte_range
            )

            other.span_range = shift_range(
                other.span_range
            )

        # --------------------------------------------------
        # 4. Update the corresponding TextSpan
        # --------------------------------------------------

        word_span = None

        for span in self.text_spans:
            if (
                span.kind == "word"
                and span.word is word
            ):
                word_span = span
                break

        if word_span is None:
            # Persistent model is inconsistent.
            # The caller should schedule a full rebuild.
            print(
                "WARNING: could not find TextSpan for Word",
                word.id_bytes,
            )
            return True

        old_span_end = word_span.end_char

        word_span.end_char = (
            word_span.start_char
            + new_length
        )

        # --------------------------------------------------
        # 5. Shift all following plain-text spans
        # --------------------------------------------------

        for span in self.text_spans:

            if span is word_span:
                continue

            if span.start_char >= old_span_end:
                span.start_char += delta
                span.end_char += delta

            elif span.end_char > old_span_end:
                span.end_char += delta

        return True

    @print_exceptions
    def _replace_element_range(
            self,
            element_byte_range: tuple[int, int],
            new_bytes: bytes,
        ):
        """
        Replace a complete element in source_bytes and rebuild the parser tree.

        This is intentionally a slow prototype implementation.
        """
        start_byte, end_byte = element_byte_range

        self.source_bytes = (
            self.source_bytes[:start_byte]
            + new_bytes
            + self.source_bytes[end_byte:]
        )

        self.tree = self.parser.parse(self.source_bytes)
        self._cached_index = None

        # The persistent model is now stale.
        self._model_initialized = False

    @print_exceptions
    def _make_unique_id(
            self,
            prefix: bytes,
            parent_id: bytes | None = None,
        ) -> bytes:
        """
        Generate a globally unique hierarchical document ID.

        Examples:

            _make_unique_id("word")
                -> word_1

            _make_unique_id("word", "word_1_2")
                -> word_1_2_1

            _make_unique_id("word", "word_1_2")
                -> word_1_2_2
                if word_1_2_1 already exists
        """
        assert isinstance(prefix, bytes)
        if parent_id:
            assert isinstance(parent_id, bytes)
        existing_ids = set(self.words_by_id)
        existing_ids.update(self.lines_by_id)
        existing_ids.update(self.paragraphs_by_id)
        if parent_id:
            id_prefix = parent_id
        else:
            id_prefix = prefix
        index = 1
        while True:
            candidate = id_prefix + b"_" + str(index).encode("ascii")
            if candidate not in existing_ids:
                return candidate
            index += 1

    @print_exceptions
    def _make_split_element_id(
            self,
            element_id: bytes,
        ) -> bytes:
        """
        Generate the ID for the new right-hand element created by
        splitting `element_id`.

        Repeated splits preserve document order by extending the
        deepest existing split chain.

        Example:

            line_1_1
                ->
            line_1_1
            line_1_1_1

        Split line_1_1 again:

            line_1_1
            line_1_1_1_1
            line_1_1_1

        Split line_1_1 again:

            line_1_1
            line_1_1_1_1_1
            line_1_1_1_1
            line_1_1_1

        The same algorithm applies to words, lines, and paragraphs.
        """

        # ---------------------------------------------------------
        # Collect all IDs which must remain globally unique.
        # ---------------------------------------------------------

        existing_ids = set()

        existing_ids.update(self.words_by_id)
        existing_ids.update(self.lines_by_id)
        existing_ids.update(self.paragraphs_by_id)

        # ---------------------------------------------------------
        # Start with the direct synthetic child.
        #
        #     line_1_1
        #         ->
        #     line_1_1_1
        # ---------------------------------------------------------

        candidate = (
            element_id
            + b"_1"
        )

        # If unused, this is the first split.
        if candidate not in existing_ids:
            return candidate

        # ---------------------------------------------------------
        # The direct child already exists.
        #
        # Find the deepest descendant chain beginning at candidate.
        #
        # Example:
        #
        #     line_1_1_1
        #     line_1_1_1_1
        #     line_1_1_1_1_1
        #
        # We want to append another `_1`:
        #
        #     line_1_1_1_1_1_1
        # ---------------------------------------------------------

        chain = candidate

        while True:
            next_candidate = (
                chain
                + b"_1"
            )

            if next_candidate in existing_ids:
                chain = next_candidate
                continue

            # The next `_1` in the chain is unused.
            #
            # Use it.
            return next_candidate

    @print_exceptions
    def _make_split_word_id(
            self,
            word: HocrWord,
        ) -> bytes:
        return self._make_split_element_id(
            word.id_bytes,
        )

    @print_exceptions
    def _make_split_line_id(
            self,
            line: HocrLine,
        ) -> bytes:
        return self._make_split_element_id(
            line.id_bytes,
        )

    @print_exceptions
    def _make_split_paragraph_id(
            self,
            paragraph: HocrParagraph,
        ) -> bytes:
        return self._make_split_element_id(
            paragraph.id_bytes,
        )

    @print_exceptions
    def merge_words(
            self,
            left: Word,
            right: Word,
        ) -> bool:
        """
        Merge two adjacent words.

        The right word is removed.

        Example:

            <span ...>hello</span>
            <span ...>world</span>

        becomes:

            <span ...>helloworld</span>
        """

        if left is None or right is None:
            return False

        # Ensure they are actually adjacent in the same line.
        found = False

        for paragraph in self.paragraphs:
            for line in paragraph.lines:
                for i in range(len(line.words) - 1):
                    if (
                        line.words[i] is left
                        and line.words[i + 1] is right
                    ):
                        found = True
                        break

        if not found:
            return False

        # ---------------------------------------------------------
        # Calculate merged text
        # ---------------------------------------------------------

        left_text = left.text_bytes.decode(
            self.source_encoding,
            errors="replace",
        )

        right_text = right.text_bytes.decode(
            self.source_encoding,
            errors="replace",
        )

        new_text = left_text + right_text

        # ---------------------------------------------------------
        # Calculate merged bbox
        # ---------------------------------------------------------

        bbox = self._union_bbox(
            left.bbox,
            right.bbox,
        )

        # ---------------------------------------------------------
        # Build new left word element
        # ---------------------------------------------------------

        left_element = self._get_word_element_bytes(left)

        old_left_bytes = left.text_bytes

        text_pos = left_element.find(old_left_bytes)

        if text_pos < 0:
            return False

        left_element = (
            left_element[:text_pos]
            + new_text.encode(self.source_encoding)
            + left_element[
                text_pos + len(old_left_bytes):
            ]
        )

        # Update bbox in left element.
        if bbox is not None:
            current_title = (
                self.source_bytes[
                    left.title_value_range[0]:
                    left.title_value_range[1]
                ]
            )

            new_title = _format_title(
                current_title,
                bbox=bbox,
            )

            relative_title_start = (
                left.title_value_range[0]
                - left.element_byte_range[0]
            )

            relative_title_end = (
                left.title_value_range[1]
                - left.element_byte_range[0]
            )

            # Text length may have changed, so find the title
            # again in the generated element.
            old_title = (
                self.source_bytes[
                    left.title_value_range[0]:
                    left.title_value_range[1]
                ]
            )

            title_pos = left_element.find(
                old_title
            )

            if title_pos >= 0:
                left_element = (
                    left_element[:title_pos]
                    + new_title
                    + left_element[
                        title_pos + len(old_title):
                    ]
                )

        # ---------------------------------------------------------
        # Replace both word elements with merged word
        # ---------------------------------------------------------

        start_byte = left.element_byte_range[0]
        end_byte = right.element_byte_range[1]

        self._replace_element_range(
            (start_byte, end_byte),
            left_element,
        )

        self.rebuild_model()

        return True

    @print_exceptions
    def split_word(self, word: Word, offset: int) -> bool:
        """
        Split one word into two words.

        Example:

            helloworld

        offset=5

        becomes:

            hello world
        """

        if word is None:
            return False

        debug = 0
        if debug:
            print("\n========== split_word DEBUG ==========")
            print("word.id_bytes:", word.id_bytes)
            print("word.text_bytes:", repr(word.text_bytes))
            print("word.text_bytes.decode():", repr(word.text_bytes.decode(self.source_encoding)))
            print("offset:", offset)
            print("len(self.source_bytes):", len(self.source_bytes))
            print("len(self.get_source_string()):", len(self.get_source_string()))
            print("word.byte_range:", word.byte_range)
            print("word.element_byte_range:", word.element_byte_range)
            print("word.span_range:", word.span_range)
            start_byte, end_byte = word.byte_range
            print("self.source_bytes[start_byte:end_byte]:",
                repr(self.source_bytes[start_byte:end_byte]))
            # print("self.get_source_string()[start_byte:end_byte]:",
            #     repr(self.get_source_string()[start_byte:end_byte]))
            source_string = self.get_source_string()
            start_char = len(self.source_bytes[:start_byte].decode(self.source_encoding))
            end_char = len(self.source_bytes[:end_byte].decode(self.source_encoding))
            print("self.get_source_string()[start_char:end_char]:",
                repr(source_string[start_char:end_char]))
            # no, this is useless
            if 0:
                # Explicitly show byte positions
                decoded_word = word.text_bytes.decode(self.source_encoding)
                for i, char in enumerate(decoded_word):
                    print(
                        f"char {i}: {char!r} "
                        f"bytes={char.encode(self.source_encoding)!r} "
                        f"bytes_len={len(char.encode(self.source_encoding))}"
                    )
            print("======================================")

        text_str = word.text_bytes.decode(
            self.source_encoding,
            errors="replace",
        )

        if offset <= 0 or offset >= len(text_str):
            return False

        left_text = text_str[:offset]
        right_text = text_str[offset:]

        # ---------------------------------------------------------
        # Split bbox
        # ---------------------------------------------------------

        left_bbox, right_bbox = self._split_word_bbox(
            word.bbox,
            offset,
            len(text_str),
        )

        # ---------------------------------------------------------
        # Create first word
        # ---------------------------------------------------------

        left_element = self._get_word_element_bytes(word)

        old_text_bytes = word.text_bytes

        text_pos = left_element.find(
            old_text_bytes
        )

        if text_pos < 0:
            return False

        left_element = (
            left_element[:text_pos]
            + left_text.encode(self.source_encoding)
            + left_element[
                text_pos + len(old_text_bytes):
            ]
        )

        if left_bbox:
            old_title = (
                self.source_bytes[
                    word.title_value_range[0]:
                    word.title_value_range[1]
                ]
            )

            new_title = _format_title(
                old_title,
                bbox=left_bbox,
            )

            title_pos = left_element.find(
                old_title
            )

            if title_pos >= 0:
                left_element = (
                    left_element[:title_pos]
                    + new_title
                    + left_element[
                        title_pos + len(old_title):
                    ]
                )

        # ---------------------------------------------------------
        # Create second word from template
        # ---------------------------------------------------------

        right_element = self._get_word_element_bytes(word)

        # new_id = self._make_unique_id(
        #     b"word",
        #     parent_id=word.id_bytes.decode("ascii"),
        # )
        new_id = self._make_split_word_id(word)

        # Replace ID.
        old_id = word.id_bytes

        id_pos = right_element.find(
            old_id
        )

        if id_pos >= 0:
            right_element = (
                right_element[:id_pos]
                + new_id
                + right_element[id_pos + len(old_id):]
            )

        # Replace text.
        text_pos = right_element.find(
            old_text_bytes
        )

        if text_pos < 0:
            return False

        right_element = (
            right_element[:text_pos]
            + right_text.encode(
                self.source_encoding
            )
            + right_element[
                text_pos + len(old_text_bytes):
            ]
        )

        # Update bbox.
        if right_bbox:
            old_title = (
                self.source_bytes[
                    word.title_value_range[0]:
                    word.title_value_range[1]
                ]
            )

            new_title = _format_title(
                old_title,
                bbox=right_bbox,
            )

            title_pos = right_element.find(
                old_title
            )

            if title_pos >= 0:
                right_element = (
                    right_element[:title_pos]
                    + new_title
                    + right_element[
                        title_pos + len(old_title):
                    ]
                )

        # ---------------------------------------------------------
        # Replace original word with two words
        # ---------------------------------------------------------

        new_element = (
            left_element + b"\n"
            # 6 spaces indent
            + b"      " + right_element
        )

        self._replace_element_range(
            word.element_byte_range,
            new_element,
        )

        self.rebuild_model()

        return True

    @print_exceptions
    def _split_word_bbox(
            self,
            bbox: Optional[Tuple[int, int, int, int]],
            offset: int,
            text_length: int,
        ) -> Tuple[
            Optional[Tuple[int, int, int, int]],
            Optional[Tuple[int, int, int, int]],
        ]:
        """
        Calculate left/right bboxes for a word split.

        The split position is estimated proportionally from the
        character position.

        A small horizontal gap is left between the resulting
        word boxes.
        """

        if bbox is None:
            return None, None

        if text_length <= 0:
            return bbox, bbox

        x1, y1, x2, y2 = bbox

        split_x = (
            x1
            + int(
                (x2 - x1)
                * offset
                / text_length
            )
        )

        # Approximate visual gap between the two resulting words.
        margin_x = round(
            (y2 - y1)
            * 0.6
            * 0.5
            * 0.5
        )

        left_x2 = split_x - margin_x
        right_x1 = split_x + margin_x

        # Keep both boxes minimally usable.
        min_width = 5

        left_x2 = max(
            left_x2,
            x1 + min_width,
        )

        right_x1 = min(
            right_x1,
            x2 - min_width,
        )

        # Clamp to original bbox.
        left_x2 = min(
            left_x2,
            x2,
        )

        right_x1 = max(
            right_x1,
            x1,
        )

        left_bbox = (
            x1,
            y1,
            left_x2,
            y2,
        )

        right_bbox = (
            right_x1,
            y1,
            x2,
            y2,
        )

        return left_bbox, right_bbox

    @print_exceptions
    def merge_lines(
            self,
            left: HocrLine,
            right: HocrLine,
        ) -> bool:
        """
        Merge two adjacent lines in the same paragraph.
        """

        if left is None or right is None:
            return False

        parent = None

        for paragraph in self.paragraphs:
            for i in range(len(paragraph.lines) - 1):
                if (
                    paragraph.lines[i] is left
                    and paragraph.lines[i + 1] is right
                ):
                    parent = paragraph
                    break

        if parent is None:
            return False

        left_element = self.source_bytes[left.element_byte_range[0]:left.element_byte_range[1]]
        right_element = self.source_bytes[right.element_byte_range[0]:right.element_byte_range[1]]

        # Remove the outer line tags from the right line.
        right_content = self._extract_element_inner_html(right_element, b"span")

        opening_end = left_element.find(b">")

        if opening_end < 0:
            return False

        left_opening_tag = left_element[:opening_end + 1]

        # Line bboxes become invalid after merging.
        # line bboxes are optional, so we simply remove them
        left_opening_tag = self._remove_bbox_from_element(left_opening_tag)

        left_content = left_element[opening_end + 1:]

        # Remove the original closing </span>.
        close_pos = left_content.rfind(b"</span>")

        if close_pos < 0:
            return False

        left_content = left_content[:close_pos]

        right_content = self._extract_element_inner_html(right_element, b"span")

        merged_element = (
            left_opening_tag
            + left_content.rstrip()
            + right_content
            + b"</span>"
        )

        debug = 0
        if debug:
            print(f"merge_lines: left_element: {left_element!r}")
            print(f"merge_lines: right_element: {right_element!r}")
            print(f"merge_lines: right_content: {right_content!r}")
            print(f"merge_lines: merged_element: {merged_element!r}")

        self._replace_element_range(
            (
                left.element_byte_range[0],
                right.element_byte_range[1],
            ),
            merged_element,
        )

        self.rebuild_model()

        return True

    @print_exceptions
    def _extract_element_inner_html(
            self,
            element: bytes,
            tag_name: bytes,
        ) -> bytes:
        """
        Extract the content between <tag ...> and </tag>.
        """

        close_tag = (
            b"</"
            + tag_name
            + b">"
        )

        close_pos = element.rfind(
            close_tag
        )

        if close_pos < 0:
            return element

        open_end = element.find(
            b">"
        )

        if open_end < 0:
            return element

        return element[
            open_end + 1:
            close_pos
        ]

    @print_exceptions
    def split_line(
            self,
            line: HocrLine,
            position: int,
        ) -> bool:
        """
        Split a line after `position` words while preserving as much of the
        original HOCR source bytes as possible.

        Example:

            original:

                <span class="ocr_line" id="line_1">
                    <span class="ocrx_word">hello</span>
                    <span class="ocrx_word">world</span>
                    <span class="ocrx_word">this</span>
                    <span class="ocrx_word">is</span>
                </span>

            position=2

            becomes approximately:

                <span class="ocr_line" id="line_1">
                    <span class="ocrx_word">hello</span>
                    <span class="ocrx_word">world</span>
                </span>
                <span class="ocr_line" id="line_2">
                    <span class="ocrx_word">this</span>
                    <span class="ocrx_word">is</span>
                </span>

        The important difference from the previous implementation is that
        the original source bytes between the word elements are preserved.

        Only the following structural bytes are newly introduced:

            1. closing tag of the first line
            2. opening tag of the second line
            3. indentation/newline separating the two lines

        Existing word elements and all bytes surrounding them are copied
        directly from the original source.
        """

        debug = 0

        if line is None:
            return False

        if position < 0 or position > len(line.words):
            return False

        if position == 0 or position == len(line.words):
            # Nothing meaningful to split.
            #
            # Splitting before the first word or after the last word would
            # create an empty line.
            return False

        if debug:
            print("\n========== split_line DEBUG ==========")
            print("line.id:", line.id_bytes)
            print("position:", position)
            print("len(line.words):", len(line.words))

            for i, word in enumerate(line.words):
                print(
                    "word[{}]:".format(i),
                    word.id_bytes,
                    repr(
                        word.text_bytes.decode(
                            self.source_encoding,
                            errors="replace",
                        )
                    ),
                    "element_byte_range:",
                    word.element_byte_range,
                )

            print("======================================")

        # ---------------------------------------------------------
        # Identify the words on each side of the split.
        # ---------------------------------------------------------

        left_word = line.words[position - 1]
        right_word = line.words[position]

        if left_word is None or right_word is None:
            return False

        # ---------------------------------------------------------
        # Extract the complete original line element.
        # ---------------------------------------------------------

        line_start = line.element_byte_range[0]
        line_end = line.element_byte_range[1]

        line_element = self.source_bytes[
            line_start:
            line_end
        ]

        if not line_element:
            return False

        # ---------------------------------------------------------
        # Locate the word boundaries relative to the line element.
        #
        # These are absolute byte offsets in source_bytes.
        #
        # We convert them to offsets relative to line_element.
        # ---------------------------------------------------------

        left_word_end = (
            left_word.element_byte_range[1]
            - line_start
        )

        right_word_start = (
            right_word.element_byte_range[0]
            - line_start
        )

        if (
            left_word_end < 0
            or right_word_start < 0
            or left_word_end > len(line_element)
            or right_word_start > len(line_element)
        ):
            print(
                "split_line: word byte ranges are outside "
                "the line element"
            )
            return False

        if left_word_end > right_word_start:
            print(
                "split_line: invalid word ordering:",
                left_word_end,
                right_word_start,
            )
            return False

        # ---------------------------------------------------------
        # Find the original opening tag.
        #
        # Example:
        #
        #     <span class="ocr_line" id="line_1">
        #
        # We preserve all original attributes and formatting.
        # ---------------------------------------------------------

        open_end = line_element.find(b">")

        if open_end < 0:
            print(
                "split_line: could not find line opening tag"
            )
            return False

        opening_tag = line_element[:open_end + 1]

        first_opening_tag = opening_tag

        # ---------------------------------------------------------
        # Find the original closing tag.
        #
        # We assume the line is represented by a normal paired
        # element:
        #
        #     <span ...>...</span>
        #
        # Use the final closing tag because word elements may also
        # contain </span>.
        # ---------------------------------------------------------

        close_start = line_element.rfind(b"</span>")

        if close_start < 0:
            print("split_line: could not find line closing tag")
            return False

        closing_tag = line_element[close_start:]

        # ---------------------------------------------------------
        # Generate the ID for the new line.
        # ---------------------------------------------------------

        # new_line_id = self._make_unique_id(b"line")
        new_line_id = self._make_split_line_id(line)

        # ---------------------------------------------------------
        # Create the opening tag for the second line.
        #
        # Preserve the complete original opening tag and replace
        # only the old line ID.
        # ---------------------------------------------------------

        old_id = line.id_bytes

        id_pos = opening_tag.find(old_id)

        if id_pos < 0:
            print(
                "split_line: could not find original line ID "
                "inside opening tag"
            )
            return False

        second_opening_tag = (
            opening_tag[:id_pos]
            + new_line_id
            + opening_tag[id_pos + len(old_id):]
        )

        # Line bboxes become invalid after splitting.
        # line bboxes are optional, so we simply remove them
        first_opening_tag = self._remove_bbox_from_element(first_opening_tag)
        second_opening_tag = self._remove_bbox_from_element(second_opening_tag)

        # ---------------------------------------------------------
        # Preserve the original source bytes around the split.
        #
        # IMPORTANT:
        #
        #     left_content
        #
        # contains everything from the beginning of the line
        # through the end of the last word on the left.
        #
        # Therefore all original whitespace, indentation, comments,
        # etc. before and inside the first part are preserved.
        # ---------------------------------------------------------

        left_content = line_element[
            open_end + 1:
            left_word_end
        ]

        # ---------------------------------------------------------
        # Preserve everything from the beginning of the first word
        # on the right through the end of the original line content.
        #
        # This preserves all original bytes after the split.
        # ---------------------------------------------------------

        right_content = line_element[
            right_word_start:
            close_start
        ]

        # ---------------------------------------------------------
        # Determine indentation.
        #
        # We use the indentation that was present immediately before
        # the original line opening tag.
        #
        # Since line.element_byte_range starts at '<', this is not
        # available inside line_element itself.
        #
        # For now, preserve the existing style used by your document.
        # This can later be made fully automatic.
        # ---------------------------------------------------------

        first_line = (
            first_opening_tag
            + left_content
            + b"\n     "
            + closing_tag
        )

        second_line = (
            second_opening_tag
            + b"\n      "
            + right_content
            + closing_tag
        )

        # ---------------------------------------------------------
        # Build the replacement.
        #
        # The first line gets its closing tag.
        #
        # The second line gets:
        #
        #     newline
        #     indentation
        #     <span ...>
        #
        # The contents of both sides are copied from the original
        # source bytes.
        # ---------------------------------------------------------

        new_element = (
            first_line
            + b"\n     "
            + second_line
        )

        if debug:
            print("split_line: opening_tag:", opening_tag)
            print("split_line: second_opening_tag:", second_opening_tag)
            print("split_line: closing_tag:", closing_tag)
            print("split_line: left_word:", left_word.id_bytes)
            print("split_line: right_word:", right_word.id_bytes)
            print("split_line: left_content:", repr(left_content))
            print("split_line: right_content:", repr(right_content))
            print("split_line: new_element:", repr(new_element))

        # ---------------------------------------------------------
        # Replace only the original line element.
        # ---------------------------------------------------------

        self._replace_element_range(
            line.element_byte_range,
            new_element,
        )

        self.rebuild_model()

        return True

    @print_exceptions
    def merge_paragraphs(
            self,
            left: HocrParagraph,
            right: HocrParagraph,
        ) -> bool:
        """
        Merge two adjacent paragraphs.
        """

        if left is None or right is None:
            return False

        found = False

        for i in range(len(self.paragraphs) - 1):
            if (
                self.paragraphs[i] is left
                and self.paragraphs[i + 1] is right
            ):
                found = True
                break

        if not found:
            return False

        left_element = self.source_bytes[left.element_byte_range[0]:left.element_byte_range[1]]
        right_element = self.source_bytes[right.element_byte_range[0]:right.element_byte_range[1]]

        # ---------------------------------------------------------
        # Extract and update left opening tag.
        #
        # The left paragraph survives the merge, so its ID remains.
        # The bbox is removed because the merged paragraph has
        # different geometry.
        # ---------------------------------------------------------

        left_open_end = left_element.find(b">")

        if left_open_end < 0:
            return False

        left_opening_tag = left_element[:left_open_end + 1]

        # Paragraph bboxes become invalid after merging.
        # paragraph bboxes are optional, so we simply remove them
        left_opening_tag = self._remove_bbox_from_element(left_opening_tag)

        # ---------------------------------------------------------
        # Extract paragraph contents.
        # ---------------------------------------------------------

        left_inner = self._extract_element_inner_html(left_element, b"p")
        right_inner = self._extract_element_inner_html(right_element, b"p")

        merged = (
            left_opening_tag
            + left_inner.rstrip()
            + right_inner
            + b"</p>"
        )

        self._replace_element_range(
            (
                left.element_byte_range[0],
                right.element_byte_range[1],
            ),
            merged,
        )

        self.rebuild_model()

        return True

    @print_exceptions
    def split_paragraph(
            self,
            paragraph: HocrParagraph,
            position: int,
        ) -> bool:
        """
        Split a paragraph after `position` lines.

        The original source bytes are preserved as much as possible.

        For example:

            <p id="p1">
                <span class="ocr_line" id="line1">...</span>
                <span class="ocr_line" id="line2">...</span>
                <span class="ocr_line" id="line3">...</span>
            </p>

        with position=2 becomes:

            <p id="p1">
                <span class="ocr_line" id="line1">...</span>
                <span class="ocr_line" id="line2">...</span>
            </p>
            <p id="p2">
                <span class="ocr_line" id="line3">...</span>
            </p>

        The line contents and all whitespace between lines are copied directly
        from the original source_bytes. Only the paragraph boundary and the
        second paragraph ID are newly generated.
        """

        debug = 0

        if paragraph is None:
            return False

        if position <= 0:
            return False

        if position >= len(paragraph.lines):
            return False

        first_lines = paragraph.lines[:position]
        second_lines = paragraph.lines[position:]

        if not first_lines or not second_lines:
            return False

        source = self.source_bytes

        # ---------------------------------------------------------
        # Paragraph source range
        # ---------------------------------------------------------

        paragraph_start = paragraph.element_byte_range[0]
        paragraph_end = paragraph.element_byte_range[1]

        paragraph_element = source[paragraph_start:paragraph_end]

        # ---------------------------------------------------------
        # Find paragraph opening tag
        # ---------------------------------------------------------

        open_end = paragraph_element.find(b">")

        if open_end < 0:
            return False

        opening_tag = paragraph_element[:open_end + 1]

        # Absolute byte position immediately after the opening tag.
        content_start = paragraph_start + open_end + 1

        if debug:
            print("split_paragraph: opening_tag:", opening_tag)

        # ---------------------------------------------------------
        # Find original paragraph closing tag
        # ---------------------------------------------------------

        close_start = paragraph_element.rfind(b"</")

        if close_start < 0:
            return False

        # Absolute byte position of the closing tag.
        closing_start = paragraph_start + close_start

        # Preserve the actual original closing tag.
        closing_tag = source[closing_start:paragraph_end]

        if debug:
            print("split_paragraph: closing_tag:", closing_tag)

        # ---------------------------------------------------------
        # Determine split byte ranges from original source
        # ---------------------------------------------------------

        #
        # first_content:
        #
        # Everything between the paragraph opening tag and the end
        # of the last line belonging to the first paragraph.
        #
        first_content_start = content_start
        first_content_end = first_lines[-1].element_byte_range[1]

        first_content = source[first_content_start:first_content_end]

        #
        # second_content:
        #
        # Everything from the beginning of the first line belonging
        # to the second paragraph through the original paragraph's
        # closing tag.
        #
        second_content_start = second_lines[0].element_byte_range[0]
        second_content_end = closing_start

        second_content = source[second_content_start:second_content_end]

        if debug:
            print(
                "split_paragraph: first_content_range:",
                (first_content_start, first_content_end),
            )
            print(
                "split_paragraph: second_content_range:",
                ( second_content_start, second_content_end),
            )
            print("split_paragraph: first_content:", first_content)
            print("split_paragraph: second_content:", second_content)

        # ---------------------------------------------------------
        # Generate second paragraph ID
        # ---------------------------------------------------------

        new_id = self._make_split_paragraph_id(paragraph)

        old_id = paragraph.id_bytes

        id_pos = opening_tag.find(old_id)

        first_opening_tag = opening_tag
        second_opening_tag = opening_tag

        if id_pos >= 0:
            second_opening_tag = (
                opening_tag[:id_pos]
                + new_id
                + opening_tag[id_pos + len(old_id):]
            )

        if debug:
            print("split_paragraph: first_opening_tag:", first_opening_tag)
            print("split_paragraph: second_opening_tag:", second_opening_tag)

        # Paragraph bboxes become invalid after splitting.
        # paragraph bboxes are optional, so we simply remove them
        first_opening_tag = self._remove_bbox_from_element(first_opening_tag)
        second_opening_tag = self._remove_bbox_from_element(second_opening_tag)

        # ---------------------------------------------------------
        # Build first paragraph
        # ---------------------------------------------------------

        #
        # first_content already contains the original whitespace
        # before and between the lines.
        #
        # We only need to add the original paragraph closing tag.
        #
        first_paragraph = (
            first_opening_tag
            + first_content
            + b"\n    "
            + closing_tag
        )

        # ---------------------------------------------------------
        # Build second paragraph
        # ---------------------------------------------------------

        #
        # second_content starts exactly at the first line that was
        # moved to the second paragraph.
        #
        # Therefore all original whitespace before/between those
        # lines is preserved.
        #
        second_paragraph = (
            second_opening_tag
            + b"\n     "
            + second_content
            + closing_tag
        )

        # ---------------------------------------------------------
        # Preserve the original indentation between paragraphs
        # ---------------------------------------------------------

        #
        # The bytes immediately before the original paragraph are
        # not part of paragraph.element_byte_range, so indentation
        # between the two generated paragraphs has to be supplied.
        #
        # This matches the indentation style used by split_line.
        #
        new_element = (
            first_paragraph
            + b"\n    "
            + second_paragraph
        )

        if debug:
            print("split_paragraph: first_paragraph:", first_paragraph)
            print("split_paragraph: second_paragraph:", second_paragraph)
            print("split_paragraph: new_element:", new_element)

        # ---------------------------------------------------------
        # Replace original paragraph
        # ---------------------------------------------------------

        self._replace_element_range(paragraph.element_byte_range, new_element)

        self.rebuild_model()

        return True

    @print_exceptions
    def get_word_by_id(self, word_id: bytes) -> Optional[Word]:
        for paragraph in self.paragraphs:
            for line in paragraph.lines:
                for word in line.words:
                    if word.id_bytes == word_id:
                        return word
        return None

    @print_exceptions
    def _remove_bbox_from_title(
            self,
            title_value: str,
        ) -> str:
        """
        Remove the bbox field from an hOCR title attribute.

        Preserves all other title fields and their order.

        Examples:

            "bbox 1 2 3 4"
                -> ""

            "bbox 1 2 3 4; baseline 0.1 2"
                -> "baseline 0.1 2"

            "baseline 0.1 2; bbox 1 2 3 4; x_size 20"
                -> "baseline 0.1 2; x_size 20"
        """

        parts = [
            part.strip()
            for part in title_value.split(";")
        ]

        parts = [
            part
            for part in parts
            if part
            and not part.startswith("bbox ")
        ]

        return "; ".join(parts)

    @print_exceptions
    def _remove_bbox_from_element(
            self,
            element: bytes,
        ) -> bytes:
        """
        Remove the bbox field from an element's title attribute.

        If the title contains other fields, only bbox is removed.

        If bbox is the only title field, the complete title attribute
        is removed.

        Works with both single and double quoted title attributes.
        """

        # ---------------------------------------------------------
        # Find title attribute.
        #
        # We intentionally operate on raw bytes so that all
        # unrelated source formatting remains untouched.
        # ---------------------------------------------------------

        title_match = re.search(
            rb"\btitle\s*=\s*(['\"])(.*?)\1",
            element,
            flags=re.DOTALL,
        )

        if title_match is None:
            return element

        quote = title_match.group(1)
        old_title = title_match.group(2)

        try:
            old_title_str = old_title.decode(
                self.source_encoding,
                errors="replace",
            )
        except Exception:
            return element

        new_title_str = self._remove_bbox_from_title(
            old_title_str
        )

        # Nothing changed.
        if new_title_str == old_title_str:
            return element

        # ---------------------------------------------------------
        # bbox was the only title field.
        #
        # Remove the complete title attribute.
        #
        # Example:
        #
        #     <p ... title="bbox 1 2 3 4">
        #
        # becomes:
        #
        #     <p ...>
        # ---------------------------------------------------------

        if not new_title_str:
            attr_start = title_match.start()
            attr_end = title_match.end()

            # Remove one preceding whitespace character as well,
            # so:
            #
            #     id='x' title="bbox ..."
            #
            # becomes:
            #
            #     id='x'
            #
            # rather than:
            #
            #     id='x' 
            #
            if (
                attr_start > 0
                and element[attr_start - 1:attr_start]
                in (b" ", b"\t")
            ):
                attr_start -= 1

            return (
                element[:attr_start]
                + element[attr_end:]
            )

        # ---------------------------------------------------------
        # bbox is only one of several title fields.
        #
        # Preserve the original quote style.
        # ---------------------------------------------------------

        new_title = new_title_str.encode(
            self.source_encoding
        )

        value_start = title_match.start(2)
        value_end = title_match.end(2)

        return (
            element[:value_start]
            + new_title
            + element[value_end:]
        )

    @print_exceptions
    def _remove_bbox_from_element_range(
            self,
            element_range: Tuple[int, int],
        ) -> bytes:
        """
        Extract an element from source_bytes and remove its bbox.
        """

        start, end = element_range

        element = self.source_bytes[start:end]

        return self._remove_bbox_from_element(element)

    @print_exceptions
    def _union_bbox(
            self,
            left_bbox: Optional[Tuple[int, int, int, int]],
            right_bbox: Optional[Tuple[int, int, int, int]],
        ) -> Optional[Tuple[int, int, int, int]]:
        """
        Return the smallest bbox containing both input bboxes.
        """

        if left_bbox and right_bbox:
            return (
                min(left_bbox[0], right_bbox[0]),
                min(left_bbox[1], right_bbox[1]),
                max(left_bbox[2], right_bbox[2]),
                max(left_bbox[3], right_bbox[3]),
            )

        if left_bbox:
            return left_bbox

        if right_bbox:
            return right_bbox

        return None

# ------------------------ helpers ------------------------

# xhtml example:
r"""
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"
    "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
"""

def _detect_lang(source: bytes) -> str:
    head = source.lstrip()[:2048]
    if head.startswith(b"<?xml"):
        return "xml"
    # Heuristic: XHTML often has xmlns with xhtml URI on <html> or top-level
    if b"http://www.w3.org/1999/xhtml" in head or b"xmlns=" in head:
        return "xml"
    return "html"


def _strip_quote_range(start_byte: int, end_byte: int, raw: bytes) -> Tuple[int, int]:
    """Given a node's byte [start_byte,end_byte) and its raw text, return the inner range
    without surrounding quotes if present."""
    # note: type(b'"'[0]) == int
    if (
        len(raw) >= 2 and
        (
            (raw[0] == b'"'[0] and raw[-1] == b'"'[0]) or
            (raw[0] == b"'"[0] and raw[-1] == b"'"[0])
        )
    ):
        return start_byte + 1, end_byte - 1
    return start_byte, end_byte


def _class_has(class_attr: bytes, token: bytes) -> bool:
    return token in class_attr.split()


def split_bbox(
    bbox,
    left_length,
    right_length,
):
    x1, y1, x2, y2 = bbox

    total = left_length + right_length

    if total <= 0:
        return bbox, bbox

    split_x = x1 + int(
        (x2 - x1)
        * left_length
        / total
    )

    return (
        (x1, y1, split_x, y2),
        (split_x, y1, x2, y2),
    )
