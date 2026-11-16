/*
 *  SPDX-License-Identifier: GPL-2.0-or-later
 *
 *  pixel_tools_plugin.cpp - registers the pixel shape tool with Krita.
 *
 *  Modelled on Krita's own plugins/tools/basictools/default_tools.cc: a tool
 *  plugin does not expose a menu action, it registers a factory in
 *  KoToolRegistry, which is what makes the tool appear in the toolbox.
 */

#include "pixel_tools_plugin.h"

#include <kpluginfactory.h>
#include <kis_debug.h>

#include <KoToolRegistry.h>

#include "kis_tool_pixel_shape.h"

K_PLUGIN_FACTORY_WITH_JSON(PixelToolsFactory, "pixeltools.json",
                           registerPlugin<PixelToolsPlugin>();)

PixelToolsPlugin::PixelToolsPlugin(QObject *parent, const QVariantList &)
    : QObject(parent)
{
    KoToolRegistry::instance()->add(new KisToolPixelShapeFactory());
}

PixelToolsPlugin::~PixelToolsPlugin() = default;

#include "pixel_tools_plugin.moc"
