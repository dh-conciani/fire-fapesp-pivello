// ============================================================================
// MAPBIOMAS | ATLAS DE ONDAS DE CALOR — THUMBNAILS (GEE CODE EDITOR)
// Versao 2.2 | Carregamento gradual e repeticao individual. Somente visualizacao. NENHUM export e enviado.
//
//
// MODOS:
//   - Mensal: 12 mapas (janeiro-dezembro) de um ano selecionado;
//   - Anual: 41 mapas (1985-2025);
//   - V1, V2 ou V1 + V2 (separadas, com escala identica);
//   - Comparacao opcional V2 - V1, onde ambos os arquivos existem;
//   - Metrica, legenda geral, zero transparente e limites Brasil/estados;
//   - Maximo de visualizacao, QC e ampliacao ao clicar.
//   - Fila progressiva e botao Recarregar para miniaturas com erro.
//
// Lê SOMENTE os produtos FINAIS (5 bandas), nunca o staging (60 bandas).
// Imagens ainda nao exportadas aparecem como cartoes "Pendente".
// Para atualizar a lista apos novos exports, clique GERAR / ATUALIZAR.
// ============================================================================

// ----------------------- 1. CONFIGURACAO -----------------------------------
var ROOT = 'projects/mapbiomas-brazil/assets/' +
    'DEGRADATION/COLLECTION-11/CLIMATIC_WAVES';
var SPEC_VERSION = 1;  // monthly_YYYY_MM_m1; annual_YYYY_m1

var COLLECTIONS = {
  PRODUCAO: {
    V1: ROOT + '/heatWaves_agg',
    V2: ROOT + '/heatWaves_v2_agg'
  },
  PILOTO: {
    V1: ROOT + '/heatWaves_agg_NATIONAL_PILOT',
    V2: ROOT + '/heatWaves_v2_agg_NATIONAL_PILOT'
  }
};

var METRICS = {
  heatwave_days: {
    label: 'Dias com onda de calor', unit: 'dias',
    monthly: 20, annual: 90, diffMonthly: 10, diffAnnual: 35
  },
  event_count: {
    label: 'Numero de eventos', unit: 'eventos',
    monthly: 4, annual: 15, diffMonthly: 3, diffAnnual: 8
  },
  max_event_duration: {
    label: 'Duracao maxima', unit: 'dias',
    monthly: 25, annual: 40, diffMonthly: 12, diffAnnual: 20
  },
  mean_event_duration: {
    label: 'Duracao media', unit: 'dias',
    monthly: 20, annual: 30, diffMonthly: 10, diffAnnual: 15
  }
};

// Valores absolutos: amarelo-claro -> laranja -> vermelho ->
// vermelho-terra/marrom -> vinho -> roxo.
var HOT = [
  'fff7bc', 'fee391', 'fec44f', 'fc8d59', 'ef3b2c',
  'b91c1c', '993404', '762a43', '54278f', '2e004f'
];
// Diferencas: azul-petroleo = V2 menor; creme = zero; roxo = V2 maior.
var DELTA = [
  '225e63', '4fa7a2', 'b8d5c3', 'e8e9d2', 'fff4d6',
  'fcbd79', 'eb593c', 'aa2041', '54278f'
];
var QC_COLOR = '00e8ff';

// A mesma area e usada em TODAS as miniaturas. O raster e recortado ao Brasil.
// O retangulo inclui o territorio nacional e ilhas orientais.
var THUMB_REGION = ee.Geometry.Rectangle([-75, -35, -28, 6], null, false);
// O RGB dos produtos finais JA esta recortado ao Brasil durante o export.
// Nao repetir clip() com uma geometria nacional complexa em cada thumbnail:
// isto reduz a carga computacional sem alterar os pixels dos assets finais.
// Usamos limites SIMPLIFICADOS somente para desenho nesta escala (~170 px).
// Os assets originais nunca sao modificados.
var BRAZIL_STATES = ee.FeatureCollection('FAO/GAUL/2015/level1')
    .filter(ee.Filter.eq('ADM0_NAME', 'Brazil'));
var BRAZIL_OUTLINE = ee.FeatureCollection('FAO/GAUL/2015/level0')
    .filter(ee.Filter.eq('ADM0_NAME', 'Brazil'));
var BORDER_SIMPLIFY_METERS = 12000;
var SIMPLIFIED_STATES = BRAZIL_STATES.map(function(feature) {
  return ee.Feature(feature).simplify(ee.ErrorMargin(BORDER_SIMPLIFY_METERS));
});
var SIMPLIFIED_COUNTRY = BRAZIL_OUTLINE.map(function(feature) {
  return ee.Feature(feature).simplify(ee.ErrorMargin(BORDER_SIMPLIFY_METERS));
});
var STATE_BORDER_COLOR = '929292';
var BRAZIL_BORDER_COLOR = '171717';
var STATE_BORDERS = ee.Image(0).byte()
    .paint(SIMPLIFIED_STATES, 1, 1).selfMask()
    .visualize({palette: [STATE_BORDER_COLOR]});
var BRAZIL_BORDERS = ee.Image(0).byte()
    .paint(SIMPLIFIED_COUNTRY, 1, 2).selfMask()
    .visualize({palette: [BRAZIL_BORDER_COLOR]});

function overlayBorders(rgb) {
  return rgb.blend(STATE_BORDERS).blend(BRAZIL_BORDERS);
}

// Miniatura mais leve, exibida em 171 px. Uma nova solicitacao e enviada a
// cada intervalo, evitando iniciar 41/82/123 processamentos simultaneamente.
var THUMB_PIXELS = 180;
var THUMB_DELAY_MS = 4000;  // Ajustavel pela interface (2, 4 ou 6 segundos).
var CARDS_PER_ROW = 5;

var MONTH_NAMES = [
  'Janeiro', 'Fevereiro', 'Marco', 'Abril', 'Maio', 'Junho',
  'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'
];
var YEARS = [];
for (var y = 1985; y <= 2025; y++) YEARS.push(String(y));

// ----------------------- 2. FUNCOES DE ASSETS -------------------------------
function pad2(value) {
  var txt = String(value);
  return txt.length < 2 ? '0' + txt : txt;
}

function fileName(period, year, month) {
  return period === 'ANUAL' ?
      'annual_' + year + '_m' + SPEC_VERSION :
      'monthly_' + year + '_' + pad2(month) + '_m' + SPEC_VERSION;
}

function normalizedType(type) {
  return String(type || '').replace(/[^a-z]/gi, '').toUpperCase();
}

// Evita dezenas de getAsset(): lista a colecao uma vez por versao.
// API JS: ee.data.listAssets(parent, params), com paginacao.
// Falhas de permissao/servidor NAO sao confundidas com falta de export.
function inventory(collectionId) {
  var answer = {images: {}, error: null, total: 0};
  try {
    var pageToken = null;
    var safety = 0;
    do {
      var options = {pageSize: '1000', view: 'BASIC'};
      if (pageToken) options.pageToken = pageToken;
      var result = ee.data.listAssets(collectionId, options) || {};
      var assets = result.assets || [];
      assets.forEach(function(asset) {
        if (normalizedType(asset.type) === 'IMAGE') {
          var id = String(asset.name || asset.id || '');
          if (id) {
            answer.images[id.split('/').pop()] = id;
            answer.total++;
          }
        }
      });
      pageToken = result.nextPageToken || null;
      safety++;
      if (safety > 1000) throw new Error('Paginacao excedeu limite de seguranca');
    } while (pageToken);
  } catch (err) {
    answer.error = String(err);
    answer.images = {}; // Nunca apresentar como "pendente" se a consulta falhou.
  }
  return answer;
}

function requestedPeriods(periodMode, selectedYear) {
  var periods = [];
  if (periodMode === 'MENSAL') {
    for (var m = 1; m <= 12; m++) {
      periods.push({
        year: Number(selectedYear), month: m,
        file: fileName('MENSAL', selectedYear, m),
        label: pad2(m) + ' · ' + MONTH_NAMES[m - 1] + ' / ' + selectedYear
      });
    }
  } else {
    for (var yr = 1985; yr <= 2025; yr++) {
      periods.push({
        year: yr, month: null,
        file: fileName('ANUAL', yr, null), label: String(yr)
      });
    }
  }
  return periods;
}

function numericScale(text, fallback) {
  var trimmed = String(text || '').trim();
  if (!trimmed) return fallback;
  var value = Number(trimmed);
  return isFinite(value) && value > 0 ? value : fallback;
}

// ----------------------- 3. IMAGENS DE VISUALIZACAO --------------------------
function visualImage(assetId, metric, maxValue, showQc, lightMode) {
  var source = ee.Image(assetId);
  var values = source.select(metric);
  // Mascara APENAS na imagem de visualizacao. O asset e seus zeros validos
  // continuam intactos; PNG representa esses pixels com alpha transparente.
  var rgb = values.updateMask(values.neq(0)).visualize({
    min: 0, max: maxValue, palette: HOT
  });
  if (showQc && metric !== 'heatwave_days') {
    var qc = source.select('qc_censored_event').eq(1).selfMask()
        .visualize({palette: [QC_COLOR]});
    rgb = rgb.blend(qc);
  }
  // No modo leve mantemos o limite externo do Brasil, mas dispensamos
  // o desenho dos estados no pedido especifico que apresentou problema.
  return lightMode ? rgb.blend(BRAZIL_BORDERS) : overlayBorders(rgb);
}

function differenceImage(v1Id, v2Id, metric, maxDiff, showQc, lightMode) {
  var first = ee.Image(v1Id);
  var second = ee.Image(v2Id);
  // Calcula V2-V1 COM os zeros originais: zero numa versao e positivo na
  // outra ainda produz diferenca. Mascara apenas o RESULTADO igual a zero.
  // Para duracoes, pixels originalmente mascarados seguem sem comparacao.
  var difference = second.select(metric).subtract(first.select(metric));
  var rgb = difference.updateMask(difference.neq(0)).visualize({
    min: -maxDiff, max: maxDiff, palette: DELTA
  });
  if (showQc && metric !== 'heatwave_days') {
    var qc = first.select('qc_censored_event').eq(1)
        .or(second.select('qc_censored_event').eq(1)).selfMask()
        .visualize({palette: [QC_COLOR]});
    rgb = rgb.blend(qc);
  }
  // No modo leve mantemos o limite externo do Brasil, mas dispensamos
  // o desenho dos estados no pedido especifico que apresentou problema.
  return lightMode ? rgb.blend(BRAZIL_BORDERS) : overlayBorders(rgb);
}

var THUMB_OPTIONS = {
  region: THUMB_REGION,
  crs: 'EPSG:4326',
  dimensions: THUMB_PIXELS,
  format: 'png'
};

// ----------------------- 4. COMPONENTES DA INTERFACE -----------------------
var COLOR_INK = '#4b2632';
var COLOR_MUTED = '#705d5b';
var COLOR_BG = '#fffaf3';

function title(text, size, color) {
  return ui.Label(text, {
    fontWeight: 'bold', fontSize: size || '14px',
    color: color || COLOR_INK, margin: '8px 0 4px 0'
  });
}
function note(text) {
  return ui.Label(text, {
    fontSize: '11px', color: COLOR_MUTED, margin: '3px 0 7px 0',
    whiteSpace: 'pre-wrap'
  });
}
function ramp(palette, left, middle, right) {
  var panel = ui.Panel({style: {margin: '4px 0 8px 0'}});
  var row = ui.Panel({layout: ui.Panel.Layout.flow('horizontal')});
  palette.forEach(function(color) {
    row.add(ui.Label('  ', {
      backgroundColor: '#' + color, padding: '7px 7px', margin: '0'
    }));
  });
  panel.add(row);
  panel.add(ui.Label(left + '     ' + middle + '     ' + right, {
    fontSize: '10px', color: COLOR_MUTED, margin: '2px 0 0 0'
  }));
  return panel;
}

var side = ui.Panel({
  layout: ui.Panel.Layout.flow('vertical'),
  style: {width: '314px', padding: '12px', backgroundColor: COLOR_BG}
});
side.add(title('MAPBIOMAS | ONDAS DE CALOR', '18px', '#812b42'));
side.add(note('Atlas de miniaturas: 12 meses ou 41 anos em uma unica visualizacao.'));

side.add(title('Base de dados'));
var datasetSelect = ui.Select({
  items: [
    {label: 'Producao (1985-2025)', value: 'PRODUCAO'},
    {label: 'Piloto nacional (2023)', value: 'PILOTO'}
  ], value: 'PRODUCAO', style: {stretch: 'horizontal'}
});
side.add(datasetSelect);

side.add(title('Galeria'));
var periodSelect = ui.Select({
  items: [
    {label: 'Mensal: os 12 meses de um ano', value: 'MENSAL'},
    {label: 'Anual: todos os 41 anos', value: 'ANUAL'}
  ], value: 'ANUAL', style: {stretch: 'horizontal'}
});
side.add(periodSelect);

var yearLabel = note('Escolher ano para a galeria mensal:');
var yearSelect = ui.Select({
  items: YEARS, value: '2023', style: {stretch: 'horizontal'}
});
side.add(yearLabel);
side.add(yearSelect);
function updateYearVisibility() {
  var shown = periodSelect.getValue() === 'MENSAL';
  yearLabel.style().set('shown', shown);
  yearSelect.style().set('shown', shown);
}
periodSelect.onChange(updateYearVisibility);
updateYearVisibility();

side.add(title('Versoes'));
var versionSelect = ui.Select({
  items: [
    {label: 'Somente V1 (metodo original)', value: 'V1'},
    {label: 'Somente V2 (TX90 / WSDI)', value: 'V2'},
    {label: 'Comparar V1 e V2 (duas galerias)', value: 'AMBAS'}
  ], value: 'V2', style: {stretch: 'horizontal'}
});
side.add(versionSelect);
var diffCheckbox = ui.Checkbox({
  label: 'Incluir terceira galeria V2 - V1', value: false
});
side.add(diffCheckbox);

side.add(title('Indicador'));
var metricSelect = ui.Select({
  items: [
    {label: 'Dias com onda de calor', value: 'heatwave_days'},
    {label: 'Numero de eventos', value: 'event_count'},
    {label: 'Duracao maxima', value: 'max_event_duration'},
    {label: 'Duracao media', value: 'mean_event_duration'}
  ], value: 'heatwave_days', style: {stretch: 'horizontal'}
});
side.add(metricSelect);

side.add(title('Escala opcional'));
side.add(note('Maximo da paleta absoluta. Vazio = limite fixo padrao por metrica e periodicidade.'));
var maxText = ui.Textbox({placeholder: 'Padrao automatico', value: ''});
side.add(maxText);
side.add(note('Amplitude da diferenca (+/-), quando a terceira galeria estiver ativa.'));
var diffText = ui.Textbox({placeholder: 'Padrao automatico', value: ''});
side.add(diffText);

var qcCheckbox = ui.Checkbox({
  label: 'Sobrepor QC de eventos em ciano', value: false
});
side.add(qcCheckbox);

side.add(title('Carregamento das miniaturas'));
side.add(note('Solicitacoes graduais para evitar sobrecarga. Todas as miniaturas permanecem na mesma galeria.'));
var speedSelect = ui.Select({
  items: [
    {label: 'Conservador (6 segundos por imagem)', value: '6000'},
    {label: 'Recomendado (4 segundos por imagem)', value: '4000'},
    {label: 'Mais rapido (2 segundos por imagem)', value: '2000'}
  ], value: '4000', style: {stretch: 'horizontal'}
});
side.add(speedSelect);
var pauseButton = ui.Button({label: 'PAUSAR FILA', disabled: true,
    style: {stretch: 'horizontal'}});
side.add(pauseButton);

var renderButton = ui.Button({
  label: 'GERAR / ATUALIZAR THUMBNAILS',
  style: {stretch: 'horizontal', fontWeight: 'bold', margin: '12px 0 4px 0'}
});
side.add(renderButton);
var status = ui.Label('', {
  fontSize: '11px', color: '#7d2533', whiteSpace: 'pre-wrap',
  margin: '5px 0 8px 0'
});
side.add(status);
side.add(title('Legenda'));
var legendBox = ui.Panel();
side.add(legendBox);
side.add(note('0 e transparente apenas no mapa (sem alterar o asset). Limites em preto/cinza: FAO GAUL 2015. Dados ausentes podem tambem aparecer transparentes. Dias pertencem a suas datas UTC; eventos ao periodo em que TERMINAM.'));

side.add(title('Ampliacao (clique numa miniatura)'));
var detailBox = ui.Panel({style: {
  stretch: 'horizontal', backgroundColor: '#ffffff', padding: '3px'
}});
side.add(detailBox);
detailBox.add(note('Clique em uma miniatura pronta para ampliar.'));

var content = ui.Panel({
  layout: ui.Panel.Layout.flow('vertical'),
  style: {stretch: 'both', padding: '12px', backgroundColor: '#f8f7f4'}
});
var gallery = ui.Panel({layout: ui.Panel.Layout.flow('vertical'),
  style: {stretch: 'horizontal'}});
var galleryInfo = ui.Label('', {
  fontSize: '12px', color: COLOR_MUTED, whiteSpace: 'pre-wrap',
  margin: '3px 0 8px 0'
});
content.add(title('ATLAS DE ONDAS DE CALOR', '20px', '#7c233f'));
content.add(galleryInfo);
// Legenda GERAL: permanece acima de todas as 12/41/82 miniaturas, identica
// entre anos/meses e entre V1 e V2. Mostra limites da escala, nao extremos reais.
var galleryLegend = ui.Panel({
  layout: ui.Panel.Layout.flow('vertical'),
  style: {
    stretch: 'horizontal', padding: '9px', margin: '4px 0 12px 0',
    backgroundColor: '#ffffff', border: '1px solid #d6c6bc'
  }
});
content.add(galleryLegend);
content.add(gallery);

ui.root.setLayout(ui.Panel.Layout.flow('horizontal'));
ui.root.widgets().reset([side, content]);

// ----------------------- 5. FILA PROGRESSIVA DE MINIATURAS -------------------
// A API ui.Thumbnail nao oferece callback de erro HTTP na imagem renderizada.
// Assim, a interface marca "solicitada", nao "carregada", e oferece um botao
// para recriar thumbnails que apresentarem icone quebrado (quota/rede/timeout).
// A fila nao faz polling nem cria exports. Ao trocar as selecoes, tarefas
// agendadas da galeria anterior sao canceladas; as ja enviadas ao GEE nao.
var thumbQueue = [];
var thumbTimeout = null;
var currentGalleryId = 0;
var queuePaused = false;
var queueRequested = 0;
var queueTotal = 0;
var queueContext = '';

function updateQueueStatus() {
  var left = thumbQueue.length;
  var summary = queueContext + '\n' + queueRequested + '/' + queueTotal +
      ' miniaturas solicitadas; ' + left + ' aguardando na fila.';
  if (left === 0) {
    summary += '\nTodas solicitadas; algumas ainda podem estar processando no EE.';
  } else if (queuePaused) {
    summary += '\nFila pausada.';
  }
  summary += '\nSe quebrar: Recarregar ou tentar o modo leve individual.';
  status.setValue(summary);
}

function cancelThumbQueue() {
  currentGalleryId += 1;
  if (thumbTimeout !== null) {
    ui.util.clearTimeout(thumbTimeout);
    thumbTimeout = null;
  }
  thumbQueue = [];
  queuePaused = false;
  queueRequested = 0;
  queueTotal = 0;
  pauseButton.setLabel('PAUSAR FILA');
  pauseButton.setDisabled(true);
}

function scheduleNextThumb() {
  if (queuePaused || thumbTimeout !== null || !thumbQueue.length) {
    updateQueueStatus();
    return;
  }
  var targetGalleryId = currentGalleryId;
  thumbTimeout = ui.util.setTimeout(function() {
    thumbTimeout = null;
    if (currentGalleryId !== targetGalleryId || queuePaused) return;
    var task = thumbQueue.shift();
    if (!task || task.galleryId !== currentGalleryId) return;
    queueRequested++;
    try {
      task.load();
    } catch (err) {
      task.showError(String(err));
    }
    updateQueueStatus();
    scheduleNextThumb();
  }, THUMB_DELAY_MS);
}

function enqueueThumb(load, showError, highPriority) {
  var task = {galleryId: currentGalleryId, load: load, showError: showError};
  if (highPriority) thumbQueue.unshift(task);
  else thumbQueue.push(task);
  queueTotal++;
}

pauseButton.onClick(function() {
  queuePaused = !queuePaused;
  pauseButton.setLabel(queuePaused ? 'CONTINUAR FILA' : 'PAUSAR FILA');
  if (queuePaused && thumbTimeout !== null) {
    ui.util.clearTimeout(thumbTimeout);
    thumbTimeout = null;
  }
  if (!queuePaused) scheduleNextThumb();
  updateQueueStatus();
});

// ----------------------- 5. GALERIA E DETALHES ------------------------------
function openDetail(label, coloredImage) {
  detailBox.clear();
  detailBox.add(title(label, '13px', '#7c233f'));
  detailBox.add(ui.Thumbnail({
    image: coloredImage,
    params: {
      region: THUMB_REGION, crs: 'EPSG:4326',
      dimensions: 620, format: 'png'
    },
    style: {width: '275px', height: '275px', backgroundColor: '#ffffff'}
  }));
  detailBox.add(note('Miniatura ampliada. As cores representam a mesma escala da galeria.'));
}

function thumbnailCard(label, asset, coloredImage, unavailableText, lightImage) {
  var card = ui.Panel({
    layout: ui.Panel.Layout.flow('vertical'),
    style: {
      width: '181px', padding: '4px', margin: '3px',
      backgroundColor: '#ffffff', border: '1px solid #decfc6'
    }
  });
  card.add(ui.Label(label, {
    fontSize: '11px', fontWeight: 'bold', color: COLOR_INK,
    margin: '3px 2px 4px 2px'
  }));
  if (asset && coloredImage) {
    var previewBox = ui.Panel({layout: ui.Panel.Layout.flow('vertical'),
      style: {width: '171px', height: '171px', margin: '0',
        backgroundColor: '#ffffff'}});
    card.add(previewBox);
    var stateText = ui.Label('Aguardando fila...', {
      fontSize: '10px', color: '#776962',
      margin: '55px 5px 0 5px', whiteSpace: 'pre-wrap'
    });
    previewBox.add(stateText);
    var retryButton = ui.Button({label: 'Recarregar miniatura',
      style: {fontSize: '10px', margin: '3px 0 0 0', stretch: 'horizontal'}});
    retryButton.setDisabled(true);
    card.add(retryButton);

    var currentRender = coloredImage;
    function loadCard() {
      previewBox.clear();
      previewBox.add(ui.Thumbnail({
        image: currentRender,
        params: THUMB_OPTIONS,
        onClick: function() { openDetail(label, currentRender); },
        style: {
          width: '171px', height: '171px', margin: '0',
          backgroundColor: '#ffffff'
        }
      }));
      retryButton.setDisabled(false);
    }
    function showCardError(message) {
      previewBox.clear();
      previewBox.add(ui.Label('Erro ao solicitar miniatura.\n' + message, {
        color: '#a33246', fontSize: '10px', margin: '15px 4px',
        whiteSpace: 'pre-wrap'
      }));
      retryButton.setDisabled(false);
    }
    retryButton.onClick(function() {
      // Nao faz export nem altera o asset: apenas solicita um PNG novo.
      retryButton.setDisabled(true);
      previewBox.clear();
      previewBox.add(ui.Label('Recarregando...', {
        color: '#665555', fontSize: '10px', margin: '55px 5px'
      }));
      enqueueThumb(loadCard, showCardError, true);
      if (queuePaused) {
        queuePaused = false;
        pauseButton.setLabel('PAUSAR FILA');
      }
      pauseButton.setDisabled(false);
      scheduleNextThumb();
      updateQueueStatus();
    });
    // Segunda alternativa para falhas persistentes: contorno do Brasil
    // permanece preto, mas a renderizacao estadual e dispensada SOMENTE
    // nesta miniatura. Outros cartoes e assets nao sao afetados.
    var lightButton = ui.Button({label: 'Tentar modo leve (sem estados)',
      style: {fontSize: '10px', margin: '0', stretch: 'horizontal'}});
    lightButton.onClick(function() {
      if (!lightImage) return;
      currentRender = lightImage;
      retryButton.setDisabled(true);
      previewBox.clear();
      previewBox.add(ui.Label('Modo leve na fila...', {
        color: '#665555', fontSize: '10px', margin: '55px 5px'
      }));
      enqueueThumb(loadCard, showCardError, true);
      if (queuePaused) {
        queuePaused = false;
        pauseButton.setLabel('PAUSAR FILA');
      }
      pauseButton.setDisabled(false);
      scheduleNextThumb();
      updateQueueStatus();
    });
    card.add(lightButton);
    enqueueThumb(loadCard, showCardError, false);
  } else {
    var empty = ui.Panel({
      layout: ui.Panel.Layout.flow('vertical'),
      style: {
        width: '171px', height: '171px',
        backgroundColor: '#f0eeeb', padding: '5px'
      }
    });
    empty.add(ui.Label(unavailableText || 'Ainda nao exportado', {
      color: '#866a62', fontSize: '11px',
      whiteSpace: 'pre-wrap', margin: '45px 5px 0 5px'
    }));
    card.add(empty);
  }
  return card;
}

function appendGrid(parent, titleText, periods, makeCard) {
  parent.add(title(titleText, '17px', '#842c42'));
  var row;
  for (var i = 0; i < periods.length; i++) {
    if (i % CARDS_PER_ROW === 0) {
      row = ui.Panel({layout: ui.Panel.Layout.flow('horizontal'),
        style: {margin: '0 0 3px 0'}});
      parent.add(row);
    }
    row.add(makeCard(periods[i]));
  }
}

function legendKey(parent, color, description, transparent) {
  var row = ui.Panel({layout: ui.Panel.Layout.flow('horizontal'),
    style: {margin: '2px 0'}});
  row.add(ui.Label('    ', {
    backgroundColor: transparent ? '#ffffff' : '#' + color,
    border: transparent ? '1px dashed #8d8d8d' : '1px solid #' + color,
    padding: '2px 5px', margin: '1px 7px 0 0'
  }));
  row.add(ui.Label(description, {
    fontSize: '11px', color: COLOR_INK, margin: '0'
  }));
  parent.add(row);
}

function buildLegend(metric, maxAbs, maxDelta, showDelta, showQc) {
  var spec = METRICS[metric];
  var absRange = '0 a ' + maxAbs + ' ' + spec.unit;
  var diffRange = '-' + maxDelta + ' a +' + maxDelta + ' ' + spec.unit;
  legendBox.clear();
  galleryLegend.clear();

  // Legenda lateral compacta.
  legendBox.add(ui.Label(spec.label + ' | ' + absRange, {
    fontWeight: 'bold', fontSize: '11px'
  }));
  legendBox.add(ramp(HOT, '0', String(maxAbs / 2), String(maxAbs)));
  legendKey(legendBox, '', 'Zero = transparente', true);
  if (showDelta) {
    legendBox.add(ui.Label('V2 - V1 | ' + diffRange,
      {fontWeight: 'bold', fontSize: '11px'}));
    legendBox.add(ramp(DELTA, '-' + maxDelta, '0', '+' + maxDelta));
  }

  // Legenda principal, acima da galeria inteira.
  galleryLegend.add(title('LEGENDA GERAL | ' + spec.label, '15px', '#812b42'));
  galleryLegend.add(ui.Label('Intervalo da escala visual: ' + absRange, {
    fontWeight: 'bold', fontSize: '12px', color: COLOR_INK
  }));
  galleryLegend.add(ramp(HOT, '0', String(maxAbs / 2), String(maxAbs)));
  if (showDelta) {
    galleryLegend.add(ui.Label('Diferenca V2 - V1 | ' + diffRange, {
      fontWeight: 'bold', fontSize: '12px', color: COLOR_INK
    }));
    galleryLegend.add(ramp(DELTA, '-' + maxDelta, '0', '+' + maxDelta));
    legendKey(galleryLegend, '', 'Diferenca exatamente zero = transparente', true);
  } else {
    legendKey(galleryLegend, '', 'Valores iguais a zero = transparentes', true);
  }
  legendKey(galleryLegend, BRAZIL_BORDER_COLOR, 'Limite do Brasil (preto)', false);
  legendKey(galleryLegend, STATE_BORDER_COLOR, 'Limites estaduais (cinza)', false);
  if (showQc && metric !== 'heatwave_days') {
    legendKey(galleryLegend, QC_COLOR,
      'Ciano: QC = 1; duracoes/eventos censurados', false);
  }
  galleryLegend.add(note('Faixa fixa de visualizacao, nao minimo/maximo observado. ' +
      'Mesma escala para todos os meses, anos e versoes. ' +
      'Valores acima do limite aparecem na ultima cor. ' +
      'Transparencia tambem pode indicar dados ausentes/mascarados. ' +
      'Divisas: FAO GAUL 2015 (referencia visual, nao IBGE).'));
}

function renderGallery() {
  cancelThumbQueue();
  THUMB_DELAY_MS = Number(speedSelect.getValue());
  gallery.clear();
  detailBox.clear();
  detailBox.add(note('Clique em uma miniatura pronta para ampliar.'));

  var dataset = datasetSelect.getValue();
  var periodMode = periodSelect.getValue();
  var selectedYear = yearSelect.getValue();
  var versions = versionSelect.getValue() === 'AMBAS' ?
      ['V1', 'V2'] : [versionSelect.getValue()];
  var metric = metricSelect.getValue();
  var conf = METRICS[metric];
  var monthly = periodMode === 'MENSAL';
  var absMax = numericScale(maxText.getValue(), monthly ? conf.monthly : conf.annual);
  var deltaMax = numericScale(diffText.getValue(),
      monthly ? conf.diffMonthly : conf.diffAnnual);
  var qcOn = qcCheckbox.getValue();
  var showDelta = diffCheckbox.getValue() && versions.length === 2;
  var periods = requestedPeriods(periodMode, selectedYear);

  status.setValue('Consultando inventario das colecoes...');
  galleryInfo.setValue('Preparando ' + periods.length + ' periodos; metrica: ' +
      conf.label + '.');
  buildLegend(metric, absMax, deltaMax, showDelta, qcOn);

  var inventories = {};
  var errors = [];
  versions.forEach(function(version) {
    var collection = COLLECTIONS[dataset][version];
    inventories[version] = inventory(collection);
    if (inventories[version].error) {
      errors.push(version + ' — ' + collection + '\n' +
          inventories[version].error);
    }
  });
  if (errors.length) {
    // Nao chamar ausencia de asset de "pendente" se a lista deu erro.
    galleryInfo.setValue('Nao foi possivel verificar todas as colecoes.');
    errors.forEach(function(error) {
      gallery.add(ui.Label(error, {
        fontSize: '12px', color: '#a22c3e', whiteSpace: 'pre-wrap'
      }));
    });
    status.setValue('Falha ao ler inventario. Verifique acesso ao projeto e tente novamente.');
    return;
  }

  var counts = {};
  versions.forEach(function(version) {
    counts[version] = {present: 0, missing: 0};
    var collectionId = COLLECTIONS[dataset][version];
    appendGrid(gallery, version + ' · ' +
        (version === 'V1' ? 'Metodo original' : 'TX90 / WSDI'),
        periods, function(period) {
      var asset = inventories[version].images[period.file];
      if (!asset) {
        counts[version].missing++;
        return thumbnailCard(period.label, null, null,
            'PENDENTE\n' + period.file);
      }
      counts[version].present++;
      return thumbnailCard(period.label, asset,
          visualImage(asset, metric, absMax, qcOn), null,
          visualImage(asset, metric, absMax, qcOn, true));
    });
  });

  var paired = 0;
  if (showDelta) {
    appendGrid(gallery, 'DIFERENCA · V2 - V1', periods, function(period) {
      var assetV1 = inventories.V1.images[period.file];
      var assetV2 = inventories.V2.images[period.file];
      if (!assetV1 || !assetV2) {
        return thumbnailCard(period.label, null, null,
            'SEM PAR COMPLETO\nV1 ou V2 pendente');
      }
      paired++;
      return thumbnailCard(period.label + ' · V2 - V1',
          assetV2, differenceImage(assetV1, assetV2, metric, deltaMax, qcOn),
          null, differenceImage(assetV1, assetV2, metric, deltaMax, qcOn, true));
    });
  }

  var summaries = versions.map(function(version) {
    return version + ': ' + counts[version].present + '/' + periods.length +
        ' imagens disponiveis (' + counts[version].missing + ' pendentes)';
  });
  if (showDelta) summaries.push('V2 - V1: ' + paired +
      '/' + periods.length + ' pares completos');
  galleryInfo.setValue(periods.length + (monthly ? ' meses · ' + selectedYear :
      ' anos · 1985-2025') + ' | ' + conf.label + ' | maximo: ' + absMax +
      '\n' + summaries.join(' | '));
  queueContext = 'Inventario: ' + summaries.join(' | ');
  pauseButton.setDisabled(thumbQueue.length === 0);
  updateQueueStatus();
  // Agenda apenas UMA miniatura por vez; os cartoes permanecem visiveis.
  scheduleNextThumb();
}

renderButton.onClick(renderGallery);
side.add(note('Este painel apenas LE os assets finais; nao dispara exports. PNG com 0 transparente e contornos simplificados FAO GAUL. Se alguma imagem quebrar, use Recarregar miniatura.'));

// Inicializa sem disparar 41 requests automaticamente: clique no botao.
galleryInfo.setValue('Escolha galeria, versoes e metrica; clique GERAR / ATUALIZAR.');
buildLegend('heatwave_days', METRICS.heatwave_days.annual,
    METRICS.heatwave_days.diffAnnual, false, false);
gallery.add(note('Sugestao: comece com V2 e 12 meses / 2023. Depois visualize os 41 anos. Para V1 x V2 escolha "Comparar".'));
