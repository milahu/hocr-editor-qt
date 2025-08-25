create a "minimal diff" hocr-editor in python.  
hocr files are special html files, where words look like

```html
<span class='ocrx_word' id='word_1_19' title='bbox 966 360 1103 387; x_wconf 91'>secret.TV</span>
```

so the word position is stored in the bbox values in the title attribute

the hocr-editor should by based on ...  
the pyside6 library (qt for python),  
tree-sitter-html (concrete syntax tree parser needed for minimal-diff editing of the AST),  
maybe a qt html rendering widget, so i can re-use html-javascript hocr libraries  
(https://github.com/scribeocr/scribeocr ?)  
(https://github.com/kba/hocrjs ?)  
for rendering the document (document image + hocr nodes),  
but this could also be implemented in qt native code  
(this could be based on some qt SVG editor,  
which could be adapted from general-purpose SVG editor to special-purpose HOCR editor,  
because HOCR can also be seen as a subset of SVG.)

---

this fails to load words from the hocr file, it says "Loaded 0 ocrx_word spans" in the status bar

my hocr document is this

```html
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"
    "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
 <head>
  <title></title>
  <meta http-equiv="Content-Type" content="text/html;charset=utf-8"/>
  <meta name='ocr-system' content='tesseract 5.5.1' />
  <meta name='ocr-capabilities' content='ocr_page ocr_carea ocr_par ocr_line ocrx_word ocrp_dir ocrp_lang ocrp_wconf'/>
 </head>
 <body>
  <div class='ocr_page' id='page_1' title='image "../070-deskew/005.tiff"; bbox 0 0 1590 2487; ppageno 0; scan_res 300 300'>
   <div class='ocr_separator' id='block_1_1' title="bbox 2 619 7 2485"></div>
   <div class='ocr_carea' id='block_1_2' title="bbox 306 243 1187 334">
    <p class='ocr_par' id='par_1_1' lang='eng' title="bbox 306 243 1187 334">
     <span class='ocr_line' id='line_1_1' title="bbox 306 243 1187 334; baseline 0 -1; x_size 118.57143; x_descenders 29.642857; x_ascenders 29.642857">
      <span class='ocrx_word' id='word_1_1' title='bbox 306 245 486 334; x_wconf 92'>JAN</span>
      <span class='ocrx_word' id='word_1_2' title='bbox 536 245 713 333; x_wconf 92'>VAN</span>
      <span class='ocrx_word' id='word_1_3' title='bbox 767 243 1187 333; x_wconf 90'>HELSING</span>
     </span>
    </p>
   </div>
   <div class='ocr_carea' id='block_1_3' title="bbox 509 411 983 459">
    <p class='ocr_par' id='par_1_2' lang='eng' title="bbox 509 411 983 459">
     <span class='ocr_line' id='line_1_2' title="bbox 509 411 983 459; baseline -0.002 0; x_size 64.370369; x_descenders 16.092592; x_ascenders 16.092592">
      <span class='ocrx_word' id='word_1_4' title='bbox 509 411 702 459; x_wconf 79'>STEFAN</span>
      <span class='ocrx_word' id='word_1_5' title='bbox 731 411 983 459; x_wconf 79'>ERDMANN</span>
     </span>
    </p>
   </div>
   <div class='ocr_carea' id='block_1_4' title="bbox 291 773 1192 1054">
    <p class='ocr_par' id='par_1_3' lang='eng' title="bbox 291 773 1192 1054">
     <span class='ocr_caption' id='line_1_3' title="bbox 291 773 1192 1054; baseline -0.001 -2; x_size 371.16666; x_descenders 92.791664; x_ascenders 92.791664">
      <span class='ocrx_word' id='word_1_6' title='bbox 291 773 1192 1054; x_wconf 90'>WHISTLE</span>
     </span>
    </p>
   </div>
   <div class='ocr_carea' id='block_1_4_b' title="bbox 226 1102 1274 1454">
    <p class='ocr_par' id='par_1_3_b' lang='eng' title="bbox 226 1102 1274 1454">
     <span class='ocr_line' id='line_1_3_b' title="bbox 226 1102 1274 1454; baseline -0.001 -2; x_size 371.16666; x_descenders 92.791664; x_ascenders 92.791664">
      <span class='ocrx_word' id='word_1_6_b' title='bbox 226 1102 1274 1454; x_wconf 0'>BLOWER</span>
     </span>
    </p>
   </div>
   <div class='ocr_carea' id='block_1_5' title="bbox 239 1656 1260 1786">
    <p class='ocr_par' id='par_1_4' lang='eng' title="bbox 239 1656 1260 1786">
     <span class='ocr_line' id='line_1_4' title="bbox 297 1656 1201 1713; baseline 0 -9; x_size 55.402596; x_descenders 7.4025974; x_ascenders 10">
      <span class='ocrx_word' id='word_1_7' title='bbox 297 1657 429 1704; x_wconf 92'>Insider</span>
      <span class='ocrx_word' id='word_1_8' title='bbox 444 1666 508 1705; x_wconf 92'>aus</span>
      <span class='ocrx_word' id='word_1_9' title='bbox 524 1657 652 1713; x_wconf 92'>Politik,</span>
      <span class='ocrx_word' id='word_1_10' title='bbox 667 1657 878 1713; x_wconf 89'>Wirtschaft,</span>
      <span class='ocrx_word' id='word_1_11' title='bbox 895 1657 1050 1712; x_wconf 92'>Medizin,</span>
      <span class='ocrx_word' id='word_1_12' title='bbox 1066 1656 1201 1712; x_wconf 82'>Polizei,</span>
     </span>
     <span class='ocr_line' id='line_1_5' title="bbox 239 1728 1260 1786; baseline -0.001 -9; x_size 56.5; x_descenders 7.5; x_ascenders 10.5">
      <span class='ocrx_word' id='word_1_13' title='bbox 239 1729 501 1786; x_wconf 88'>Geheimdienst,</span>
      <span class='ocrx_word' id='word_1_14' title='bbox 517 1729 748 1777; x_wconf 91'>Bundeswehr</span>
      <span class='ocrx_word' id='word_1_15' title='bbox 763 1729 828 1777; x_wconf 92'>und</span>
      <span class='ocrx_word' id='word_1_16' title='bbox 844 1729 1020 1786; x_wconf 92'>Logentum</span>
      <span class='ocrx_word' id='word_1_17' title='bbox 1036 1729 1166 1785; x_wconf 93'>packen</span>
      <span class='ocrx_word' id='word_1_18' title='bbox 1182 1728 1260 1776; x_wconf 92'>aus!</span>
     </span>
    </p>
   </div>
   <div class='ocr_carea' id='block_1_6' title="bbox 510 2131 960 2168">
    <p class='ocr_par' id='par_1_5' lang='eng' title="bbox 510 2131 960 2168">
     <span class='ocr_line' id='line_1_6' title="bbox 510 2131 960 2168; baseline 0 -8; x_size 37; x_descenders 8; x_ascenders 7">
      <span class='ocrx_word' id='word_1_19' title='bbox 510 2131 960 2168; x_wconf 84'>amadeus-verlag.com</span>
     </span>
    </p>
   </div>
   <div class='ocr_photo' id='block_1_7' title="bbox 0 0 1590 2487"></div>
  </div>
 </body>
</html>
```

---

i also had to change `def _attribute_value_text_and_range`

```diff
- if ch.type == 'attribute_value':
+ if ch.type == 'quoted_attribute_value':
```

now the status bar says
"Loaded 20 ocrx_word spans"

but now i dont see any words in the GUI

---

lets go the native Qt route for better performance.
basically, every word is a box with text.
the box must be draggable (on the top-left corner)
and the box must be resizable (on the bottom-right corner).
the word text must be editable in-place (via doubleclick)

---

nice. but ...

the font-size is too small, the text is unreadable.

i cannot move words by clicking and dragging the top-left corner.

i cannot resize word bboxes by clicking and dragging the bottom-right corner.

when i click on a word, the right view does not update to show the id / text / bbox / x_wconf values of that word.

i had to replace

```diff
-from tree_sitter_languages import get_language # broken
+from tree_sitter_language_pack import get_language
```

and

```diff
-        self.parser = Parser()
-        self.parser.set_language(HTML_LANGUAGE)
+        self.parser = Parser(HTML_LANGUAGE)
```

---

can you make this work with both html and xhtml?

tree-sitter-html does not support xhtml, so i have to use tree-sitter-xml.

the parse tree from tree-sitter-xml looks like this

```
(document (prolog (XMLDecl (VersionNum) (EncName)) (doctypedecl (Name) (ExternalID (PubidLiteral) (SystemLiteral (URI))))) root: (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name)) (content (CharData) (element (STag (Name)) (ETag (Name))) (CharData) (element (EmptyElemTag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)))) (CharData) (element (EmptyElemTag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)))) (CharData) (element (EmptyElemTag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)))) (CharData)) (ETag (Name))) (CharData) (element (STag (Name)) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (content (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData) (element (STag (Name) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue)) (Attribute (Name) (AttValue))) (ETag (Name))) (CharData)) (ETag (Name))) (CharData)) (ETag (Name))) (CharData)) (ETag (Name))))
```

---

yes, add update functions in this parser module.
but the parser module is broken, find_words returns an empty list of words.

---

ok, now we need to integrate class Word with class WordItem

---

ImportError: cannot import name 'QGraphicsLineEdit' from 'PySide6.QtWidgets'

---

ok, now inspector_update_cb is missing

---

ok, now there is something wrong with `class HocrParser` because `word.bbox == None`

---

ok, now when i edit a word text and hit enter, i get a segfault

---

nope, i still get a segfault when i edit word text

---

nope, i still get a segfault when i edit word text

here is my full hocr-editor.py

---

ok, now i need a way to save the new hocr file. add a menubar with "file > save"

---

ok, now  when i edit a hocr file (change text or bbox) and save the file, there is no change in the file contents
