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
