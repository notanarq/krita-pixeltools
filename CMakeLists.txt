# SPDX-License-Identifier: GPL-2.0-or-later
#
# pixeltools - native Krita tool
#
# Two very different build targets live here:
#
#  1. The rasterization CORE (core/): plain C++17, zero dependencies. Builds
#     and tests anywhere. This is the part that proves the geometry.
#
#  2. The native Krita TOOL (native/): needs Krita headers, Qt and KF.
#
# IMPORTANT, read before trying (2)
# ---------------------------------
# Krita ships a C++ API but NO installed SDK, NO stable ABI, and its own
# CMakeLists.txt says it does not guarantee binary compatibility between
# releases. There is no supported "-DBUILD_MY_PLUGIN=ON" switch in a Krita
# tree either: third-party native tools are compiled by placing their sources
# in the tree (e.g. plugins/tools/<name>/) and adding them to that directory's
# CMakeLists, then rebuilding Krita. See docs/02-build-native.md for the exact
# procedure. This file therefore only *checks* the prerequisites and tells you
# precisely what is missing, instead of pretending a one-flag build exists.
#
# Core only (what CI runs, and what works anywhere):
#   cmake -S . -B build -DPIXELTOOLS_BUILD_CORE_ONLY=ON
#   cmake --build build -j && ctest --test-dir build --output-on-failure

cmake_minimum_required(VERSION 3.16)
project(pixeltools VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

option(PIXELTOOLS_BUILD_CORE_ONLY
       "Build only the dependency-free core and its tests" OFF)

# ---------------------------------------------------------------------------
# The rasterization core: no Qt, no Krita, no external dependencies.
# ---------------------------------------------------------------------------

add_library(pixeltools_core STATIC core/pixel_engine.cpp)
target_include_directories(pixeltools_core PUBLIC core)
target_compile_options(pixeltools_core PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra -Wpedantic>
)

enable_testing()

add_executable(pixel_engine_tests core/tests/test_pixel_engine.cpp)
target_link_libraries(pixel_engine_tests PRIVATE pixeltools_core)
add_test(NAME pixel_engine COMMAND pixel_engine_tests)

# Reference oracle for the C++/Python parity test.
add_executable(engine_oracle tools/engine_oracle.cpp)
target_link_libraries(engine_oracle PRIVATE pixeltools_core)

if(PIXELTOOLS_BUILD_CORE_ONLY)
    message(STATUS "pixeltools: core-only build; native tool not configured")
    return()
endif()

# ---------------------------------------------------------------------------
# The native Krita tool.
# ---------------------------------------------------------------------------
#
# KRITA_INCLUDE_DIRS must point at the include/ directory of a Krita *build*
# that matches the Krita you will run (same Qt major version, ideally same
# commit). A distro's -dev package is usually NOT enough because Krita does
# not install its private headers.

if(NOT KRITA_INCLUDE_DIRS)
    message(STATUS
        "pixeltools: KRITA_INCLUDE_DIRS not set, so the native tool is not "
        "being built. The core is built and tested.\n"
        "To build the native tool, follow docs/02-build-native.md; it requires "
        "a Krita source tree and a matching Qt/KF toolchain.")
    return()
endif()

find_package(QT NAMES Qt6 Qt5 REQUIRED COMPONENTS Core Gui Widgets)
find_package(Qt${QT_VERSION_MAJOR} REQUIRED COMPONENTS Core Gui Widgets)
find_package(KF${QT_VERSION_MAJOR} REQUIRED COMPONENTS CoreAddons)

message(STATUS "pixeltools: building native tool against Qt${QT_VERSION_MAJOR}")

set(CMAKE_AUTOMOC ON)

add_library(pixeltools_tool MODULE
    native/pixel_tools_plugin.cpp
    native/kis_tool_pixel_shape.cpp
    native/pixel_tools_plugin.h
    native/kis_tool_pixel_shape.h
)

target_include_directories(pixeltools_tool PRIVATE
    ${KRITA_INCLUDE_DIRS}
    core
)

target_link_libraries(pixeltools_tool PRIVATE
    Qt${QT_VERSION_MAJOR}::Core
    Qt${QT_VERSION_MAJOR}::Gui
    Qt${QT_VERSION_MAJOR}::Widgets
    KF${QT_VERSION_MAJOR}::CoreAddons
    pixeltools_core
)

# Krita resolves the plugin JSON next to the library, so both must be installed
# into the same kritaplugins directory.
set_target_properties(pixeltools_tool PROPERTIES
    PREFIX ""
    OUTPUT_NAME "pixeltools_tool"
)

install(TARGETS pixeltools_tool DESTINATION ${KRITA_PLUGIN_DIR})
install(FILES native/pixeltools.json DESTINATION ${KRITA_PLUGIN_DIR})

# The Python half is always installable on its own; ship it alongside.
install(FILES pykrita/pixeltools.desktop
        DESTINATION ${KDE_INSTALL_DATADIR}/krita/pykrita OPTIONAL)
install(DIRECTORY pykrita/pixeltools
        DESTINATION ${KDE_INSTALL_DATADIR}/krita/pykrita OPTIONAL
        FILES_MATCHING PATTERN "*.py")
