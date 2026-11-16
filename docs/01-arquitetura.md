# Arquitetura e o limite real da API

## Por que três camadas, e o que cada uma pode fazer

O projeto original propõe motor C++ + tool C++ + docker Python. Isso está
correto **conceitualmente**, mas a divisão de trabalho real não é a que parece,
porque o PyKrita não expõe o que a tool nativa precisa.

### O fato que muda o desenho

`libkis` (a API Python) expõe:

- `Krita`, `Document`, `Node`, `Selection`, `View`, `Canvas`, `Window`, `Resource`
- `Canvas.zoomLevel()`, `Canvas.rotation()`, `Canvas.pan()` (só leitura de estado)

`libkis` **não** expõe:

- nenhum evento de mouse/tablet no canvas
- `canvasToImage()` / `imageToCanvas()` — nenhuma conversão tela→imagem
- a pilha de undo

Logo: **nenhum plugin Python consegue "arrastar para desenhar"**. Não há como
saber onde o usuário clicou. Qualquer plugin Python que sugira isso está pedindo
que o usuário digite coordenadas ou use uma seleção — e é exatamente o que este
docker faz, de forma explícita.

### Camada 1 — Motor (`core/`)

C++17 puro, sem Qt e sem Krita. Determinístico: mesma entrada, mesma saída.

Algoritmos:

- **Linha**: Bresenham, **canonicalizado**. O laço clássico não é invariante à
  troca de endpoints: quando a reta ideal passa exatamente por um canto de pixel,
  o desempate escolhe outro pixel conforme a direção. Rasterizamos a partir do
  endpoint lexicograficamente menor e invertemos a saída, então o *conjunto* é
  independente da direção e a *ordem* segue o chamador.
- **Elipse**: Zingl (`plotEllipseRect`), o midpoint corrigido. O midpoint do
  livro-texto perde o último pixel em diâmetros pares e achata elipses finas —
  reproduzido nos testes 6×6 e 8×8, onde o ingênuo falha.
- **Bézier**: amostragem proporcional ao comprimento do polígono de controle,
  arredondamento para inteiro, e Bresenham entre amostras consecutivas. O
  resultado é 8-conexo por construção: não existe buraco.
- **Espessura**: carimbo de disco (não quadrado) por pixel do caminho.
- **Preenchimento**: scanline even-odd sobre o contorno.

### Camada 2 — Tool nativa (`native/`)

`KisToolPixelShape : KisToolPaint`, com as assinaturas lidas do fonte do Krita:

```cpp
void beginPrimaryAction(KoPointerEvent *event) override;
void continuePrimaryAction(KoPointerEvent *event) override;
void endPrimaryAction(KoPointerEvent *event) override;
void paint(QPainter &gc, const KoViewConverter &converter) override;
```

Ela preenche a lacuna do Python: snap no grid (`floor` da coordenada contínua),
preview durante o arrasto, e um dab por pixel via
`device->paintAt(docPos, painter, 1.0)` com o preset ativo do usuário.

**Não foi compilada neste repositório** porque exige uma árvore do Krita.
Ver `docs/02-build-native.md`.

### Camada 3 — Docker Python (`pykrita/`)

Recebe a geometria e aplica. Importável como ZIP, sem compilar nada.

## Undo: por que não `setPixelData` direto

`Node.setPixelData()` escreve os pixels e **não** cria passo de undo. Os
mantenedores do Krita confirmaram que a pilha de undo não é exposta ao scripting
(saída oficial: clonar documento, ou usar só `action()`).

Solução em `painter.py`:

```
createNode("pixeltools stroke", "paintlayer")   # temporária
  -> addChildNode(temp, None)                   # Irmã seguinte da ativa
  -> setPixelData(bytes, x, y, w, h)            # pixels exatos
  -> temp.mergeDown()                           # comando de undo "Merge Down"
```

`Node::mergeDown()` → `KisImage::mergeDown()` → `KisLayerUtils::mergeDown()`,
que roda em `KisProcessingApplicator` com `kundo2_i18n("Merge Down")`. Um Ctrl+Z
desfaz a forma inteira.

## Ordem dos canais

`pixelData`/`setPixelData` usam a ordem nativa do paint device. Para inteiro
RGBA 8 bits é **BGRA** (documentado: "Integer RGBA: Blue, Green, Red, Alpha").
Trocar isso inverte vermelho e azul — invisível em teste monocromático, óbvio na
arte real. `_write_points()` escreve B,G,R,A nessa ordem de propósito.

## Espaço de cor

Só escrevemos em **RGBA U8**. Outros espaços são recusados com mensagem clara em
vez de gerar cor errada silenciosamente.
