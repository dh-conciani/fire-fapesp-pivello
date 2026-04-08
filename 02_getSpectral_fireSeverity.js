// =========================
// INPUT POINTS
// =========================
var points = ee.FeatureCollection(
  'users/dh-conciani/help/fire-fapesp/2026-04-08-tabFato'
);

// =========================
// LANDSAT COLLECTIONS
// =========================
var col_5 = ee.ImageCollection('LANDSAT/LT05/C02/T1_L2');
var col_8 = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2');
var col_9 = ee.ImageCollection('LANDSAT/LC09/C02/T1_L2');


// =========================
// FUNCTIONS
// =========================

// Clip borders
function clipBoard_Landsat(image) {
  return image.updateMask(
    ee.Image().paint(image.geometry().buffer(-3000)).eq(0)
  );
}

// LS8/9 correction
function corrections_LS8_col2(image) {
  var optical = image.select('SR_B.*').multiply(0.0000275).add(-0.2);
  var thermal = image.select('ST_B.*').multiply(0.00341802).add(149.0);

  image = image.addBands(optical, null, true)
               .addBands(thermal, null, true);

  var qa = image.select('QA_PIXEL');
  var cloud = qa.bitwiseAnd(1 << 3)
    .or(qa.bitwiseAnd(1 << 4))
    .or(qa.bitwiseAnd(1 << 9));

  return image.updateMask(cloud.eq(0))
    .select(
      ['SR_B2','SR_B3','SR_B4','SR_B5','SR_B6','SR_B7'],
      ['blue','green','red','nir','swir1','swir2']
    ).float();
}

// LS5 correction
function corrections_LS57_col2(image) {
  var optical = image.select('SR_B.*').multiply(0.0000275).add(-0.2);
  image = image.addBands(optical, null, true);

  var qa = image.select('QA_PIXEL');
  var mask = qa.bitwiseAnd(1 << 3).eq(0)
    .and(qa.bitwiseAnd(1 << 5).eq(0));

  return image.updateMask(mask)
    .select(
      ['SR_B1','SR_B2','SR_B3','SR_B4','SR_B5','SR_B7'],
      ['blue','green','red','nir','swir1','swir2']
    ).float();
}

// NBR
function addBand_NBR(image) {
  var nbr = image.expression(
    '(nir - swir2) / (nir + swir2)',
    {
      nir: image.select('nir'),
      swir2: image.select('swir2')
    }
  ).rename('nbr');

  return image.addBands(nbr);
}

// SMA (same logic as your script)
function fractions(image) {

  var bands = ['blue','green','red','nir','swir1','swir2'];
  var img = image.select(bands).multiply(10000);

  var GV    = [119,475,169,6250,2399,675];
  var NPV   = [1514,1597,1421,3053,7707,1975];
  var Soil  = [1799,2479,3158,5437,7707,6646];
  var Cloud = [4031,8714,7900,8989,7002,6607];

  var sma = img.unmix([GV, NPV, Soil, Cloud])
    .max(0)
    .multiply(100)
    .int16();

  sma = image
    .addBands(sma.select(0).rename('gv'))
    .addBands(sma.select(1).rename('npv'))
    .addBands(sma.select(2).rename('soil'))
    .addBands(sma.select(3).rename('cloud'));

  // Derived bands (same as your original script)
  var sum = sma.select(['gv','npv','soil','cloud'])
    .reduce(ee.Reducer.sum());

  var shade = sum.subtract(100).abs().rename('shade');
  var gvs = sma.select('gv').divide(sum).multiply(100).rename('gvs');
  var npvSoil = sma.select('gv')
    .add(sma.select('soil'))
    .add(sma.select('cloud'))
    .rename('npvSoil');

  return sma.addBands(shade).addBands(gvs).addBands(npvSoil);
}


// =========================
// SENSOR PROCESSING
// =========================
function prepL5(img) {
  return fractions(addBand_NBR(clipBoard_Landsat(corrections_LS57_col2(img))))
    .set('sensor', 'L5');
}

function prepL8(img) {
  return fractions(addBand_NBR(clipBoard_Landsat(corrections_LS8_col2(img))))
    .set('sensor', 'L8');
}

function prepL9(img) {
  return fractions(addBand_NBR(clipBoard_Landsat(corrections_LS8_col2(img))))
    .set('sensor', 'L9');
}


// =========================
// DATE HANDLING (DOY)
// =========================
function addDateWindow(pt) {

  var year = ee.Number(pt.get('Yr_f_f_')).int();
  var doy  = ee.Number(pt.get('Day')).int();

  var centerDate = ee.Date.fromYMD(year, 1, 1)
    .advance(doy.subtract(1), 'day');

  var startDate = centerDate.advance(-3, 'year');
  var endDate   = centerDate.advance(3, 'year').advance(1, 'day');

  return pt.set({
    FEvn_ID: pt.get('Fr_E_ID'),
    center_date: centerDate.format('YYYY-MM-dd'),
    start_date: startDate.format('YYYY-MM-dd'),
    end_date: endDate.format('YYYY-MM-dd')
  });
}


// =========================
// CLEAN POINTS
// =========================
var pointsClean = points
  .filter(ee.Filter.notNull(['Yr_f_f_','Day','Fr_E_ID']))
  .map(addDateWindow);


// =========================
// PROCESS EACH POINT
// =========================
function processPoint(pt) {

  var geom = pt.geometry();

  var start = ee.Date(pt.get('start_date'));
  var end   = ee.Date(pt.get('end_date'));

  var col = col_5.map(prepL5)
    .merge(col_8.map(prepL8))
    .merge(col_9.map(prepL9))
    .filterBounds(geom)
    .filterDate(start, end);

  return col.map(function(img) {

    var sample = img.sample({
      region: geom,
      scale: 30,
      numPixels: 1,
      geometries: true
    });

    return sample.map(function(f) {
      return f.set({
        Fire_Event_ID: pt.get('Fr_E_ID'),
        Year: pt.get('Yr_f_f_'),
        DOY: pt.get('Day'),
        center_date: pt.get('center_date'),
        scene_date: ee.Date(img.get('system:time_start')).format('YYYY-MM-dd'),
        sensor: img.get('sensor')
      });
    });

  }).flatten();
}


// =========================
// RUN
// =========================
var result = pointsClean.map(processPoint).flatten();

print('Result preview', result.limit(20));


// =========================
// EXPORT
// =========================
Export.table.toDrive({
  collection: result,
  description: 'Landsat_SMA_points_with_FEvnID_v2',
  folder: 'export-gee',
  fileFormat: 'CSV'
});


// =========================
// VISUAL CHECK
// =========================
Map.centerObject(pointsClean, 6);
Map.addLayer(pointsClean, {color: 'yellow'}, 'points');
