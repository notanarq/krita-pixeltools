# pixeltools — geometria correta para pixel art no Krita

Ferramentas de desenho para Krita que rasterizam formas em **coordenadas inteiras**.
Em vez de aproximar curvas com Bézier + antialiasing e carimbar dabs por distância
percorrida (o que desloca pixels), aqui cada pixel do caminho é calculado
matematicamente e aplicado exatamente ali.

```sh
# módulo sem dependências: compila e testa em qualquer lugar
cmake -S . -B build -DPIXELTOOLS_BUILD_CORE_ONLY=ON
cmake --build build -j && ctest --test-dir build --output-on-failure
# ou sem cmake:
./scripts/build_core.sh
```

## O problema

O Krita desenha elipses e curvas via Bézier + antialiasing, aplicando dabs do brush
por distância percorrida, não por coordenada inteira. O resultado é sólido, mas
geometricamente incorreto. Para uma elipse 7×5:

| esperado | produzido pelo Krita |
|---|---|
| `0011100` | `0011100` |
| `0100010` | `0100010` |
| `1000001` | `0110001` |
| `0100010` | `0100100` |
| `0011100` | `0011100` |

A linha central e a penúltima têm pixels deslocados. O `pixeltools` reproduz a
coluna da esquerda **exatamente** — e isso é um teste automatizado, não uma
afirmação (`core/tests/test_pixel_engine.cpp`, caso `ACCEPTANCE`).

## O que está pronto e verificado

| Camada | Estado |
|---|---|
| **Motor de rasterização** (`core/`) — C++17 puro, sem Qt/Krita | ✅ testado: **69.128 checagens, 0 falhas** |
| **Port Python do motor** (`pykrita/pixeltools/pixel_engine.py`) | ✅ paridade com o C++: **44.636 checagens, 0 falhas** |
| **Escrita de pixels undoable** (`painter.py`) | ✅ implementado (merge down = 1 passo de undo) |
| **Docker Python** (`pixeltools_docker.py`) | ✅ importável como ZIP |
| **Tool nativa C++** (`native/`) | ⚠️ escrita, **não compilada aqui** (precisa de árvore do Krita) |

## Instalação (plugin Python, via ZIP)

1. `python3 scripts/package.py` gera `dist/pixeltools.zip`
2. No Krita: **Ferramentas ▸ Scripts ▸ Importar plugin Python de arquivo…**
3. Reinicie o Krita
4. **Configurar Krita ▸ Gerenciador de plugins Python** → ative *Pixel Tools*
5. Reinicie. O painel aparece em **Configurações ▸ Dockers ▸ Pixel Tools**

> O ZIP entrega **apenas a metade Python**. A tool nativa não pode ser
> distribuída por ZIP — veja `docs/02-build-native.md`.

## O limite que define o projeto

O PyKrita **não expõe eventos de mouse no canvas nem conversão de coordenadas
tela→imagem**. Isso não é limitação de esforço, é da API. Consequência prática:

- **Não dá** em Python: arrastar para desenhar com preview em tempo real.
- **Dá** em Python: calcular e escrever pixels exatos e desfazíveis.
- **Só dá em C++**: travar o cursor no grid durante o arrasto e desenhar o preview.

Por isso o docker recebe a geometria como **números**, e oferece o botão
*Capture from selection*: selecione uma área com qualquer ferramenta de seleção e
o plugin usa a bounding box dela como caixa da forma. É o mais próximo de
"desenhar arrastando" que existe sem C++.

## Como o undo funciona

`Node.setPixelData()` **não é desfazível** — o Krita não expõe a pilha de undo ao
scripting. Então `painter.py` não escreve na layer ativa. Ele:

1. cria uma layer temporária acima da ativa,
2. escreve os pixels exatos nela,
3. faz `mergeDown()` nela.

O passo 3 roda dentro de um `KisProcessingApplicator` com o comando de undo
"Merge Down", então **um único Ctrl+Z remove a forma inteira**.

## Estrutura

```
core/            motor C++ puro + testes (sem Qt, sem Krita)
  pixel_engine.h/.cpp
  tests/test_pixel_engine.cpp    69k checagens
  tests/test_parity.py           paridade Python ↔ C++
tools/engine_oracle.cpp          oráculo de referência para a paridade
pykrita/                         o plugin que o Krita importa
  pixeltools.desktop
  pixeltools/pixel_engine.py     motor portado
  pixeltools/painter.py          escrita undoable
  pixeltools/pixeltools_docker.py  UI
native/                          tool C++ (precisa de árvore do Krita)
docs/                            build nativo, limitações, arquitetura
scripts/                         build_core.sh, package.py
```

## Licença

GPL-2.0-or-later, a mesma do Krita — exigido para plugins C++ que linkam
contra o Krita.
