var asset = 'users/dh-conciani/help/fire-fapesp/2026-08-21-fire-fapesp-fato';
var features = ee.FeatureCollection(asset);

var columns = [
  'Day',
  'Fr_C_ID',
  'Fr_E_ID',
  'Month',
  'Site',
  'Yr_f_f_'
];

var chart = makeTableChart(features, columns, 'Fr_C_ID', 300);
print('TABELA CRUA', chart);

// --- --- --- AUXILIAR
var scale = 30;
var years = [
  1985,1986,1987,1988,1989,1990,1991,1992,1993,1994,
  1995,1996,1997,1998,1999,2000,2001,2002,2003,2004,
  2005,2006,2007,2008,2009,2010,2011,2012,2013,2014,
  2015,2016,2017,2018,2019,2020,2021,2022,2023,2024,
  2025
];
// --- --- --- --- --- MÉTRICAS
// --- --- --- MÉTRICAS HISTÓRICAS SOBRE O FOGO PRÉTERITO
var fire = ee.Image('projects/mapbiomas-public/assets/brazil/fire/collection5/mapbiomas_fire_collection5_annual_burned_v1');

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
    .slice(0,ee.List(years).indexOf(feature.getNumber('Yr_f_f_').add(1)));
  
  var values_invertida = values.slice(0).reverse();

  var keys = frequencyHistogram.keys()
    .slice(0,ee.List(years).indexOf(feature.getNumber('Yr_f_f_').add(1)));
  
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

print('+ MÉTRICAS HISTÓRICAS SOBRE O FOGO PRÉTERITO', makeTableChart(features, features.first().propertyNames(), 'Fr_E_ID', 300));

// --- --- --- MÉTRICAS DA COBERTURA DA VIZINHANÇA
var coverage_nivel2_subset = getCoverageMapBiomas(['nivel2']).nivel2;
print("coverage_nivel2_subset",coverage_nivel2_subset);
var features = features.map(function(feature){
  
  var coverage_year = coverage_nivel2_subset.eeObject.select(ee.String('classification_').cat(ee.String(feature.getNumber('Yr_f_f_').int())));
  
  var legend = coverage_nivel2_subset.legenda;
  
  var area_ha = ee.Image.pixelArea().divide(10000);
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
print('+ MÉTRICAS DA COBERTURA DA VIZINHANÇA', makeTableChart(features, features.first().propertyNames(), 'Fr_E_ID', 300));

// --- --- --- MÉTRICAS DE ACESSIBILIDADE / ANTROPIZAÇÃO
// ============================================================================
// DISTÂNCIA À ÁREA URBANIZADA + DENSIDADE DE ESTRADAS
//
// URBANO:
// - MapBiomas Collection 11
// - classe 24 = Área Urbanizada
// - usa a classificação do MESMO ANO de Yr_f_f_
// - distância Euclidiana calculada com ee.Image.distance()
// - MapBiomas original = 30 m; grade operacional da distância = 120 m
// - kernel Euclidiano limitado a 30 km
// - se não houver classe 24 até 30 km:
//       urban-distance_m = null
//       urban-distance_gt30km = 1
//       urban-distance_label = '>30 km'
//
// ESTRADAS:
// - GRIP4 Central-South America
// - rede viária estática (não anual)
// - comprimento total de estradas dentro de buffer circular de 1 km
// - densidade = km de estrada / km² de buffer
//
// As métricas são calculadas apenas para combinações únicas:
// - urbano: localização + ano
// - estrada: localização
// e depois associadas novamente a todos os registros.
// ============================================================================

var accessibilityMapBiomas = ee.Image(
  'projects/mapbiomas-public/assets/brazil/lulc/collection11/' +
  'mapbiomas_brazil_collection11_coverage_v3'
);

var accessibilityRoadsGRIP4 = ee.FeatureCollection(
  'projects/sat-io/open-datasets/GRIP4/Central-South-America'
);

var URBAN_CLASS = 24;
var URBAN_SOURCE_SCALE_M = 30;
var URBAN_DISTANCE_SCALE_M = 120; // operational grid for 30-km Euclidean kernel
var URBAN_MAX_DISTANCE_M = 30000;  // 30 km

var ROAD_BUFFER_M = 1000;

// Kernel Euclidiano em metros.
// ee.Image.distance() mascara pixels cuja distância excede o raio do kernel.
// At 30 m, a 30-km radius would create a 2001-pixel-wide kernel,
// exceeding Earth Engine's 512-pixel kernel limit.
// At 120 m, the same 30-km radius is 250 pixels, i.e. 501 pixels wide.
var urbanEuclideanKernel = ee.Kernel.euclidean(
  URBAN_MAX_DISTANCE_M,
  'meters'
);


// ---------------------------------------------------------------------------
// CHAVES PARA EVITAR CÁLCULOS REPETIDOS
// ---------------------------------------------------------------------------

function addAccessibilityKeys(feature) {

  var coords = ee.List(
    feature.geometry().coordinates()
  );

  var lon = ee.Number(coords.get(0));
  var lat = ee.Number(coords.get(1));
  var year = feature.getNumber('Yr_f_f_').int();

  var locationKey = lon.format('%.6f')
    .cat('_')
    .cat(lat.format('%.6f'));

  var accessKey = locationKey
    .cat('_')
    .cat(year.format());

  return feature.set({
    '_access_location_key': locationKey,
    '_access_year_key': accessKey
  });
}

var featuresWithAccessibilityKeys = features.map(
  addAccessibilityKeys
);

var uniqueAccessibilityLocationYears =
  featuresWithAccessibilityKeys
    .distinct(['_access_year_key']);

var uniqueAccessibilityLocations =
  featuresWithAccessibilityKeys
    .distinct(['_access_location_key']);

print(
  'ACCESSIBILITY optimization: rows / unique location-year / unique locations',
  featuresWithAccessibilityKeys.size(),
  uniqueAccessibilityLocationYears.size(),
  uniqueAccessibilityLocations.size()
);


// ---------------------------------------------------------------------------
// 1) DISTÂNCIA EUCLIDIANA À CLASSE 24, LIMITADA A 30 KM
// ---------------------------------------------------------------------------
// Para cada ano:
//   1. seleciona classification_<ano>
//   2. cria máscara binária de classe 24
//   3. calcula distância Euclidiana apenas até 30 km
//   4. amostra somente os pontos daquele ano
//
// Não existe cálculo de distância além de 30 km.

var accessibilityYears = ee.List(
  uniqueAccessibilityLocationYears
    .aggregate_array('Yr_f_f_')
).distinct().sort();

var urbanMetricsByYear = ee.FeatureCollection(
  accessibilityYears.map(function(y) {

    y = ee.Number(y).int();

    var pointsThisYear =
      uniqueAccessibilityLocationYears.filter(
        ee.Filter.eq('Yr_f_f_', y)
      );

    var urbanMask30m = accessibilityMapBiomas
      .select(
        ee.String('classification_')
          .cat(y.format())
      )
      .eq(URBAN_CLASS)
      .unmask(0)
      .rename('urban');

    // Aggregate the original 30-m MapBiomas urban mask to 120 m.
    // MAX preserves urban presence: if any 30-m class-24 pixel occurs
    // inside the 120-m cell, that cell is treated as urban.
    //
    // This is necessary because a 30-km Euclidean kernel at 30 m would
    // be 2001 pixels wide, above Earth Engine's 512-pixel kernel limit.
    var urbanMask120m = urbanMask30m
      .reduceResolution({
        reducer: ee.Reducer.max(),
        maxPixels: 64
      })
      .reproject({
        crs: urbanMask30m.projection(),
        scale: URBAN_DISTANCE_SCALE_M
      })
      .rename('urban');

    // Euclidean distance is evaluated only up to 30 km.
    // Pixels farther than 30 km remain masked.
    var urbanDistance = urbanMask120m
      .distance(
        urbanEuclideanKernel,
        false
      )
      .rename('urban-distance_m');

    return pointsThisYear.map(function(feature) {

      var d = urbanDistance.reduceRegion({
        reducer: ee.Reducer.first(),
        geometry: feature.geometry(),
        scale: URBAN_DISTANCE_SCALE_M,
        maxPixels: 1e8,
        tileScale: 4
      }).get('urban-distance_m');

      var beyond30km = ee.Algorithms.IsEqual(
        d,
        null
      );

      return ee.Feature(null, {
        '_access_year_key':
          feature.get('_access_year_key'),

        // Exact numeric distance only when an urban pixel occurs <=30 km.
        'urban-distance_m':
          ee.Algorithms.If(
            beyond30km,
            null,
            d
          ),

        // Useful numeric censoring flag for statistical analysis.
        'urban-distance_gt30km':
          ee.Algorithms.If(
            beyond30km,
            1,
            0
          ),

        // Human-readable output requested.
        'urban-distance_label':
          ee.Algorithms.If(
            beyond30km,
            '>30 km',
            '<=30 km'
          ),

        // Metadata describing the operational distance calculation.
        'urban-distance_resolution_m':
          URBAN_DISTANCE_SCALE_M,

        'urban-distance_method':
          'MapBiomas class 24; Euclidean kernel <=30 km; 120 m operational grid'
      });
    });
  })
).flatten();

print(
  'URBAN DISTANCE CHECK',
  urbanMetricsByYear.first(),
  urbanMetricsByYear.limit(3)
);


// ---------------------------------------------------------------------------
// 2) DENSIDADE DE ESTRADAS GRIP4 NO BUFFER DE 1 KM
// ---------------------------------------------------------------------------
// GRIP4 é estático. Portanto esta métrica NÃO varia com Yr_f_f_.
//
// Para cada localização única:
//   - cria buffer circular de 1 km;
//   - seleciona somente estradas que interceptam o buffer;
//   - recorta cada linha ao buffer;
//   - soma o comprimento em metros;
//   - calcula km de estrada / km² de buffer.

var roadMetricsUnique = uniqueAccessibilityLocations.map(
  function(feature) {

    var buffer = feature.geometry().buffer(
      ROAD_BUFFER_M,
      20
    );

    var localRoads = accessibilityRoadsGRIP4
      .filterBounds(buffer);

    var roadLengths = localRoads.map(
      function(road) {

        var clipped = ee.Feature(road)
          .geometry()
          .intersection(
            buffer,
            20
          );

        return ee.Feature(null, {
          '_road_length_m':
            clipped.length(20)
        });
      }
    );

    var totalRoadLengthM = ee.Number(
      ee.Algorithms.If(
        localRoads.size().gt(0),
        roadLengths.aggregate_sum(
          '_road_length_m'
        ),
        0
      )
    );

    var bufferAreaKm2 = buffer
      .area(20)
      .divide(1e6);

    var roadDensityKmPerKm2 =
      totalRoadLengthM
        .divide(1000)
        .divide(bufferAreaKm2);

    return ee.Feature(null, {
      '_access_location_key':
        feature.get('_access_location_key'),

      'road-GRIP4_length_1km_m':
        totalRoadLengthM,

      'road-GRIP4_density_1km_km_per_km2':
        roadDensityKmPerKm2
    });
  }
);

print(
  'ROAD DENSITY CHECK',
  roadMetricsUnique.first(),
  roadMetricsUnique.limit(3)
);


// ---------------------------------------------------------------------------
// 3) JOIN URBANO + ESTRADAS DE VOLTA A TODOS OS REGISTROS
// ---------------------------------------------------------------------------

// --- urbano: localização + ano
var urbanJoin = ee.Join.saveFirst(
  '_urban_match'
);

var featuresWithUrban = ee.FeatureCollection(
  urbanJoin.apply(
    featuresWithAccessibilityKeys,
    urbanMetricsByYear,
    ee.Filter.equals({
      leftField: '_access_year_key',
      rightField: '_access_year_key'
    })
  )
).map(function(feature) {

  feature = ee.Feature(feature);

  var match = feature.get('_urban_match');

  var urbanDict = ee.Dictionary(
    ee.Algorithms.If(
      ee.Algorithms.IsEqual(match, null),
      ee.Dictionary({
        'urban-distance_m': null,
        'urban-distance_gt30km': null,
        'urban-distance_label': null,
        'urban-distance_resolution_m': null,
        'urban-distance_method': null
      }),
      ee.Feature(match).toDictionary([
        'urban-distance_m',
        'urban-distance_gt30km',
        'urban-distance_label',
        'urban-distance_resolution_m',
        'urban-distance_method'
      ])
    )
  );

  return feature.set(urbanDict);
});


// --- estradas: localização
var roadJoinAccessibility = ee.Join.saveFirst(
  '_road_match'
);

features = ee.FeatureCollection(
  roadJoinAccessibility.apply(
    featuresWithUrban,
    roadMetricsUnique,
    ee.Filter.equals({
      leftField: '_access_location_key',
      rightField: '_access_location_key'
    })
  )
).map(function(feature) {

  feature = ee.Feature(feature);

  var match = feature.get('_road_match');

  var roadDict = ee.Dictionary(
    ee.Algorithms.If(
      ee.Algorithms.IsEqual(match, null),
      ee.Dictionary({
        'road-GRIP4_length_1km_m': null,
        'road-GRIP4_density_1km_km_per_km2': null
      }),
      ee.Feature(match).toDictionary([
        'road-GRIP4_length_1km_m',
        'road-GRIP4_density_1km_km_per_km2'
      ])
    )
  );

  var result = feature.set(roadDict);

  // Remove propriedades internas usadas somente nos joins.
  return result.select(
    result.propertyNames()
      .remove('_access_location_key')
      .remove('_access_year_key')
      .remove('_urban_match')
      .remove('_road_match')
  );
});

print(
  'ACCESSIBILITY FINAL CHECK',
  features.first(),
  features.limit(3)
);



// --- --- --- MÉTRICAS DE CLIMA
// FAST VERSION:
// 1) compute climate only once for each UNIQUE point + reference date;
// 2) use ERA5-Land MONTHLY_AGGR for calendar-year and 1991-2020 precipitation;
// 3) keep the exact 10-year search for the preceding dry spell;
// 4) no heat-wave or cold-wave calculations.

var climateScale = 11132;
var DRY_DAY_THRESHOLD_MM = 1.0;
var DRY_LOOKBACK_YEARS = 10;

var hourly = ee.ImageCollection('ECMWF/ERA5_LAND/HOURLY');
var daily = ee.ImageCollection('ECMWF/ERA5_LAND/DAILY_AGGR');
var monthly = ee.ImageCollection('ECMWF/ERA5_LAND/MONTHLY_AGGR');

// ---------------------------------------------------------------------------
// HELPERS
// ---------------------------------------------------------------------------

function select_temperature_celsius_by_hourly(image) {
  return image
    .select('temperature_2m')
    .subtract(273.15)
    .rename('temperature_2m');
}

function reduceRegion_first_climate(image, geom) {
  return image.reduceRegion({
    reducer: ee.Reducer.first(),
    geometry: geom,
    scale: climateScale,
    maxPixels: 1e13
  });
}

function getClimatePointDate(feature) {
  var year = feature.getNumber('Yr_f_f_').int();
  var month = feature.getNumber('Month').int();
  var dayText = ee.String(feature.get('Day'));

  var day = ee.Number(ee.Algorithms.If(
    dayText.compareTo('NA').eq(0),
    1,
    ee.Number.parse(dayText)
  )).int();

  return ee.Date.fromYMD(year, month, day);
}

// ---------------------------------------------------------------------------
// PRECIPITATION IN MILLIMETRES
// ---------------------------------------------------------------------------

var precipitationDailyMM = daily.map(function(image) {
  return image
    .select('total_precipitation_sum')
    .multiply(1000)
    .max(0)
    .rename('precip_mm')
    .copyProperties(image, ['system:time_start']);
});

// MONTHLY_AGGR already contains the monthly sum of flow variables.
// This is much cheaper for long climatological/calendar-year calculations.
var precipitationMonthlyMM = monthly.map(function(image) {
  return image
    .select('total_precipitation_sum')
    .multiply(1000)
    .max(0)
    .rename('precip_mm')
    .copyProperties(image, ['system:time_start']);
});

// 360 monthly images rather than ~10,958 daily images.
var meanAnnualPrecip1991_2020 = precipitationMonthlyMM
  .filterDate('1991-01-01', '2021-01-01')
  .sum()
  .divide(30)
  .rename('precip_mean_annual_1991_2020_mm');

// ---------------------------------------------------------------------------
// ADD A CLIMATE KEY AND REMOVE DUPLICATE CLIMATE REQUESTS
// ---------------------------------------------------------------------------
// Climate values depend only on:
//   point geometry + reference date.
//
// Many rows in the source table have the same point/date. Compute those once
// and join the resulting climate properties back to every original row.

var featuresWithClimateKey = features.map(function(feature) {
  var coords = ee.List(feature.geometry().coordinates());
  var lon = ee.Number(coords.get(0));
  var lat = ee.Number(coords.get(1));
  var date = getClimatePointDate(feature);

  var key = lon.format('%.6f')
    .cat('_')
    .cat(lat.format('%.6f'))
    .cat('_')
    .cat(date.format('YYYYMMdd'));

  return feature.set('_climate_key', key);
});

var climateUniqueInput = featuresWithClimateKey
  .distinct(['_climate_key']);

print(
  'CLIMATE optimization: original rows / unique point+date rows',
  featuresWithClimateKey.size(),
  climateUniqueInput.size()
);

// ---------------------------------------------------------------------------
// CONSECUTIVE DRY DAYS IMMEDIATELY BEFORE REFERENCE DATE
// ---------------------------------------------------------------------------
// Exact configured lookback is retained: 10 years.
// Only unique point/date combinations are evaluated.

function consecutiveDryDaysBefore(feature, pointDate) {
  var geom = feature.geometry();
  var searchStart = pointDate.advance(-DRY_LOOKBACK_YEARS, 'year');

  var lastWetDateImage = precipitationDailyMM
    .filterDate(searchStart, pointDate)
    .map(function(image) {
      var wet = image.select('precip_mm')
        .gte(DRY_DAY_THRESHOLD_MM);

      return image
        .select('precip_mm')
        .multiply(0)
        .add(image.date().millis())
        .toDouble()
        .rename('last_wet_millis')
        .updateMask(wet);
    })
    .max();

  var lastWetMillis = reduceRegion_first_climate(
    lastWetDateImage,
    geom
  ).get('last_wet_millis');

  return ee.Algorithms.If(
    ee.Algorithms.IsEqual(lastWetMillis, null),
    null,
    pointDate
      .difference(ee.Date(ee.Number(lastWetMillis)), 'day')
      .subtract(1)
      .max(0)
  );
}

// ---------------------------------------------------------------------------
// COMPUTE CLIMATE ONLY FOR UNIQUE POINT + DATE COMBINATIONS
// ---------------------------------------------------------------------------

var climateComputed = climateUniqueInput.map(function(feature) {

  var geom = feature.geometry();
  var pointDate = getClimatePointDate(feature);
  var pointDateEnd = pointDate.advance(1, 'day');

  var year = feature.getNumber('Yr_f_f_').int();
  var calendarYearStart = ee.Date.fromYMD(year, 1, 1);
  var calendarYearEnd = calendarYearStart.advance(1, 'year');

  // -------------------------------------------------------------------------
  // TEMPERATURE: PREVIOUS 30 DAYS
  // -------------------------------------------------------------------------

  var tempMonthlyCol = hourly
    .filterDate(pointDate.advance(-30, 'day'), pointDate)
    .map(select_temperature_celsius_by_hourly)
    .select('temperature_2m');

  var tempMonthlyMedian = reduceRegion_first_climate(
    tempMonthlyCol.median(), geom
  ).get('temperature_2m');

  var tempMonthlyMin = reduceRegion_first_climate(
    tempMonthlyCol.min(), geom
  ).get('temperature_2m');

  var tempMonthlyMax = reduceRegion_first_climate(
    tempMonthlyCol.max(), geom
  ).get('temperature_2m');

  // -------------------------------------------------------------------------
  // TEMPERATURE: REFERENCE DAY
  // -------------------------------------------------------------------------

  var tempDailyCol = hourly
    .filterDate(pointDate, pointDateEnd)
    .map(select_temperature_celsius_by_hourly)
    .select('temperature_2m');

  var tempDailyMedian = reduceRegion_first_climate(
    tempDailyCol.median(), geom
  ).get('temperature_2m');

  var tempDailyMin = reduceRegion_first_climate(
    tempDailyCol.min(), geom
  ).get('temperature_2m');

  var tempDailyMax = reduceRegion_first_climate(
    tempDailyCol.max(), geom
  ).get('temperature_2m');

  // -------------------------------------------------------------------------
  // ROLLING PRECIPITATION: exact daily windows
  // -------------------------------------------------------------------------

  var precipitation1Week = reduceRegion_first_climate(
    precipitationDailyMM
      .filterDate(pointDate.advance(-7, 'day'), pointDate)
      .sum(),
    geom
  ).get('precip_mm');

  var precipitation3Months = reduceRegion_first_climate(
    precipitationDailyMM
      .filterDate(pointDate.advance(-3, 'month'), pointDate)
      .sum(),
    geom
  ).get('precip_mm');

  var precipitation1Year = reduceRegion_first_climate(
    precipitationDailyMM
      .filterDate(pointDate.advance(-1, 'year'), pointDate)
      .sum(),
    geom
  ).get('precip_mm');

  // -------------------------------------------------------------------------
  // COMPLETE CALENDAR YEAR: only 12 monthly images
  // -------------------------------------------------------------------------

  var precipitationCalendarYear = reduceRegion_first_climate(
    precipitationMonthlyMM
      .filterDate(calendarYearStart, calendarYearEnd)
      .sum(),
    geom
  ).get('precip_mm');

  // -------------------------------------------------------------------------
  // 1991-2020 MEAN ANNUAL PRECIPITATION: 360 monthly images total
  // -------------------------------------------------------------------------

  var precipitationMeanAnnual = reduceRegion_first_climate(
    meanAnnualPrecip1991_2020,
    geom
  ).get('precip_mean_annual_1991_2020_mm');

  var dryDaysBefore = consecutiveDryDaysBefore(
    feature,
    pointDate
  );

  return feature.set({
    'temp-monthly_median': tempMonthlyMedian,
    'temp-monthly_min': tempMonthlyMin,
    'temp-monthly_max': tempMonthlyMax,

    'temp-daily_median': tempDailyMedian,
    'temp-daily_min': tempDailyMin,
    'temp-daily_max': tempDailyMax,

    'precipitation_1week': precipitation1Week,
    'precipitation_3months': precipitation3Months,
    'precipitation_1year': precipitation1Year,

    'precip-dry_days_before_reference': dryDaysBefore,
    'precip-calendar_year_total_mm': precipitationCalendarYear,
    'precip-mean_annual_1991_2020_mm': precipitationMeanAnnual
  });
});

// ---------------------------------------------------------------------------
// JOIN CLIMATE METRICS BACK TO ALL ORIGINAL ROWS
// ---------------------------------------------------------------------------

var climateMetricNames = [
  'temp-monthly_median',
  'temp-monthly_min',
  'temp-monthly_max',
  'temp-daily_median',
  'temp-daily_min',
  'temp-daily_max',
  'precipitation_1week',
  'precipitation_3months',
  'precipitation_1year',
  'precip-dry_days_before_reference',
  'precip-calendar_year_total_mm',
  'precip-mean_annual_1991_2020_mm'
];

var climateJoin = ee.Join.saveFirst('climate_match');

features = ee.FeatureCollection(
  climateJoin.apply(
    featuresWithClimateKey,
    climateComputed,
    ee.Filter.equals({
      leftField: '_climate_key',
      rightField: '_climate_key'
    })
  )
).map(function(feature) {
  var climateMatch = ee.Feature(feature.get('climate_match'));

  var result = ee.Feature(feature).set(
    climateMatch.toDictionary(climateMetricNames)
  );

  // Remove internal helper properties before final CSV export.
  return result.select(
    result.propertyNames()
      .remove('climate_match')
      .remove('_climate_key')
  );
});

// Avoid forcing an extra full table/chart evaluation in the Console.
print('CLIMATE CHECK - first feature', features.first());


// --- --- --- MÉTRICAS DE ACUMULO DE MATERIAL COMBUSTIVEL
var features = years.map(function(year){
  return features.filter(ee.Filter.eq('Yr_f_f_',year))
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
      'SMA-npv':combustivel_bands.getNumber('npv'),
      'SMA-gv':combustivel_bands.getNumber('gv'),
      'SMA-soil':combustivel_bands.getNumber('soil'),
      'SMA-cloud':combustivel_bands.getNumber('cloud'),
      'SMA-shade':combustivel_bands.getNumber('shade'),
      'SMA_gvs':combustivel_bands.getNumber('gvs'),
      'SMA_npvSoil':combustivel_bands.getNumber('npvSoil'),
      



    });
  });
});
features = ee.FeatureCollection(features).flatten();
print('features',features.first(),features.limit(3));
print('+ MÉTRICAS DE ACUMULO DE MATERIAL COMBUSTIVEL', makeTableChart(features, features.first().propertyNames(), 'Fr_E_ID', 300));

// --- --- --- MÉTRICAS DE KG/M² DOS COMPARTIMENTOS DE CARBONO

var qcn_kg_m2 = ee.Image('projects/ee-ipam/assets/CCAL/public/qcn/qcn_brazil_historical_biomass_carbon_qcn_rect_v1')
  .select(['TOTAL','AGB','BGB','CDW','LITTER'],['QCN-total','QCN-agb','QCN-bgb','QCN-cdw','QCN-litter']).divide(10);
var features = qcn_kg_m2.reduceRegions({
  collection:features, 
  reducer:ee.Reducer.sum(),
  scale:30, 
  // crs, crsTransform, 
  // tileScale:2,
  maxPixelsPerRegion:1e13
});
  
print('features',features.first(),features.limit(3));
print('+ MÉTRICAS DE KG/M² DOS COMPARTIMENTOS DE CARBONO', makeTableChart(features, features.first().propertyNames(), 'Fr_E_ID', 300));


var description = '2026-08-21-fato-stats';
var folder = 'fire-fapesp';
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
    2024: ['LC09', 'LC08'],
    2024: ['LC09', 'LC08'],
    2025: ['LC09', 'LC08']

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

  var coverage = ee.Image('projects/mapbiomas-public/assets/brazil/lulc/collection11/mapbiomas_brazil_collection11_coverage_v3');

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
