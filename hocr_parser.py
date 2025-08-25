# hocr_parser.py
from tree_sitter import Parser
from tree_sitter_language_pack import get_language


HTML_LANG = get_language("html")
XML_LANG = get_language("xml")


class HocrParser:
    """
    Unified hOCR parser for both HTML and XHTML (XML).
    Extracts ocrx_word spans with id, bbox (from title), and text.
    """

    def __init__(self, source: str):
        self.source = source
        self.source_bytes = source.encode("utf-8")

        # Detect HTML vs XHTML
        if source.lstrip().startswith("<?xml") or "xmlns=" in source.split("\n", 1)[0]:
            self.lang = "xml"
            self.parser = Parser(XML_LANG)
        else:
            self.lang = "html"
            self.parser = Parser(HTML_LANG)

        self.tree = self.parser.parse(self.source_bytes)

    def find_words(self):
        """Return list of {id, title, text, bbox} dicts for each ocrx_word span."""
        words = []
        cursor = self.tree.walk()
        stack = [cursor.node]

        while stack:
            node = stack.pop()

            if self.lang == "html":
                if node.type == "element":
                    tagname, attrs, text_node = None, {}, None
                    for c in node.children:
                        if c.type == "start_tag":
                            for cc in c.children:
                                if cc.type == "tag_name":
                                    tagname = self._text(cc)
                                elif cc.type == "attribute":
                                    n, v = self._attr(cc)
                                    if n and v is not None:
                                        attrs[n] = v
                        elif c.type == "text":
                            text_node = c

                    if tagname == "span" and attrs.get("class") == "ocrx_word":
                        words.append({
                            "id": attrs.get("id"),
                            "title": attrs.get("title"),
                            "bbox": self._parse_bbox(attrs.get("title")),
                            "text": self._text(text_node) if text_node else "",
                            "node": node,
                        })

            else:  # XML
                if node.type == "element":
                    stags = [c for c in node.children if c.type == "STag"]
                    contents = [c for c in node.children if c.type == "content"]

                    if stags:
                        attrs, tagname = {}, None
                        for sc in stags[0].children:
                            if sc.type == "Name":
                                tagname = self._text(sc)
                            elif sc.type == "Attribute":
                                n, v = self._xml_attr(sc)
                                if n and v is not None:
                                    attrs[n] = v

                        if tagname == "span" and attrs.get("class") == "ocrx_word":
                            text = ""
                            for cc in contents:
                                for sub in cc.children:
                                    if sub.type == "CharData":
                                        text += self._text(sub)

                            words.append({
                                "id": attrs.get("id"),
                                "title": attrs.get("title"),
                                "bbox": self._parse_bbox(attrs.get("title")),
                                "text": text,
                                "node": node,
                            })

            stack.extend(node.children)

        return words

    # ---------------- helpers ----------------
    def _text(self, node):
        return self.source_bytes[node.start_byte:node.end_byte].decode("utf-8") if node else ""

    def _attr(self, attr_node):
        """HTML attribute node -> (name, value)"""
        name_node = attr_node.child_by_field_name("name")
        value_node = attr_node.child_by_field_name("value")
        if not name_node or not value_node:
            return None, None
        n = self._text(name_node)
        v = self._text(value_node).strip('"')
        return n, v

    def _xml_attr(self, attr_node):
        """XML Attribute node -> (name, value)"""
        name_node = None
        value_node = None
        for c in attr_node.children:
            if c.type == "Name":
                name_node = c
            elif c.type == "AttValue":
                value_node = c
        if not name_node or not value_node:
            return None, None
        n = self._text(name_node)
        v = self._text(value_node).strip('"')
        return n, v

    def _parse_bbox(self, title):
        """Extract bbox from title='bbox x0 y0 x1 y1; x_wconf 95'"""
        if not title or "bbox" not in title:
            return None
        try:
            parts = title.split()
            i = parts.index("bbox")
            return tuple(map(int, parts[i+1:i+5]))
        except Exception:
            return None
