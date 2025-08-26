#!/usr/bin/env python3

# source https://github.com/milahu/resizable-rect-item-pyside
# based on https://stackoverflow.com/a/34442054/10440128

import sys

from PySide6.QtCore import (
    Qt,
    QRectF,
    QPointF,
)
from PySide6.QtGui import (
    QBrush,
    QPainterPath,
    QPainter,
    QColor,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QGraphicsRectItem,
    QApplication,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsItem,
)


class ResizableRectItem(QGraphicsRectItem):

    handleTopLeft = 0
    handleTopMiddle = 1
    handleTopRight = 2
    handleMiddleLeft = 3
    handleMiddleRight = 4
    handleBottomLeft = 5
    handleBottomMiddle = 6
    handleBottomRight = 7

    handleSizeRel = 0.25
    # handleSizeRel = 0.2
    # handleSizeRel = 0.125

    handleCursors = (
        Qt.SizeFDiagCursor, # handleTopLeft
        Qt.SizeVerCursor, # handleTopMiddle
        Qt.SizeBDiagCursor, # handleTopRight
        Qt.SizeHorCursor, # handleMiddleLeft
        Qt.SizeHorCursor, # handleMiddleRight
        Qt.SizeBDiagCursor, # handleBottomLeft
        Qt.SizeVerCursor, # handleBottomMiddle
        Qt.SizeFDiagCursor, # handleBottomRight
    )

    def __init__(
            self,
            *args,
            move_done_cb=None,
            resize_done_cb=None,
        ):
        """
        Initialize the shape.
        """
        super().__init__(*args)
        self.move_done_cb = move_done_cb or (lambda *args: None)
        self.resize_done_cb = resize_done_cb or (lambda *args: None)
        self.handleSelected = None
        self.mousePressPos = None
        self.mousePressRect = None
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        # self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.ItemIsFocusable, True)
        self.initHandles()
        # Executed when the mouse is being moved over the item while being pressed.
        self.mouseMoveEvent = self.mouseMoveEventCenter
        self.mouseMoveEventByHandle = (
            self.mouseMoveEventTopLeft,
            self.mouseMoveEventTopMiddle,
            self.mouseMoveEventTopRight,
            self.mouseMoveEventMiddleLeft,
            self.mouseMoveEventMiddleRight,
            self.mouseMoveEventBottomLeft,
            self.mouseMoveEventBottomMiddle,
            self.mouseMoveEventBottomRight,
        )
        debug = False
        # debug = True
        if debug:
            self.paint = self.paintDebug

    def handleAt(self, point):
        """
        Returns the resize handle below the given point.
        """
        for k, v, in enumerate(self.handles):
            if v.contains(point):
                return k
        return None

    def hoverMoveEvent(self, moveEvent):
        """
        Executed when the mouse moves over the shape (NOT PRESSED).
        """
        if 1:
            handle = self.handleAt(moveEvent.pos())
            # cursor = Qt.SizeAllCursor if handle is None else self.handleCursors[handle]
            cursor = Qt.ArrowCursor if handle is None else self.handleCursors[handle]
            self.setCursor(cursor)
        super().hoverMoveEvent(moveEvent)

    def hoverLeaveEvent(self, moveEvent):
        """
        Executed when the mouse leaves the shape (NOT PRESSED).
        """
        self.setCursor(Qt.ArrowCursor)
        super().hoverLeaveEvent(moveEvent)

    def mousePressEvent(self, mouseEvent):
        """
        Executed when the mouse is pressed on the item.
        """
        self.handleSelected = self.handleAt(mouseEvent.pos())
        self.mousePressRect = self.boundingRect()
        self.mousePressScenePos = self.scenePos()
        if self.handleSelected is not None:
            # resizing
            self.mousePressPos = mouseEvent.pos()
            self.mouseMoveEvent = self.mouseMoveEventByHandle[self.handleSelected]
        else:
            # moving
            self.mouseMoveEvent = self.mouseMoveEventCenter
        super().mousePressEvent(mouseEvent)

    def mouseReleaseEvent(self, mouseEvent):
        if self.handleSelected is not None:
            # resizing
            rect1 = self.mapRectToScene(self.mousePressRect)
            rect2 = self.mapRectToScene(self.rect())
            if rect1 != rect2:
                self.resize_done_cb(rect1, rect2)
        else:
            # moving
            pos1 = self.mousePressScenePos
            pos2 = self.scenePos()
            if pos1 != pos2:
                self.move_done_cb(pos1, pos2)
        super().mouseReleaseEvent(mouseEvent)
        self.handleSelected = None
        self.mousePressPos = None
        self.mousePressScenePos = None
        self.mousePressRect = None
        self.update()

    def boundingRect(self):
        """
        Returns the bounding rect of the shape (including the resize handles).
        """
        return self.rect()

    def initHandles(self):
        """
        Init resize handles.
        """
        self.handles = (
            QRectF(0, 0, 0, 0),
            QRectF(0, 0, 0, 0),
            QRectF(0, 0, 0, 0),
            QRectF(0, 0, 0, 0),
            QRectF(0, 0, 0, 0),
            QRectF(0, 0, 0, 0),
            QRectF(0, 0, 0, 0),
            QRectF(0, 0, 0, 0),
        )
        self.updateHandlesPos()

    def updateHandlesPos(self):
        """
        Update current resize handles according to the shape size and position.
        """
        b = self.boundingRect()
        s1 = self.handleSizeRel
        s2 = 1 - 2 * s1
        L = b.left()
        T = b.top()
        R = b.right()
        B = b.bottom()
        W = b.width()
        H = b.height()
        self.handles[self.handleTopLeft].setRect(L, T, s1 * W, s1 * H)
        self.handles[self.handleTopMiddle].setRect(L + s1 * W, T, s2 * W, s1 * H)
        self.handles[self.handleTopRight].setRect(R - s1 * W, T, s1 * W, s1 * H)
        self.handles[self.handleMiddleLeft].setRect(L, T + s1 * H, s1 * W, s2 * H)
        self.handles[self.handleMiddleRight].setRect(R - s1 * W, T + s1 * H, s1 * W, s2 * H)
        self.handles[self.handleBottomLeft].setRect(L, B - s1 * H, s1 * W, s1 * H)
        self.handles[self.handleBottomMiddle].setRect(L + s1 * W, B - s1 * H, s2 * W, s1 * H)
        self.handles[self.handleBottomRight].setRect(R - s1 * W, B - s1 * H, s1 * W, s1 * H)

    def mouseMoveEventCenter(self, mouseEvent):
        super().mouseMoveEvent(mouseEvent)

    def mouseMoveEventTopLeft(self, mouseEvent):
        """
        Perform shape interactive resize.
        """
        mousePos = mouseEvent.pos()
        boundingRect = self.boundingRect()
        rect = self.rect()

        self.prepareGeometryChange()

        fromX = self.mousePressRect.left()
        fromY = self.mousePressRect.top()
        toX = fromX + mousePos.x() - self.mousePressPos.x()
        toY = fromY + mousePos.y() - self.mousePressPos.y()
        boundingRect.setLeft(toX)
        boundingRect.setTop(toY)
        rect.setLeft(boundingRect.left())
        rect.setTop(boundingRect.top())
        self.setRect(rect)

        self.updateHandlesPos()

    def mouseMoveEventTopMiddle(self, mouseEvent):
        """
        Perform shape interactive resize.
        """
        mousePos = mouseEvent.pos()
        boundingRect = self.boundingRect()
        rect = self.rect()

        self.prepareGeometryChange()

        fromY = self.mousePressRect.top()
        toY = fromY + mousePos.y() - self.mousePressPos.y()
        boundingRect.setTop(toY)
        rect.setTop(boundingRect.top())
        self.setRect(rect)

        self.updateHandlesPos()

    def mouseMoveEventTopRight(self, mouseEvent):
        """
        Perform shape interactive resize.
        """
        mousePos = mouseEvent.pos()
        boundingRect = self.boundingRect()
        rect = self.rect()

        self.prepareGeometryChange()

        fromX = self.mousePressRect.right()
        fromY = self.mousePressRect.top()
        toX = fromX + mousePos.x() - self.mousePressPos.x()
        toY = fromY + mousePos.y() - self.mousePressPos.y()
        boundingRect.setRight(toX)
        boundingRect.setTop(toY)
        rect.setRight(boundingRect.right())
        rect.setTop(boundingRect.top())
        self.setRect(rect)

        self.updateHandlesPos()

    def mouseMoveEventMiddleLeft(self, mouseEvent):
        """
        Perform shape interactive resize.
        """
        mousePos = mouseEvent.pos()
        boundingRect = self.boundingRect()
        rect = self.rect()

        self.prepareGeometryChange()

        fromX = self.mousePressRect.left()
        toX = fromX + mousePos.x() - self.mousePressPos.x()
        boundingRect.setLeft(toX)
        rect.setLeft(boundingRect.left())
        self.setRect(rect)

        self.updateHandlesPos()

    def mouseMoveEventMiddleRight(self, mouseEvent):
        """
        Perform shape interactive resize.
        """
        mousePos = mouseEvent.pos()
        boundingRect = self.boundingRect()
        rect = self.rect()

        self.prepareGeometryChange()

        fromX = self.mousePressRect.right()
        toX = fromX + mousePos.x() - self.mousePressPos.x()
        boundingRect.setRight(toX)
        rect.setRight(boundingRect.right())
        self.setRect(rect)

        self.updateHandlesPos()

    def mouseMoveEventBottomLeft(self, mouseEvent):
        """
        Perform shape interactive resize.
        """
        mousePos = mouseEvent.pos()
        boundingRect = self.boundingRect()
        rect = self.rect()

        self.prepareGeometryChange()

        fromX = self.mousePressRect.left()
        fromY = self.mousePressRect.bottom()
        toX = fromX + mousePos.x() - self.mousePressPos.x()
        toY = fromY + mousePos.y() - self.mousePressPos.y()
        boundingRect.setLeft(toX)
        boundingRect.setBottom(toY)
        rect.setLeft(boundingRect.left())
        rect.setBottom(boundingRect.bottom())
        self.setRect(rect)

        self.updateHandlesPos()

    def mouseMoveEventBottomMiddle(self, mouseEvent):
        """
        Perform shape interactive resize.
        """
        mousePos = mouseEvent.pos()
        boundingRect = self.boundingRect()
        rect = self.rect()

        self.prepareGeometryChange()

        fromY = self.mousePressRect.bottom()
        toY = fromY + mousePos.y() - self.mousePressPos.y()
        boundingRect.setBottom(toY)
        rect.setBottom(boundingRect.bottom())
        self.setRect(rect)

        self.updateHandlesPos()

    def mouseMoveEventBottomRight(self, mouseEvent):
        """
        Perform shape interactive resize.
        """
        mousePos = mouseEvent.pos()
        boundingRect = self.boundingRect()
        rect = self.rect()

        self.prepareGeometryChange()

        fromX = self.mousePressRect.right()
        fromY = self.mousePressRect.bottom()
        toX = fromX + mousePos.x() - self.mousePressPos.x()
        toY = fromY + mousePos.y() - self.mousePressPos.y()
        boundingRect.setRight(toX)
        boundingRect.setBottom(toY)
        rect.setRight(boundingRect.right())
        rect.setBottom(boundingRect.bottom())
        self.setRect(rect)

        self.updateHandlesPos()

    def paintDebug(self, painter, option, widget=None):
        """
        Paint the node in the graphic view.
        """
        painter.setBrush(QBrush(QColor(255, 0, 0, 100)))
        painter.setPen(QPen(QColor(0, 0, 0), 1.0, Qt.SolidLine))
        painter.drawRect(self.rect())

        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(QColor(255, 0, 0, 255)))
        painter.setPen(Qt.NoPen)
        for handle, rect in enumerate(self.handles):
            if self.handleSelected is None or handle == self.handleSelected:
                painter.drawRect(rect)


def main():

    app = QApplication(sys.argv)

    grview = QGraphicsView()
    scene = QGraphicsScene()
    scene.setSceneRect(0, 0, 680, 459)

    scene.addPixmap(QPixmap('qt.png'))
    grview.setScene(scene)

    def move_done_cb(pos1, pos2):
        print(f"move_done_cb: {pos1} -> {pos2}")

    def resize_done_cb(rect1, rect2):
        print(f"resize_done_cb: {rect1} -> {rect2}")

    item = ResizableRectItem(
        0, 0, 300, 150,
        move_done_cb=move_done_cb,
        resize_done_cb=resize_done_cb,
    )
    scene.addItem(item)

    grview.fitInView(scene.sceneRect(), Qt.KeepAspectRatio)
    grview.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
