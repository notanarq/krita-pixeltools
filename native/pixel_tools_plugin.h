/*
 *  SPDX-License-Identifier: GPL-2.0-or-later
 *
 *  pixel_tools_plugin.h - Krita plugin entry point.
 */

#ifndef PIXEL_TOOLS_PLUGIN_H_
#define PIXEL_TOOLS_PLUGIN_H_

#include <QObject>
#include <QVariantList>

class PixelToolsPlugin : public QObject
{
    Q_OBJECT

public:
    PixelToolsPlugin(QObject *parent, const QVariantList &);
    ~PixelToolsPlugin() override;
};

#endif // PIXEL_TOOLS_PLUGIN_H_
