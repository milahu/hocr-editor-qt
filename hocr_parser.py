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
    src = Path("doc.hocr.html").read_text(encoding="utf-8")
    hp = HocrParser(src)
    words = hp.find_words()  # list[Word]
    hp.update(word_id=words[0].id, text="NEW")
    hp.update(word_id=words[0].id, bbox=(10,20,100,60), x_wconf=95)
    new_src = hp.source  # updated HTML/XML string

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
import re

from tree_sitter import Parser
from tree_sitter_language_pack import get_language

HTML_LANG = get_language("html")
XML_LANG = get_language("xml")

# ------------------------ utilities ------------------------


_TITLE_BBOX_RE = re.compile(r"bbox\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)", re.IGNORECASE)
_TITLE_XWCONF_RE = re.compile(r"x_wconf\s+(-?\d+)", re.IGNORECASE)


def _parse_title(title_value: str):
    """Return (bbox_tuple_or_None, x_wconf_or_None) from the raw 'title' value (no quotes)."""
    s = (title_value or "").strip()
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
    if bbox is None and "bbox" in s.lower():
        try:
            parts = re.split(r"[;\s]+", s)
            for i, p in enumerate(parts):
                if p.lower() == "bbox" and i + 4 < len(parts):
                    bx = tuple(map(int, parts[i+1:i+5]))
                    if len(bx) == 4:
                        bbox = bx
                        break
        except Exception:
            pass

    return bbox, xw


def _format_title(existing: str,
                  bbox: Optional[Tuple[int, int, int, int]] = None,
                  x_wconf: Optional[int] = None) -> str:
    """Merge bbox/x_wconf into an existing semicolon-separated title value.
    Preserves unknown fields and order (except we ensure bbox first if set).
    """
    existing = existing or ""
    parts = [p.strip() for p in existing.split(";")]
    # remove existing bbox/x_wconf from parts
    rest: List[str] = [p for p in parts if p and not p.startswith("bbox") and not p.startswith("x_wconf")]
    if bbox is not None:
        rest.insert(0, f"bbox {bbox[0]} {bbox[1]} {bbox[2]} {bbox[3]}")
    if x_wconf is not None:
        rest.append(f"x_wconf {x_wconf}")
    # remove duplicates and empty
    rest = [p for p in rest if p]
    return "; ".join(rest)


@dataclass
class Word:
    id: str
    text: str
    bbox: Optional[Tuple[int, int, int, int]]
    x_wconf: Optional[int]
    # raw title value (without surrounding quotes)
    title_value: str
    # precise byte ranges (start, end) in source_bytes
    text_range: Tuple[int, int]
    title_value_range: Tuple[int, int]
    id_value_range: Tuple[int, int]
    element_range: Tuple[int, int]


class HocrParser:
    def __init__(self, source: str):
        self.set_source(source)

    # ------------------------ public API ------------------------

    @property
    def is_xml(self) -> bool:
        return self._lang == "xml"

    def find_words(self) -> List[Word]:
        return list(self._index_words().values())

    def get_word(self, word_id: str) -> Optional[Word]:
        return self._index_words().get(word_id)

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
        idx = self._index_words()
        node = idx.get(word_id)
        if not node:
            return False

        changed = False

        # 1) text
        if text is not None and node.text_range:
            self._replace_range(node.text_range, text)
            changed = True
            # reindex to refresh ranges
            idx = self._index_words()
            node = idx.get(word_id) or node

        # 2) title merge (bbox/x_wconf)
        if bbox is not None or x_wconf is not None:
            current_title = self.source[node.title_value_range[0]:node.title_value_range[1]]
            new_title = _format_title(current_title, bbox=bbox, x_wconf=x_wconf)
            if new_title != current_title:
                self._replace_range(node.title_value_range, new_title)
                changed = True
                idx = self._index_words()
                node = idx.get(word_id) or node

        # 3) id change
        if new_id is not None and new_id != node.id:
            self._replace_range(node.id_value_range, new_id)
            changed = True

        return changed

    # ------------------------ core ------------------------

    def set_source(self, source: str):
        self.source = source
        self.source_bytes = source.encode("utf-8")
        self._lang = _detect_lang(source)
        lang = XML_LANG if self._lang == "xml" else HTML_LANG
        self.parser = Parser(lang)
        self.tree = self.parser.parse(self.source_bytes)
        self._cached_index: Optional[Dict[str, Word]] = None

    def _index_words(self) -> Dict[str, Word]:
        if self._cached_index is not None:
            return self._cached_index
        words: Dict[str, Word] = {}
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

    def _extract_word_html(self, element, sb: bytes) -> Optional[Word]:
        # element = start_tag, (text|element)*, end_tag
        # Find start_tag
        if not element.children or element.children[0].type != "start_tag":
            return None
        start_tag = element.children[0]

        tag_name = None
        attrs: Dict[str, Tuple[str, Tuple[int, int]]] = {}

        for ch in start_tag.children:
            t = ch.type
            if t == "tag_name":
                tag_name = sb[ch.start_byte:ch.end_byte].decode()
            elif t == "attribute":
                n, v, vr = self._read_html_attribute(ch, sb)
                if n:
                    attrs[n] = (v, vr)

        if (tag_name or "").lower() != "span":
            return None
        cls_val = attrs.get("class", ("", (0, 0)))[0]
        if not _class_has(cls_val, "ocrx_word"):
            return None

        # id & title
        id_val, id_range = attrs.get("id", ("", (0, 0)))
        title_val, title_range = attrs.get("title", ("", (0, 0)))

        # inner text: first 'text' child directly under element
        text_node = None
        for ch in element.children:
            if ch.type == "text":
                text_node = ch
                break
        if text_node is not None:
            text = sb[text_node.start_byte:text_node.end_byte].decode()
            text_range = (text_node.start_byte, text_node.end_byte)
        else:
            # empty span: zero-length before end_tag
            end_tag = element.children[-1]
            text = ""
            text_range = (end_tag.start_byte, end_tag.start_byte)

        bbox, xw = _parse_title(title_val)
        assert not (bbox is None), f"failed to parse bbox from title {title_val!r}"
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
        )

    def _read_html_attribute(self, attr_node, sb: bytes):
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
            return None, "", (attr_node.start_byte, attr_node.start_byte)

        name = sb[name_node.start_byte:name_node.end_byte].decode()
        raw = sb[value_node.start_byte:value_node.end_byte].decode()

        inner_start, inner_end = _strip_quote_range(value_node.start_byte, value_node.end_byte, raw)
        value = sb[inner_start:inner_end].decode()
        return name, value, (inner_start, inner_end)

    # ------------------------ extraction: XML ------------------------

    def _extract_word_xml(self, element, sb: bytes) -> Optional[Word]:
        # element -> STag, content?, ETag | EmptyElemTag
        stags = [c for c in element.children if c.type == "STag"]
        if not stags:
            return None
        st = stags[0]

        tag_name = None
        attrs: Dict[str, Tuple[str, Tuple[int, int]]] = {}
        for c in st.children:
            if c.type == "Name" and tag_name is None:
                tag_name = sb[c.start_byte:c.end_byte].decode()
            elif c.type == "Attribute":
                n, v, vr = self._read_xml_attribute(c, sb)
                if n:
                    attrs[n] = (v, vr)

        if (tag_name or "").lower() != "span":
            return None
        cls_val = attrs.get("class", ("", (0, 0)))[0]
        if not _class_has(cls_val, "ocrx_word"):
            return None

        id_val, id_range = attrs.get("id", ("", (0, 0)))
        title_val, title_range = attrs.get("title", ("", (0, 0)))

        # content text
        text = ""
        text_range: Tuple[int, int] = (element.start_byte, element.start_byte)
        contents = [c for c in element.children if c.type == "content"]
        if contents:
            # find first CharData as text node
            for sub in contents[0].children:
                if sub.type == "CharData":
                    text = sb[sub.start_byte:sub.end_byte].decode()
                    text_range = (sub.start_byte, sub.end_byte)
                    break

        bbox, xw = _parse_title(title_val)
        assert not (bbox is None), f"failed to parse bbox from title {title_val!r}"
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
        )

    def _read_xml_attribute(self, attr_node, sb: bytes):
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
            return None, "", (attr_node.start_byte, attr_node.start_byte)

        name = sb[name_node.start_byte:name_node.end_byte].decode()
        raw = sb[value_node.start_byte:value_node.end_byte].decode()

        inner_start, inner_end = _strip_quote_range(value_node.start_byte, value_node.end_byte, raw)
        value = sb[inner_start:inner_end].decode()
        return name, value, (inner_start, inner_end)

    # ------------------------ editing ------------------------

    def _replace_range(self, byte_range: Tuple[int, int], new_content_utf8: str):
        start, end = byte_range
        before = self.source_bytes[:start]
        after = self.source_bytes[end:]
        insert = new_content_utf8.encode("utf-8")
        self.source_bytes = before + insert + after
        self.source = self.source_bytes.decode("utf-8", errors="replace")
        # Reparse and clear cache
        self.tree = self.parser.parse(self.source_bytes)
        self._cached_index = None


# ------------------------ helpers ------------------------

def _detect_lang(source: str) -> str:
    head = source.lstrip()[:2048]
    if head.startswith("<?xml"):
        return "xml"
    # Heuristic: XHTML often has xmlns with xhtml URI on <html> or top-level
    if "http://www.w3.org/1999/xhtml" in head or "xmlns=" in head.split("\n", 1)[0]:
        return "xml"
    return "html"


def _strip_quote_range(start: int, end: int, raw: str) -> Tuple[int, int]:
    """Given a node's byte [start,end) and its raw text, return the inner range
    without surrounding quotes if present."""
    if len(raw) >= 2 and ((raw[0] == '"' and raw[-1] == '"') or (raw[0] == "'" and raw[-1] == "'")):
        return start + 1, end - 1
    return start, end


def _class_has(class_attr: str, token: str) -> bool:
    return token in class_attr.split()
