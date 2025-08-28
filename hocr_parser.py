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
    hp.update(word_id=words[0].id, text=b"NEW")
    hp.update(word_id=words[0].id, bbox=(10,20,100,60), x_wconf=95)
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
from typing import Dict, Iterable, List, Optional, Tuple
from typing import (
    Any,
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
    id: bytes
    text: bytes
    bbox: Optional[Tuple[int, int, int, int]]
    x_wconf: Optional[int]
    # raw title value (without surrounding quotes)
    title_value: Optional[bytes]
    # precise byte ranges (start, end) in source_bytes
    text_range: Tuple[int, int]
    title_value_range: Tuple[int, int]
    id_value_range: Tuple[int, int]
    element_range: Tuple[int, int]
    span_range: Tuple[int, int]
    # @print_exceptions
    # def __init__(self, *a, **k):
    #     super().__init__(*a, **k)
    #     assert isinstance(self.id, bytes)
    #     assert isinstance(self.text, bytes)
    #     assert isinstance(self.title_value, bytes)


class HocrParser:
    @print_exceptions
    def __init__(self, source_bytes: bytes):
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
                id=attrs.get(b"id", (b"", (0,0)))[0],
                text=b"",
                bbox=bbox,
                x_wconf=None,
                title_value=title_val,
                text_range=(0,0),
                title_value_range=title_range,
                id_value_range=attrs.get(b"id",(b"",(0,0)))[1],
                element_range=(element.start_byte, element.end_byte),
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
                id=attrs.get(b"id", (b"", (0,0)))[0],
                text=b"",
                bbox=bbox,
                x_wconf=None,
                title_value=title_val,
                text_range=(0,0),
                title_value_range=title_range,
                id_value_range=attrs.get(b"id",(None,(0,0)))[1],
                element_range=(element.start_byte, element.end_byte),
                span_range=(0,0),
            )

    @print_exceptions
    def get_word(self, word_id: str) -> Optional[Word]:
        return self._index_words().get(word_id)

    @print_exceptions
    def update(self,
               word_id: str,
               *,
               text: Optional[str] = None,
               bbox: Optional[Tuple[int, int, int, int]] = None,
               x_wconf: Optional[int] = None,
               new_id: Optional[str] = None) -> bool:
        """Apply one or more changes to a word by id using minimal diffs.
        Returns True if the word was found and something changed.
        """
        # print(f"parser.update: text {text!r} bbox {bbox!r}")
        idx = self._index_words()
        node = idx.get(word_id)
        if not node:
            return False

        changed = False

        # 1) text
        if text is not None and node.text_range:
            if debug_word_id and debug_word_id == word_id:
                old_text = self.source_bytes[node.text_range[0]:node.text_range[1]]
                print(f"word {word_id}: update: update text: {old_text!r} -> {text!r}")
            self._replace_range(node.text_range, text)
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
        if new_id is not None and new_id != node.id:
            self._replace_range(node.id_value_range, new_id)
            changed = True

        return changed

    @print_exceptions
    def update_by_span(
            self,
            span_start: int,
            *,
            text: Optional[str] = None,
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

        # 1) text
        if text is not None and word.text_range:
            old_text = self.source_bytes[word.text_range[0]:word.text_range[1]]
            print(f"word {word.id}: update_by_span: update text (by span): {old_text!r} -> {text!r}")
            self._replace_range(word.text_range, text)
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
                if debug_word_id and debug_word_id == word.id:
                    print(f"word {word.id}: update_by_span: update title: no change in attribute @ {word.title_value_range}: title = {current_title!r}")
            else:
                if debug_word_id and debug_word_id == word.id:
                    print(f"word {word.id}: update_by_span: update title: attribute @ {word.title_value_range}: title = {current_title!r}")
                    print(f"word {word.id}: update_by_span: update title: {current_title!r} -> {new_title!r}")
                self._replace_range(word.title_value_range, new_title)
                changed = True
                word = self.find_word_by_span_start(span_start) or word

        # 3) id change
        if new_id is not None and new_id != word.id:
            self._replace_range(word.id_value_range, new_id)
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
        self._cached_index: Optional[Dict[bytes, Word]] = None

    @print_exceptions
    def set_source_string(self, source: str, encoding=None):
        encoding = encoding or self.source_encoding
        assert isinstance(source, str)
        source_bytes = source.encode(encoding, errors="replace")
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
                        words[w.id] = w
            else:  # xml
                if n.type == "element":
                    w = self._extract_word_xml(n, sb)
                    if w:
                        words[w.id] = w
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
            text = sb[text_node.start_byte:text_node.end_byte]
            text_range = (text_node.start_byte, text_node.end_byte)
        else:
            # empty span: zero-length before end_tag
            text = b""
            text_range = (end_tag.start_byte, end_tag.start_byte)

        bbox, xw = _parse_title(title_val)
        if bbox is None:
            print(f"word {id_val!r}: failed to parse bbox from title {title_val!r}")
            return None
        assert not (bbox is None), f"word {id_val!r}: failed to parse bbox from title {title_val!r}"
        return Word(
            id=id_val,
            text=text,
            bbox=bbox,
            x_wconf=xw,
            title_value=title_val,
            text_range=text_range,
            title_value_range=title_range,
            id_value_range=id_range,
            element_range=(element.start_byte, element.end_byte),
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
        text = b""
        text_range: Tuple[int, int] = (element.start_byte, element.start_byte)
        contents = [c for c in element.children if c.type == "content"]
        if contents:
            # find first CharData as text node
            for sub in contents[0].children:
                if sub.type == "CharData":
                    text = sb[sub.start_byte:sub.end_byte]
                    text_range = (sub.start_byte, sub.end_byte)
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
        return Word(
            id=id_val,
            text=text,
            bbox=bbox,
            x_wconf=xw,
            title_value=title_val,
            text_range=text_range,
            title_value_range=title_range,
            id_value_range=id_range,
            element_range=(element.start_byte, element.end_byte),
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
        assert isinstance(new_bytes, bytes)
        assert isinstance(self.source_bytes, bytes)
        start, end = byte_range
        if debug:
            old_bytes = self.source_bytes[start:end]
            print(f"_replace_range: range {byte_range}: {old_bytes!r} -> {new_bytes!r}")
        before = self.source_bytes[:start]
        after = self.source_bytes[end:]
        insert = new_bytes
        self.source_bytes = before + insert + after
        # Reparse and clear cache
        self.tree = self.parser.parse(self.source_bytes)
        self._cached_index = None

    @print_exceptions
    def find_word_at_offset(self, pos: int) -> Optional[Word]:
        for word in self.find_words():
            if word.span_range[0] <= pos < word.span_range[1]:
                return word
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


def _strip_quote_range(start: int, end: int, raw: bytes) -> Tuple[int, int]:
    """Given a node's byte [start,end) and its raw text, return the inner range
    without surrounding quotes if present."""
    # note: type(b'"'[0]) == int
    if (
        len(raw) >= 2 and
        (
            (raw[0] == b'"'[0] and raw[-1] == b'"'[0]) or
            (raw[0] == b"'"[0] and raw[-1] == b"'"[0])
        )
    ):
        return start + 1, end - 1
    return start, end


def _class_has(class_attr: bytes, token: bytes) -> bool:
    return token in class_attr.split()
