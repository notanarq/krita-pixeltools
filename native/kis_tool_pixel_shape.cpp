/*
 *  SPDX-License-Identifier: GPL-2.0-or-later
 *
 *  kis_tool_pixel_shape.cpp - native Krita tool for pixel-exact shapes.
 *
 *  The whole point of this file is what happens in paintPath(): every pixel
 *  of the shape is applied with a single dab of the user's ACTIVE preset at
 *  pressure 1.0. We never build our own brush, so the user keeps their
 *  colour, their opacity and their pattern; we only decide WHERE the dabs go,
 *  which is the one thing Krita's native tools get wrong for pixel art.
 *
 *  Stroke lifecycle
 *  ----------------
 *    beginPrimaryAction   -> remember the snapped anchor, open the stroke
 *    continuePrimaryAction-> recompute the path for the live preview
 *    endPrimaryAction     -> commit the path into the stroke, end the stroke
 *
 *  The commit happens inside a stroke transaction, so the whole shape is ONE
 *  undo step: Ctrl+Z removes the entire stroke, not one dab.
 */

#include "kis_tool_pixel_shape.h"

#include <QPainter>
#include <QPointF>
#include <QVector>
#include <QWidget>

#include <KoCanvasBase.h>
#include <KoPointerEvent.h>
#include <KoViewConverter.h>

#include <kis_canvas2.h>
#include <kis_canvas_resource_provider.h>
#include <kis_config.h>
#include <kis_coordinates_converter.h>
#include <kis_debug.h>
#include <kis_paint_device.h>
#include <kis_paint_information.h>
#include <kis_painter.h>
#include <kis_paintop_preset.h>
#include <kis_painting_information_builder.h>

KisToolPixelShape::KisToolPixelShape(KoCanvasBase *canvas)
    : KisToolPaint(canvas, Qt::CrossCursor)
{
    setObjectName("KisToolPixelShape");
    setSupportOutline(true);
    // One pixel per dab: anything else lets the paintop interpolate between
    // dabs and reintroduce the sub-pixel drift this tool exists to remove.
    m_path.reserve(4096);
}

KisToolPixelShape::~KisToolPixelShape() = default;

void KisToolPixelShape::setShapeMode(ShapeMode mode)
{
    m_mode = mode;
}

QString KisToolPixelShape::quickHelp() const
{
    return i18n("Pixel Shape: drag to place an exact shape. Every pixel is "
                "snapped to the integer grid; no antialiasing drift.");
}

QPoint KisToolPixelShape::snapToPixel(const QPointF &imagePos) const
{
    // Krita's image coordinates are continuous: pixel (x, y) covers
    // [x, x+1) x [y, y+1). floor() gives the pixel index under the cursor.
    // We deliberately do NOT round(): rounding would make the boundary
    // depend on half-right/half-down bias and put the shape off by one.
    return QPoint(static_cast<int>(std::floor(imagePos.x())),
                  static_cast<int>(std::floor(imagePos.y())));
}

void KisToolPixelShape::beginPrimaryAction(KoPointerEvent *event)
{
    if (nodePaintAbility() != PAINT || !nodeEditable()) {
        event->ignore();
        return;
    }

    setMode(KisTool::PAINT_MODE);
    m_active = true;
    m_anchor = snapToPixel(event->point);
    m_current = m_anchor;

    rebuildPath();
    event->accept();
}

void KisToolPixelShape::continuePrimaryAction(KoPointerEvent *event)
{
    if (!m_active) {
        event->ignore();
        return;
    }

    m_current = snapToPixel(event->point);
    rebuildPath();

    // Repaint so the previous preview does not leave trails.
    canvas()->updateCanvas(QRectF(0, 0, canvas()->canvasWidget()->width(),
                                  canvas()->canvasWidget()->height()));
    event->accept();
}

void KisToolPixelShape::endPrimaryAction(KoPointerEvent *event)
{
    if (!m_active) {
        event->ignore();
        return;
    }

    m_current = snapToPixel(event->point);
    rebuildPath();

    paintPath();

    m_active = false;
    m_path.clear();
    event->accept();

    canvas()->updateCanvas(QRectF(0, 0, canvas()->canvasWidget()->width(),
                                  canvas()->canvasWidget()->height()));
}

void KisToolPixelShape::cancelStroke()
{
    m_active = false;
    m_path.clear();
}

void KisToolPixelShape::requestStrokeCancellation()
{
    cancelStroke();
}

void KisToolPixelShape::requestStrokeEnd()
{
    if (m_active) {
        m_active = false;
    }
}

void KisToolPixelShape::rebuildPath()
{
    m_path.clear();

    const pixeltools::Point a{m_anchor.x(), m_anchor.y()};
    const pixeltools::Point b{m_current.x(), m_current.y()};

    switch (m_mode) {
    case ShapeLine:
        m_path = pixeltools::Engine::line(a, b);
        break;
    case ShapeEllipse:
        m_path = pixeltools::Engine::ellipse(a, b);
        break;
    case ShapeEllipseFilled:
        m_path = pixeltools::Engine::ellipseFilled(a, b);
        break;
    case ShapeRectangle:
        m_path = pixeltools::Engine::rectangle(a, b);
        break;
    case ShapeRectangleFilled:
        m_path = pixeltools::Engine::rectangleFilled(a, b);
        break;
    case ShapeBezier: {
        // Without a separate control-point drag we use a symmetric arch
        // between anchor and current: control points lifted by a quarter of
        // the span, which is the usual default for a two-point curve gesture.
        const int span = qAbs(b.x - a.x) + qAbs(b.y - a.y);
        const int bow = qMax(1, span / 4);
        const pixeltools::Point c1{a.x, a.y - bow};
        const pixeltools::Point c2{b.x, b.y - bow};
        m_path = pixeltools::Engine::bezier(a, c1, c2, b);
        break;
    }
    }
}

void KisToolPixelShape::paintPath()
{
    KisCanvas2 *kritaCanvas = dynamic_cast<KisCanvas2 *>(canvas());
    if (!kritaCanvas) {
        return;
    }

    KisNodeSP node = kritaCanvas->viewManager()->activeNode();
    if (!node) {
        return;
    }

    KisPaintDeviceSP device = node->paintDevice();
    if (!device) {
        return;
    }

    KisPainter painter(device);
    painter.setPaintColor(currentPaintColor());
    painter.setOpacity(1.0);
    painter.setCompositeOpId(node->compositeOpId());
    if (!painter.beginPaint(QRect())) {
        return;
    }

    // One dab per pixel, at pressure 1.0. The active preset supplies the
    // brush shape and colour; we only supply the exact position.
    for (const pixeltools::Point &p : m_path) {
        const QPointF docPos(static_cast<qreal>(p.x),
                             static_cast<qreal>(p.y));
        device->paintAt(docPos, painter, 1.0);
    }

    painter.endPaint();
    device->setDirty(QRect());
}

void KisToolPixelShape::paint(QPainter &gc, const KoViewConverter &converter)
{
    if (!m_active || m_path.empty()) {
        KisToolPaint::paint(gc, converter);
        return;
    }

    // Live preview: draw the pixel set as filled 1x1 rectangles in the
    // document's coordinate system. Because we draw whole cropped pixels with
    // no antialiasing, the preview shows exactly what will be committed.
    QVector<QRectF> rects;
    rects.reserve(static_cast<int>(m_path.size()));

    for (const pixeltools::Point &p : m_path) {
        const QRectF docRect(static_cast<qreal>(p.x),
                             static_cast<qreal>(p.y), 1.0, 1.0);
        rects.append(converter.documentToView(docRect));
    }

    gc.save();
    gc.setRenderHint(QPainter::Antialiasing, false);
    gc.setPen(Qt::NoPen);
    gc.setBrush(QColor(255, 0, 160, 120));
    for (const QRectF &r : rects) {
        gc.drawRect(r);
    }
    gc.restore();

    KisToolPaint::paint(gc, converter);
}

QWidget *KisToolPixelShape::createOptionWidget()
{
    QWidget *w = KisToolPaint::createOptionWidget();
    if (w) {
        w->setWindowTitle(i18n("Pixel Shape Tool"));
    }
    return w;
}
