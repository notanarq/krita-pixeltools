/*
 *  SPDX-License-Identifier: GPL-2.0-or-later
 *
 *  kis_tool_pixel_shape.h - native Krita tool for pixel-exact shapes.
 *
 *  WHY THIS FILE EXISTS
 *  --------------------
 *  The Python plugin (pykrita/) can write exact pixels, but PyKrita exposes
 *  neither mouse events on the canvas nor a screen->image coordinate
 *  conversion. So the two things the project actually asked for - snapping
 *  the cursor to the integer grid while dragging, and a live preview during
 *  the drag - are impossible from Python.
 *
 *  This tool is that missing half. It inherits KisToolPaint, overrides the
 *  primary action, snaps incoming event positions to integer pixels, asks
 *  the shared rasterization core for the exact pixel set, and paints ONE dab
 *  per pixel of the active preset with paintAt(p, 1.0).
 *
 *  IMPORTANT BUILD CAVEAT
 *  ----------------------
 *  Krita exposes a C++ *API* but no stable ABI and no installed headers: a
 *  native plugin is compiled against a Krita source tree (or a build's
 *  include/ directory) and must be rebuilt for each Krita version and
 *  platform. It also cannot be delivered as a ZIP through
 *  Tools > Scripts > Import Python Plugin. See docs/02-build-native.md.
 *
 *  The signatures below were taken from Krita master (5.3/6.0 share one
 *  source tree; they differ only in Qt5 vs Qt6). Verify against your tree
 *  before building - that is the whole point of build.py's version check.
 */

#ifndef KIS_TOOL_PIXEL_SHAPE_H_
#define KIS_TOOL_PIXEL_SHAPE_H_

#include <QPoint>
#include <QVector>

#include <KisToolPaintFactoryBase.h>
#include <kis_types.h>
#include <tool/kis_tool_paint.h>

#include "pixel_engine.h"

class KoCanvasBase;
class KoPointerEvent;
class QPainter;
class KoViewConverter;

class KisToolPixelShape : public KisToolPaint
{
    Q_OBJECT

public:
    enum ShapeMode {
        ShapeLine = 0,
        ShapeEllipse,
        ShapeEllipseFilled,
        ShapeRectangle,
        ShapeRectangleFilled,
        ShapeBezier,
    };

    KisToolPixelShape(KoCanvasBase *canvas);
    ~KisToolPixelShape() override;

    void beginPrimaryAction(KoPointerEvent *event) override;
    void continuePrimaryAction(KoPointerEvent *event) override;
    void endPrimaryAction(KoPointerEvent *event) override;
    void cancelStroke();

    void paint(QPainter &gc, const KoViewConverter &converter) override;

    void requestStrokeCancellation() override;
    void requestStrokeEnd() override;

    void setShapeMode(ShapeMode mode);
    ShapeMode shapeMode() const { return m_mode; }

    QString quickHelp() const override;

protected:
    QWidget *createOptionWidget() override;

private:
    /** Converts an event position to an integer pixel index. */
    QPoint snapToPixel(const QPointF &imagePos) const;

    /** Recomputes m_path from the current anchor/mode. */
    void rebuildPath();

    /** Paints every pixel of m_path as one dab of the active preset. */
    void paintPath();

    ShapeMode m_mode {ShapeLine};
    bool m_active {false};
    QPoint m_anchor;
    QPoint m_current;

    pixeltools::Path m_path;
};

/**
 * Tool factory. Krita's toolbox discovers tools through KoToolRegistry, and a
 * Krita plugin registers factories in its factory class (see the K_PLUGIN_*
 * macro in pixel_tools_plugin.cpp).
 */
class KisToolPixelShapeFactory : public KisToolPaintFactoryBase
{
public:
    KisToolPixelShapeFactory()
        : KisToolPaintFactoryBase("PixelTools/KisToolPixelShape")
    {
        setToolTip(i18n("Pixel Shape Tool"));
        setSection(ToolBoxSection::Shape);
        setActivationShapeId(KRITA_TOOL_ACTIVATION_ID);
        setPriority(1);
        setIconName(koIconNameCStr("krita_tool_line"));
    }

    ~KisToolPixelShapeFactory() override = default;

    KoToolBase *createTool(KoCanvasBase *canvas) override
    {
        return new KisToolPixelShape(canvas);
    }
};

#endif // KIS_TOOL_PIXEL_SHAPE_H_
