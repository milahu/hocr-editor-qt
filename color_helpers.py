from PySide6.QtCore import (
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QPalette,
)
from PySide6.QtWidgets import (
    QWidget,
)

def apply_dark_palette(widget: QWidget) -> None:
    themeColor = "#2a82da" # QColor(42, 130, 218)
    darkLightness = 25 # of 255
    darkerLightness = 0 # of 255

    pal = QPalette()
    darkColor = QColor(darkLightness, darkLightness, darkLightness)
    darkerColor = QColor(darkerLightness, darkerLightness, darkerLightness)
    disabledColor = QColor(127, 127, 127)

    # Window & general background
    pal.setColor(QPalette.Window, darkColor)
    pal.setColor(QPalette.WindowText, Qt.white)

    # Text editing areas
    pal.setColor(QPalette.Base, darkerColor)
    pal.setColor(QPalette.AlternateBase, darkColor)
    pal.setColor(QPalette.Text, Qt.white)
    pal.setColor(QPalette.Disabled, QPalette.Text, disabledColor)

    # Buttons
    pal.setColor(QPalette.Button, darkColor)
    pal.setColor(QPalette.ButtonText, Qt.white)
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, disabledColor)

    # Tooltips
    pal.setColor(QPalette.ToolTipBase, Qt.white)
    pal.setColor(QPalette.ToolTipText, Qt.white)

    # Special / status colors
    pal.setColor(QPalette.BrightText, Qt.red)
    pal.setColor(QPalette.Link, QColor(themeColor))

    # Highlighting
    pal.setColor(QPalette.Highlight, QColor(themeColor))
    pal.setColor(QPalette.HighlightedText, Qt.black)
    pal.setColor(QPalette.Disabled, QPalette.HighlightedText, disabledColor)

    widget.setPalette(pal)

    # Tooltip styling
    widget.setStyleSheet(f"""
        QToolTip {{
            color: #ffffff;
            background-color: rgb({darkLightness}, {darkLightness}, {darkLightness});
            border: 1px solid white;
        }}
    """)


def apply_light_palette(widget: QWidget) -> None:
    themeColor = "#2a82da" # QColor(42, 130, 218)
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("white"))
    pal.setColor(QPalette.WindowText, QColor("black"))
    pal.setColor(QPalette.Base, QColor("white"))
    pal.setColor(QPalette.AlternateBase, QColor("lightgray"))
    pal.setColor(QPalette.ToolTipBase, QColor("yellow"))
    pal.setColor(QPalette.ToolTipText, QColor("black"))
    pal.setColor(QPalette.Text, QColor("black"))
    pal.setColor(QPalette.Button, QColor("lightgray"))
    pal.setColor(QPalette.ButtonText, QColor("black"))
    pal.setColor(QPalette.BrightText, QColor("red"))
    pal.setColor(QPalette.Link, QColor(themeColor))
    pal.setColor(QPalette.Highlight, QColor(themeColor))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    widget.setPalette(pal)
    widget.setStyleSheet("")
