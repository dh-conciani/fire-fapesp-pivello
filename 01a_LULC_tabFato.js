var asset = 'users/dh-conciani/help/fire-fapesp/2026-01-22-fire-fapesp-fato';
var features = ee.FeatureCollection(asset);

print('features',features);

var columns = [
  'FEvn_ID',
  'Year',
  'Month',
  'Day',
  'Source',
  'Locatin'
];

var chart = makeTableChart(features, columns, 'FEvn_ID', 300);
print('TABELA CRUA', chart);

// --- --- --- AUXILIAR
var scale = 30;
var years = [
  1985,1986,1987,1988,1989,1990,1991,1992,1993,1994,
  1995,1996,1997,1998,1999,2000,2001,2002,2003,2004,
  2005,2006,2007,2008,2009,2010,2011,2012,2013,2014,
  2015,2016,2017,2018,2019,2020,2021,2022,2023,2024
];
// --- --- --- --- --- MÉTRICAS
// --- --- --- MÉTRICAS HISTÓRICAS SOBRE O FOGO PRÉTERITO
var fire = ee.Image('projects/mapbiomas-public/assets/brazil/fire/collection4_1/mapbiomas_fire_collection41_annual_burned_v1');

var features = features.map(function(feature){
  
  var frequencyHistogram = fire.unmask().reduceRegion({
    reducer:ee.Reducer.first(), 
    geometry:feature.geometry(), 
    scale:scale, 
    // crs, crsTransform, bestEffort, 
    maxPixels:1e13,
    // tileScale
  });
  
  var values = frequencyHistogram.values()
    .slice(0,ee.List(years).indexOf(feature.getNumber('Year').add(1)));
  
  var values_invertida = values.slice(0).reverse();

  var keys = frequencyHistogram.keys()
    .slice(0,ee.List(years).indexOf(feature.getNumber('Year').add(1)));
  
  var keys_invertida = keys.slice(0).reverse();
  
  return feature.set({
    // frequencyHistogram:frequencyHistogram,
    // values:values,
    // keys:keys,
    // 'fogo-values_length':values.length(),
    'fogo-recorrencia':values.reduce('sum'),
    'fogo-frequencia':ee.Number(values.reduce('sum')).divide(values.length()),
    'fogo-primeiro ano':keys.getString(values.indexOf(1)).slice(-4),
    'fogo-ultimo ano':keys_invertida.getString(values_invertida.indexOf(1)).slice(-4),
  });
});

print('features',features.first(),features.limit(3));

print('+ MÉTRICAS HISTÓRICAS SOBRE O FOGO PRÉTERITO', makeTableChart(features, features.first().propertyNames(), 'FEvn_ID', 300));

// --- --- --- MÉTRICAS DA COBERTURA DA VIZINHANÇA
var coverage_nivel2_subset = getCoverageMapBiomas(['nivel2']).nivel2;
print("coverage_nivel2_subset",coverage_nivel2_subset);
var features = features.map(function(feature){
  
  var coverage_year = coverage_nivel2_subset.eeObject.select(ee.String('classification_').cat(ee.String(feature.getNumber('Year').int())))
  
  var legend = coverage_nivel2_subset.legenda;
  
  var area_ha = ee.Image.pixelArea().divide(10000)
  var area_total = area_ha.reduceRegion({
      reducer:ee.Reducer.sum(),
      geometry:feature.geometry().buffer(1000),
      scale:scale,
      maxPixels:1e13,
    }).getNumber('area');
    
  feature = feature.set('cob_ha-AreaTotal',area_total);

  Object.keys(legend).forEach(function(key){
    
    
    var area_coverage = area_ha.multiply(coverage_year.eq(parseInt(key)))
    .reduceRegion({
      reducer:ee.Reducer.sum(),
      geometry:feature.geometry().buffer(1000),
      scale:scale,
      maxPixels:1e13,
    }).getNumber('area');
    
    feature = feature.set('cob_ha-' + legend[key],area_coverage);
    feature = feature.set('cob_percent-' + legend[key],area_coverage.multiply(100).divide(area_total));
    
  });
  return feature;
});
print('features',features.first(),features.limit(3));
print('+ MÉTRICAS DA COBERTURA DA VIZINHANÇA', makeTableChart(features, features.first().propertyNames(), 'FEvn_ID', 300));

// --- --- --- MÉTRICAS DE CLIMA
// Temp. média Max (mês) °C
// Temp. média Min (mês) °C
// Temp. Média do Mês (°c)
// Temp. Média do Dia (°c)
// Temp. Min do Dia (°C)
// Temp. Max do Dia (°C)	

// Chuva Acumulada (12 meses)	
// Chuva Acumulada (3 meses)	
// Chuva Acumulada(1 semana)	

// Temp.  <4 °C (12 meses)	Contagem de eventos de ondas de Calor (12 meses)	
// Contagem de dias de onda de calor (12 meses) 	


// Precipitação Média Anual (mm)	// perguntar amanhã

  // Temp. do ar no momento do fogo (°C)
  // Vento no momento do fogo  (m/s)
  // UR no momento do fogo (%)	
// --- --- MÉTRICAS DE TEMPERATURA

// ee.ImageCollection("ECMWF/ERA5/HOURLY")               // 27830 meters
// ee.ImageCollection("ECMWF/ERA5/DAILY")                // 27830 meters
// ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")      // 11132 meters
// ee.ImageCollection("ECMWF/ERA5_LAND/MONTHLY_BY_HOUR") // 11132 meters


function select_temperature_celsius_by_horly (image){
  return image.select('temperature_2m').subtract(273.15);
}

var hourly = ee.ImageCollection("ECMWF/ERA5_LAND/HOURLY");          // 11132 meters
var daily = ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR"); // 11132 meters

// print('temp_hourly',temp_hourly.first(),temp_hourly.limit(10),temp_hourly.first().bandNames());
print('daily',daily.first(),daily.limit(10),daily.first().bandNames());

// --- --- MÉTRICAS DE PRECIPITAÇAO
function reduceRegion_first (image,geom){
  return image
    .reduceRegion({
      reducer:ee.Reducer.first(),
      geometry:geom,
      scale:scale, 
      maxPixels:1e13, 
    });
}

var features = features.map(function(feature){
  // var feature = features.first();
  
  var year = feature.getNumber('Year').int();
  var month = feature.getNumber('Month').int();
  var day = feature.getString('Day'); day = day.equals('NA') ? '1' : day

  var pointDate_day = ee.Date(ee.String('').cat(year).cat('-').cat(month).cat('-').cat(day));
  var pointDate_month = ee.Date(ee.String('').cat(year).cat('-').cat(month).cat('-01'));
  var pointDate_year = ee.Date(ee.String('').cat(year).cat('-01-01'));
  
  var temp_monthly_col = hourly.filterDate(pointDate_day.advance(-30,'days'),pointDate_day)
      .map(select_temperature_celsius_by_horly)
      .select('temperature_2m');
  // var temp_monthly_col = temp_daily.filterDate(pointDate_day.advance(-15,'days'),pointDate_day.advance(15,'days'));
  var temp_monthly_median = reduceRegion_first(temp_monthly_col.median(),feature.geometry()).get('temperature_2m');
  var temp_monthly_min = reduceRegion_first(temp_monthly_col.min(),feature.geometry()).get('temperature_2m');
  var temp_monthly_max = reduceRegion_first(temp_monthly_col.max(),feature.geometry()).get('temperature_2m');


  var temp_daily_col = hourly.filterDate(pointDate_day,pointDate_day.advance(1,'days'))
    .map(select_temperature_celsius_by_horly)
    .select('temperature_2m');
  var temp_daily_median = reduceRegion_first(temp_daily_col.median(),feature.geometry()).get('temperature_2m');
  var temp_daily_min = reduceRegion_first(temp_daily_col.min(),feature.geometry()).get('temperature_2m');
  var temp_daily_max = reduceRegion_first(temp_daily_col.max(),feature.geometry()).get('temperature_2m');

// Chuva Acumulada (12 meses)	
// Chuva Acumulada (3 meses)	
// Chuva Acumulada(1 semana)	

  var precipitation_daily_col = daily.select('total_precipitation_sum');
  var precipitation_1week = reduceRegion_first(precipitation_daily_col.filterDate(pointDate_day.advance(-7,'days'),pointDate_day).sum(),feature.geometry()).get('total_precipitation_sum');
  var precipitation_3months = reduceRegion_first(precipitation_daily_col.filterDate(pointDate_day.advance(-3,'months'),pointDate_day).sum(),feature.geometry()).get('total_precipitation_sum');
  var precipitation_1year = reduceRegion_first(precipitation_daily_col.filterDate(pointDate_day.advance(-1,'year'),pointDate_day).sum(),feature.geometry()).get('total_precipitation_sum');
  
  
  ///////////////
  var count_cold_fronts = reduceRegion_first(
    daily
      .filterDate(pointDate_day.advance(-1,'year'),pointDate_day)
      .map(function select_temperature_celsius_by_horly (image){
          return image.select('temperature_2m_min').subtract(273.15);
        })
      .map(function(image){return image.lte(4)})
      .sum(),
    feature.geometry()).get('temperature_2m_min');


  //////////////
  
  feature = feature.set({
    'temp-yearly_count_cold_fronts': count_cold_fronts,
    'temp-monthly_median': temp_monthly_median,
    'temp-monthly_min': temp_monthly_min,
    'temp-monthly_max': temp_monthly_max,
    'temp-daily_median': temp_daily_median,
    'temp-daily_min': temp_daily_min,
    'temp-daily_max': temp_daily_max,

    'precipitation_1week':precipitation_1week,
    'precipitation_3months':precipitation_3months,
    'precipitation_1year':precipitation_1year,
  });

  return feature;
  // print(feature);
});
print('features',features.first(),features.limit(3));
print('+ MÉTRICAS DE CLIMA', makeTableChart(features, features.first().propertyNames(), 'FEvn_ID', 300));


// --- --- --- MÉTRICAS DE ACUMULO DE MATERIAL COMBUSTIVEL
var features = years.map(function(year){
  return features.filter(ee.Filter.eq('Year',year))
    .map(function(feature){
      var month = feature.getNumber('Month').int();
      var day = feature.getString('Day'); day = day.equals('NA') ? '1' : day
      var landsat_year_collection = getLandsat(year,month,day,feature);
      var combustivel_bands = landsat_year_collection
        .unmask().reduceRegion({
        reducer:ee.Reducer.first(), 
        geometry:feature.geometry(), 
        scale:scale, 
        // crs, crsTransform, bestEffort, 
        maxPixels:1e13,
        // tileScale
      });
      
    return feature.set({
      // 'combustivel-bands':combustivel_bands,
      'combustivel-npv':combustivel_bands.getNumber('npv'),
      'combustivel-gv':combustivel_bands.getNumber('gv'),
      'combustivel-soil':combustivel_bands.getNumber('soil'),
    });
  });
});
features = ee.FeatureCollection(features).flatten();
print('features',features.first(),features.limit(3));
print('+ MÉTRICAS DE ACUMULO DE MATERIAL COMBUSTIVEL', makeTableChart(features, features.first().propertyNames(), 'FEvn_ID', 300));


var description = '2026-01-22-fato-lulc-climate-stats'
var folder = 'fire-fapesp'
Export.table.toDrive({
  collection:features,
  description:description,
  folder:folder,
  fileNamePrefix:description,
  fileFormat:'csv',
  // selectors:,
  // maxVertices:,
  // priority:
})
                          
// --- --- --- --- --- FUNÇÕES AUXILIARES
// --- --- --- GRAFICO DE TABELA GENERICO
function makeTableChart(fc, columns, axisColumn, pageSize) {
  var selected = fc.select(columns);
  var chart;

  if (axisColumn) {
    chart = ui.Chart.feature.byFeature(selected, axisColumn);
  } else {
    chart = ui.Chart.feature.byFeature(selected);
  }

  chart
    .setChartType('Table')
    .setOptions({
      page: 'enable',
      pageSize: pageSize || 300
    });

  chart.style().set({ stretch: 'horizontal', maxHeight:'300px' });

  return chart;
}

// preparar coleção de imagens do ano landsat solicitado
function getLandsat(year,month,day,point){
  // -------------------------------------------------------------------
  // DATASETS (LANDSAT COLLECTION 2 T1 L2)
  // -------------------------------------------------------------------
  // Load the block list module for Landsat

  var datasets = {
    // LANDSAT COLLECTION 02 TIER 1 LEVEL 2
    LC08: {
      address: 'LANDSAT/LC08/C02/T1_L2',
      pre_processings: function (col) {
        return col
          // .filter(ee.Filter.inList('system:index', blockList_landsat).not())
          .map(function (image) {
            image = clipBoard_Landsat(image);
            image = corrections_LS89_col2(image);
            image = addBand_NBR(image);
            image = fractions(image);
            return image;
          });
      },
    },
    LC09: {
      address: 'LANDSAT/LC09/C02/T1_L2',
      pre_processings: function (col) {
        return col
          // .filter(ee.Filter.inList('system:index', blockList_landsat).not())
          .map(function (image) {
            image = clipBoard_Landsat(image);
            image = corrections_LS89_col2(image);
            image = addBand_NBR(image);
            image = fractions(image);
            return image;
          });
      },
    },
    LT05: {
      address: 'LANDSAT/LT05/C02/T1_L2',
      pre_processings: function (col) {
        return col
          // .filter(ee.Filter.inList('system:index', blockList_landsat).not())
          .map(function (image) {
            image = clipBoard_Landsat(image);
            image = corrections_LS57_col2(image);
            image = addBand_NBR(image);
            image = fractions(image);
            return image;
          });
      },
    },
    LE07: {
      address: 'LANDSAT/LE07/C02/T1_L2',
      pre_processings: function (col) {
        return col
          // .filter(ee.Filter.inList('system:index', blockList_landsat).not())
          .map(function (image) {
            image = clipBoard_Landsat(image);
            image = corrections_LS57_col2(image);
            image = addBand_NBR(image);
            image = fractions(image);
            return image;
          });
      },
    },
  };
  
  var yearToConstelation = {
    1985: ['LT05'],
    1986: ['LT05'],
    1987: ['LT05'],
    1988: ['LT05'],
    1989: ['LT05'],
    1990: ['LT05'],
    1991: ['LT05'],
    1992: ['LT05'],
    1993: ['LT05'],
    1994: ['LT05'],
    1995: ['LT05'],
    1996: ['LT05'],
    1997: ['LT05'],
    1998: ['LT05'],
  
    1999: ['LT05', 'LE07'],
    2000: ['LT05', 'LE07'],
    2001: ['LT05', 'LE07'],
    2002: ['LT05', 'LE07'],
    2003: ['LT05', 'LE07'],
    2004: ['LT05', 'LE07'],
    2005: ['LT05', 'LE07'],
    2006: ['LT05', 'LE07'],
    2007: ['LT05', 'LE07'],
    2008: ['LT05', 'LE07'],
    2009: ['LT05', 'LE07'],
    2010: ['LT05', 'LE07'],
    2011: ['LT05', 'LE07'],
    2012: ['LT05', 'LE07'],
  
    2013: ['LE07', 'LC08'],
    2014: ['LE07', 'LC08'],
    2015: ['LE07', 'LC08'],
    2016: ['LE07', 'LC08'],
    2017: ['LE07', 'LC08'],
    2018: ['LE07', 'LC08'],
    2019: ['LE07', 'LC08'],
    2020: ['LE07', 'LC08'],
    2021: ['LE07', 'LC08'],
  
    2022: ['LC09', 'LC08'],
    2023: ['LC09', 'LC08'],
    2024: ['LC09', 'LC08']
  };
  
  var constelations = yearToConstelation[year];
  
  var images;
  // print(year,month,day,point)
  var end = ee.Date(ee.String('').cat(''+year).cat('-').cat(month).cat('-').cat(day));
  var start = end.advance(-3,'month');  
  constelations.forEach(function(constelation){
    var obj = datasets[constelation];
    var imgs = obj.pre_processings(ee.ImageCollection(obj.address)
      .filterDate(start,end));
    images = images === undefined? imgs : images.merge(imgs);
  });
  
  // return images.select(['npv','gv','soil']).median();
  return images.median();
  
  
  // -------------------------------------------------------------------
  // --- Functions for cloud masking, radiometric corrections
  
  function corrections_LS89_col2(image) {
    // Radiometric correction for optical bands
    var opticalBands = image.select('SR_B.*').multiply(0.0000275).add(-0.2);
    opticalBands = opticalBands.multiply(10000)
      .subtract(0.0000275 * 0.2 * 1e5 * 100)
      .round()
      .divide(10000);
  
    // Radiometric correction for thermal bands
    var thermalBands = image.select('ST_B.*').multiply(0.00341802).add(149.0);
  
    // Return the image with corrected bands
    image = image.addBands(opticalBands, null, true).addBands(thermalBands, null, true);
  
    // Cloud masking
    var qa = image.select('QA_PIXEL');
    var cloud = qa.bitwiseAnd(1 << 3)
      .and(qa.bitwiseAnd(1 << 9))
      .or(qa.bitwiseAnd(1 << 4));
    var good_pixel = qa.bitwiseAnd(1 << 6).or(qa.bitwiseAnd(1 << 7));
  
    var radsatQA = image.select('QA_RADSAT');
    var saturated = radsatQA.bitwiseAnd(1 << 0).or(radsatQA.bitwiseAnd(1 << 1))
      .or(radsatQA.bitwiseAnd(1 << 2)).or(radsatQA.bitwiseAnd(1 << 3))
      .or(radsatQA.bitwiseAnd(1 << 4)).or(radsatQA.bitwiseAnd(1 << 5))
      .or(radsatQA.bitwiseAnd(1 << 6));
  
    var negative_mask = image.select(['SR_B1']).gt(0).and(
      image.select(['SR_B2']).gt(0)).and(
      image.select(['SR_B3']).gt(0)).and(
      image.select(['SR_B4']).gt(0)).and(
      image.select(['SR_B5']).gt(0)).and(
      image.select(['SR_B7']).gt(0));
  
    image = image
      .updateMask(cloud.not())
      .updateMask(good_pixel)
      .updateMask(saturated.not())
      .updateMask(negative_mask);
  
    // Correction of band names to default
    var oldBands = ['SR_B2', 'SR_B3', 'SR_B4', 'SR_B5', 'SR_B6', 'SR_B7'];
    var newBands = ['blue', 'green', 'red', 'nir', 'swir1', 'swir2'];
  
    image = image.select(oldBands, newBands);
  
    return image.float();
  }
  
  // - Function for cloud and radiometric correction for Landsat 5 and 7 images
  function corrections_LS57_col2 (image){
    var opticalBands = image.select('SR_B.*').multiply(0.0000275).add(-0.2);
    var thermalBands = image.select('ST_B.*').multiply(0.00341802).add(149.0);
    
    image = image.addBands(opticalBands, null, true)
                .addBands(thermalBands, null, true);
                
    // mascara de nuvem
    var cloudShadowBitMask = (1 << 3);
    var cloudsBitMask = (1 << 5);
  
    var qa = image.select('QA_PIXEL');
    var mask = qa.bitwiseAnd(cloudShadowBitMask).eq(0)
        .and(qa.bitwiseAnd(cloudsBitMask).eq(0));
  
    // mascara de ruídos, saturação radiométrica
    function bitwiseExtract(value, fromBit, toBit) {
      if (toBit === undefined)
        toBit = fromBit;
      var maskSize = ee.Number(1).add(toBit).subtract(fromBit);
      var mask = ee.Number(1).leftShift(maskSize).subtract(1);
      return value.rightShift(fromBit).bitwiseAnd(mask);
    }
  
    var clear = bitwiseExtract(qa, 6); // 1 if clear
    var water = bitwiseExtract(qa, 7); // 1 if water
  
    var radsatQA = image.select('QA_RADSAT');
    var band5Saturated = bitwiseExtract(radsatQA, 4); // 0 if band 5 is not saturated
    var anySaturated = bitwiseExtract(radsatQA, 0, 6); // 0 if no bands are saturated
  
    var mask_saturation = clear
      .or(water)
      .and(anySaturated.not());
    
    // is visible bands with negative reflectance? 
    var negative_mask = image.select(['SR_B1']).gt(0).and(
      image.select(['SR_B2']).gt(0)).and(
        image.select(['SR_B3']).gt(0)).and(
          image.select(['SR_B4']).gt(0)).and(
            image.select(['SR_B5']).gt(0)).and(
              image.select(['SR_B7']).gt(0));
    
    image = image
      .updateMask(mask)
      .updateMask(mask_saturation)
      .updateMask(negative_mask);
  
    var oldBands = ['SR_B1','SR_B2','SR_B3','SR_B4','SR_B5','SR_B7'];
    var newBands = ['blue','green','red','nir','swir1','swir2'];
    image = image.select(oldBands,newBands);
  
    return image.float();
  }
  
  // -------------------------------------------------------------------
  // Function to calculate spectral mixture (opcional, não usado no mosaico final)
  function fractions(image) {
    var newBands = ['blue', 'green', 'red', 'nir', 'swir1', 'swir2'];
  
    // Select bands and multiply by 10000
    var imageSelected = image.select(newBands).multiply(10000);
  
    // Atmospheric coefficients
    var atm = [805.6, 458.1, 286.8, 168.3, 46.8, 26.6];
  
    // Define endmembers for spectral mixture analysis
    var GV = [119.0, 475.0, 169.0, 6250.0, 2399.0, 675.0];
    var NPV = [1514.0, 1597.0, 1421.0, 3053.0, 7707.0, 1975.0];
    var Soil = [1799.0, 2479.0, 3158.0, 5437.0, 7707.0, 6646.0];
    var Cloud = [4031.0, 8714.0, 7900.0, 8989.0, 7002.0, 6607.0];
  
    // Spectral mixture analysis
    var sma = imageSelected.unmix([GV, NPV, Soil, Cloud])
      .max(0)
      .multiply(100)
      .int16();
  
    // Rename and add bands
    sma = image
      .addBands(sma.select('band_0').rename("gv"))
      .addBands(sma.select('band_1').rename("npv"))
      .addBands(sma.select('band_2').rename("soil"))
      .addBands(sma.select('band_3').rename("cloud"));
  
    // Calculate summed values
    var summed = sma.expression('GV + NPV + SOIL + CLOUD', {
      GV: sma.select('gv'),
      NPV: sma.select('npv'),
      SOIL: sma.select('soil'),
      CLOUD: sma.select('cloud')
    });
  
    // Shade, GVS, NPV + Soil + Cloud
    var shade = summed.subtract(100).abs();
    var gvs = (sma.select(['gv']).divide(summed)).multiply(100);
    var npvSoil = sma.select(['gv']).add(sma.select(['soil'])).add(sma.select('cloud'));
  
    return sma
      .addBands(shade.rename("shade"))
      .addBands(npvSoil.rename("npvSoil"))
      .addBands(gvs.rename("gvs"));
  }
  
  // -------------------------------------------------------------------
  // Add band with normalized burned ratio (NBR)
  function addBand_NBR(image) {
    var exp = '( b("nir") - b("swir2") ) / ( b("nir") + b("swir2") )';
    var minimoNBR = image
      .expression(exp)
      // .add(1)
      .multiply(100)
      .multiply(-1)
      .int16()
      .rename("nbr");
    return image.addBands(minimoNBR);
  }
  
  // Function to clip border of images
  function clipBoard_Landsat(image) {
    return image.updateMask(
      ee.Image().paint(image.geometry().buffer(-3000)).eq(0)
    );
  }

}

function getCoverageMapBiomas(levelsRequested) {

  var coverage = ee.Image('projects/mapbiomas-public/assets/brazil/lulc/collection10/mapbiomas_brazil_collection10_coverage_v2');

  // Dicionário de legendas por nível
  var legend_coverage = {
    nivel0: {
      3: 'Natural',
      15: 'Antrópico',
      33: 'Corpo D’água',
      27: 'Não observado'
    },
    nivel1: {
      1: 'Floresta',
      10: 'Vegetação Herbácea e Arbustiva',
      14: 'Agropecuária',
      22: 'Área não Vegetada',
      26: 'Corpo D’água',
      27: 'Não observado'
    },
    nivel2: {
      3:  'Formação Florestal',
      4:  'Formação Savânica',
      5:  'Mangue',
      6:  'Floresta Alagável',
      49: 'Restinga Arbórea',

      11: 'Campo Alagado e Área Pantanosa',
      12: 'Formação Campestre',
      32: 'Apicum',
      29: 'Afloramento Rochoso',
      50: 'Restinga Herbácea',

      15: 'Pastagem',
      18: 'Agricultura',

      9:  'Silvicultura',
      21: 'Mosaico de Usos',

      23: 'Praia, Duna e Areal',
      24: 'Área Urbanizada',
      30: 'Mineração',
      75: 'Usina Fotovoltaica (beta)',
      25: 'Outras Áreas não Vegetadas',

      33: 'Rio, Lago e Oceano',
      31: 'Aquicultura',

      27: 'Não observado'
    },
    nivel3: {
      3: 'Formação Florestal',
      4: 'Formação Savânica',
      5: 'Mangue',
      6: 'Floresta Alagável',
      49: 'Restinga Arbórea',

      11: 'Campo Alagado e Área Pantanosa',
      12: 'Formação Campestre',
      32: 'Apicum',
      29: 'Afloramento Rochoso',
      50: 'Restinga Herbácea',

      15: 'Pastagem',
      19: 'Lavoura Temporária',
      36: 'Lavoura Perene',

      9:  'Silvicultura',
      21: 'Mosaico de Usos',

      23: 'Praia, Duna e Areal',
      24: 'Área Urbanizada',
      30: 'Mineração',
      75: 'Usina Fotovoltaica (beta)',
      25: 'Outras Áreas não Vegetadas',

      33: 'Rio, Lago e Oceano',
      31: 'Aquicultura',

      27: 'Não observado'
    },
    nivel4: {
      3:  'Formação Florestal',
      4:  'Formação Savânica',
      5:  'Mangue',
      6:  'Floresta Alagável',
      49: 'Restinga Arbórea',
      11: 'Campo Alagado e Área Pantanosa',
      12: 'Formação Campestre',
      32: 'Apicum',
      29: 'Afloramento Rochoso',
      50: 'Restinga Herbácea',
      15: 'Pastagem',
      39: 'Soja',
      20: 'Cana',
      40: 'Arroz',
      62: 'Algodão (beta)',
      41: 'Outras Lavouras Temporárias',
      46: 'Café',
      47: 'Citrus',
      35: 'Dendê',
      48: 'Outras Lavouras Perenes',
      9:  'Silvicultura',
      21: 'Mosaico de Usos',
      23: 'Praia, Duna e Areal',
      24: 'Área Urbanizada',
      30: 'Mineração',
      75: 'Usina Fotovoltaica (beta)',
      25: 'Outras Áreas não Vegetadas',
      33: 'Rio, Lago e Oceano',
      31: 'Aquicultura',
      27: 'Não observado',
      0:  'Sem dado'
    }
  };

  // Arrays da reclassificação
  var oldValues = [3, 4, 5, 6, 49, 11, 12, 32, 29, 50, 15, 39, 20, 40, 62, 41, 46, 47, 35, 48, 9, 21, 23, 24, 30, 75, 25, 33, 31, 27, 0];
  var newValuesByLevel = {
    nivel4: oldValues,
    nivel3: [3, 4, 5, 6, 49, 11, 12, 32, 29, 50, 15, 19, 19, 19, 19, 19, 36, 36, 36, 36, 9, 21, 23, 24, 30, 75, 25, 33, 31, 27, 27],
    nivel2: [3, 4, 5, 6, 49, 11, 12, 32, 29, 50, 15, 18, 18, 18, 18, 18, 18, 18, 18, 18, 9, 21, 23, 24, 30, 75, 25, 33, 31, 27, 27],
    nivel1: [1, 1, 1, 1, 1, 10, 10, 10, 10, 10, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 22, 22, 22, 22, 22, 26, 26, 27, 27],
    nivel0: [3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 33, 15, 27, 27]
  };

  // Se não passar argumento → retorna todos os níveis
  if (!levelsRequested || levelsRequested.length === 0) {
    levelsRequested = ['nivel0', 'nivel1', 'nivel2', 'nivel3', 'nivel4'];
  }

  // Função de reclassificação
  function reclass(img, oldVals, newVals) {
    var out = img.multiply(0);
    oldVals.forEach(function(v, i) {
      out = out.where(img.eq(v), newVals[i]);
    });
    return out;
  }

  // Constrói o objeto de retorno
  var out = {};
  levelsRequested.forEach(function(level) {
    var img = level === 'nivel4'
      ? coverage // nível 4 é original
      : reclass(coverage, oldValues, newValuesByLevel[level]);

    out[level] = {
      eeObject: img,
      legenda: legend_coverage[level]
    };
  });

  return out;
}
