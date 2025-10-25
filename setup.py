from setuptools import setup

with open('requirements.txt') as f:
    install_requires = f.read().splitlines()

setup(
  name='hocr-editor-qt',
  py_modules=[
    'color_helpers',
    'git_helpers',
    'hocr_parser',
    'hocr_source_editor',
    'resizable_rect_item',
  ],
  version='0.1.0',
  author='Milan Hauth <milahu@gmail.com>',
  description='graphical HOCR editor to produce minimal diffs for proofreading of tesseract OCR output',
  install_requires=install_requires,
  scripts=[
    'hocr-editor.py',
  ],
)
