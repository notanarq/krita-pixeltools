# Compilar a tool nativa

## Leia isto primeiro

O Krita **tem** API de plugin C++: carrega `.so` de `lib/kritaplugins/` e expõe
`KisToolPaint`, `KisPaintOp`, `KisFilter`. Plugins reais usam isso (krita-ai-tools
distribui `kritatoolsegmentation.so`; o G'MIC-Qt é compilado contra headers do
Krita).

O que o Krita **não** tem:

- **SDK instalado.** Os headers não vêm em pacote de distro nem no AppImage: é
  preciso a árvore de código-fonte.
- **ABI estável.** O `CMakeLists.txt` do próprio Krita diz: *"In general Krita
  does not guarantee any binary compatibility over C++ libraries between the
  releases."*
- **Um switch `-DBUILD_MEU_PLUGIN=ON`.** Não existe. Plugin nativo de terceiro é
  compilado **dentro** da árvore.

Consequências:

| | Python (ZIP) | C++ (nativo) |
|---|---|---|
| Instala em 1 minuto | ✅ | ❌ |
| Funciona em 5.3 e 6.0 | ✅ | precisa 1 build por versão |
| Distribuível a terceiros | ✅ | ❌ (só compilando) |
| Arrastar com preview | ❌ | ✅ |
| Snap no grid em tempo real | ❌ | ✅ |

**Boa notícia:** 5.3 e 6.0 são *o mesmo código-fonte*, compilado com Qt5 ou Qt6.
Não são duas bases para manter — é uma base, dois builds.

## Procedimento

1. Clone e compile o Krita ([manual oficial](https://docs.krita.org/en/untranslatable_pages/building_krita.html)):

```sh
git clone https://invent.kde.org/graphics/krita.git
cd krita && mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_INSTALL_PREFIX=$PWD/_install
ninja -j$(nproc) install
```

2. Copie a tool para dentro da árvore, junto das tools nativas:

```sh
mkdir -p ../plugins/tools/pixeltools
cp -r <este-repo>/native/* ../plugins/tools/pixeltools/
cp -r <este-repo>/core ../plugins/tools/pixeltools/core
```

3. Registre a subpasta em `plugins/tools/CMakeLists.txt`:

```cmake
add_subdirectory(pixeltools)
```

4. Crie `plugins/tools/pixeltools/CMakeLists.txt` no molde de
   `plugins/tools/basictools/CMakeLists.txt`:

```cmake
set(pixeltools_SOURCES
    pixel_tools_plugin.cpp
    kis_tool_pixel_shape.cpp
    core/pixel_engine.cpp
)
kis_add_library(pixeltools MODULE ${pixeltools_SOURCES})
target_include_directories(pixeltools PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(pixeltools kritaui kritabasicflakes)
install(TARGETS pixeltools DESTINATION ${KRITA_PLUGIN_INSTALL_DIR})
install(FILES pixeltools.json DESTINATION ${KRITA_PLUGIN_INSTALL_DIR})
generate_export_header(pixeltools BASE_NAME pixeltools)
```

5. Recompile e rode o Krita do build:

```sh
ninja -j$(nproc) install && ./_install/bin/krita
```

A tool aparece na caixa de ferramentas (seção *Shape*).

## O que validar contra a sua árvore

As assinaturas em `native/` foram lidas do master, mas **confirme antes de
compilar** — é justamente o risco que você apontou:

- `libs/ui/tool/kis_tool_paint.h` — construtor `(KoCanvasBase*, const QCursor&)`
- `libs/ui/tool/kis_tool.h` — `beginPrimaryAction`/`continuePrimaryAction`/`endPrimaryAction`
- `plugins/tools/basictools/kis_tool_line.h` — modelo de `KisToolPaintFactoryBase`,
  `setSection(ToolBoxSection::Shape)`, `setActivationShapeId(KRITA_TOOL_ACTIVATION_ID)`
- `plugins/tools/basictools/default_tools.cc` — modelo de registro em `KoToolRegistry`

## Pontos frágeis conhecidos desta primeira versão

1. **`paintAt(p, 1.0)`** — a assinatura usada é a de `KisPaintDevice` com painter,
   posição e pressão. Confirme a sobrecarga disponível na sua versão; se a
   assinatura divergir, é o único ponto a ajustar.
2. **`beginTransaction`/`endTransaction`** — abertos em `beginPrimaryAction` e
   fechados em `endPrimaryAction` com dois `KisPainter` distintos. Funciona, mas o
   idioma canônico do Krita é manter o painter como membro. Se o stroke não
   agrupar no undo, esse é o primeiro lugar a olhar.
3. **Preview** — usa `paint()` da tool com retângulos 1×1, não
   `KisCanvasDecoration`. Isso evita deliberadamente a API instável que você
   apontou como maior risco; o resultado visual é equivalente porque cada pixel é
   desenhado sem antialiasing.
4. **Bezier** — sem um segundo arrasto para os pontos de controle, a curva usa um
   arco simétrico. Para controle total, adicione um estado de drag secundário.
