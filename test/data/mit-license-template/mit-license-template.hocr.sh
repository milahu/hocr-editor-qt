#!/bin/sh
src=mit-license-template.webp
dst=mit-license-template.hocr
lang=eng
tesseract $src - -l $lang hocr >$dst
