// ============================================================================
// MAPBIOMAS BRAZIL - CLIMATIC WAVES
// DAILY HEAT-WAVE AND COLD-WAVE BINARY MAPS
//
// Source:
//   ECMWF/ERA5_LAND/DAILY_AGGR
//
// OUTPUT:
//   HEAT:
//   projects/mapbiomas-brazil/assets/DEGRADATION/COLLECTION-11/
//   CLIMATIC_WAVES/heatWaves
//
//   COLD:
//   projects/mapbiomas-brazil/assets/DEGRADATION/COLLECTION-11/
//   CLIMATIC_WAVES/coldWaves
//
// PIXEL VALUES:
//   1 = pixel belongs to heat/cold-wave event on that day
//   0 = no event
//
// VERSION:
//   1
//
// CLIMATOLOGY:
//   1991-2020
//
// HEAT-WAVE CRITERION:
//   Tmax >= monthly climatological Tmax + 5 °C
//   for >= 5 consecutive days
//
// COLD-WAVE CRITERION:
//   Tmin <= monthly climatological Tmin - 5 °C
//   for >= 5 consecutive days
//
// IMPORTANT:
// Every day belonging to a >=5-day event is assigned 1.
// The output is NOT only the 5th day onward.
//
// ============================================================================


// ============================================================================
// 1. PARAMETERS
// ============================================================================

var VERSION = 1;


// ---------------------------------------------------------------------------
// Analysis period
// ---------------------------------------------------------------------------
//
// END_DATE is inclusive.
//
// Change these values to export another year/period.
//

var START_DATE = '2025-12-01';
var END_DATE   = '2025-12-31';


// ---------------------------------------------------------------------------
// Climatological reference period
// ---------------------------------------------------------------------------
//
// Current standard normals:
// 1991-01-01 through 2020-12-31.
//
// filterDate() uses an exclusive ending date, therefore 2021-01-01.
//

var CLIM_START = '1985-01-01';
var CLIM_END   = '2025-01-01';


// ---------------------------------------------------------------------------
// Event definition
// ---------------------------------------------------------------------------

var TEMP_THRESHOLD = 5;   // °C anomaly
var MIN_DAYS = 5;         // consecutive days


// ---------------------------------------------------------------------------
// Export control
// ---------------------------------------------------------------------------
//
// You can turn one of these off if you only want to generate one
// family of export tasks.
//

var EXPORT_HEAT = true;
var EXPORT_COLD = true;


// ---------------------------------------------------------------------------
// Output Asset folders
// ---------------------------------------------------------------------------

var HEAT_ASSET_ROOT =
  'projects/mapbiomas-brazil/assets/DEGRADATION/COLLECTION-11/' +
  'CLIMATIC_WAVES/heatWaves';

var COLD_ASSET_ROOT =
  'projects/mapbiomas-brazil/assets/DEGRADATION/COLLECTION-11/' +
  'CLIMATIC_WAVES/coldWaves';


// ---------------------------------------------------------------------------
// Export spatial parameters
// ---------------------------------------------------------------------------
//
// ERA5-Land native resolution is approximately 11 km.
//
// Nearest-neighbor behavior preserves binary values.
//

var EXPORT_SCALE = 11132;

var EXPORT_CRS = 'EPSG:4326';


// ============================================================================
// 2. BRAZIL TERRITORY
// ============================================================================
//
// Replace this boundary with a MapBiomas/IBGE boundary asset if you
// want the climatic-wave products clipped to exactly the same national
// geometry used elsewhere in your workflow.
//

var brazilFC = ee.FeatureCollection('USDOS/LSIB_SIMPLE/2017')
  .filter(
    ee.Filter.eq('country_na', 'Brazil')
  );

var brazil = brazilFC.geometry();

Map.centerObject(brazil, 4);

Map.addLayer(
  brazilFC,
  {},
  'Brazil',
  false
);


// ============================================================================
// 3. LOAD ERA5-LAND DAILY
// ============================================================================
//
// Daily aggregated ERA5-Land.
//
// temperature_2m_max:
//   daily maximum temperature at 2 m
//
// temperature_2m_min:
//   daily minimum temperature at 2 m
//
// Native units:
//   Kelvin
//
// Conversion:
//   Celsius = Kelvin - 273.15
//

var era5 = ee.ImageCollection(
    'ECMWF/ERA5_LAND/DAILY_AGGR'
  )
  .select(
    [
      'temperature_2m_max',
      'temperature_2m_min'
    ],
    [
      'tmax',
      'tmin'
    ]
  )
  .map(function(img) {

    var celsius = img
      .subtract(273.15)
      .copyProperties(
        img,
        ['system:time_start']
      );

    return celsius;
  });


print(
  'ERA5-Land daily',
  era5.limit(5)
);


// ============================================================================
// 4. MONTHLY 1991-2020 CLIMATOLOGY
// ============================================================================
//
// For each ERA5-Land pixel:
//
// January:
// mean of all daily Tmax observations occurring in January,
// 1991-2020.
//
// Same calculation for Tmin.
//
// The procedure is repeated for all 12 months.
//
// Output:
//   tmax_clim
//   tmin_clim
//

var climatology = ee.ImageCollection.fromImages(

  ee.List.sequence(1, 12).map(function(month) {

    month = ee.Number(month);

    var monthlyData = era5
      .filterDate(
        CLIM_START,
        CLIM_END
      )
      .filter(
        ee.Filter.calendarRange(
          month,
          month,
          'month'
        )
      );


    var monthlyClimate = monthlyData
      .mean()
      .rename([
        'tmax_clim',
        'tmin_clim'
      ])
      .set({
        'month': month,
        'climatology_start': CLIM_START,
        'climatology_end': '2020-12-31'
      });


    return monthlyClimate;

  })
);


print(
  '1991-2020 monthly climatology',
  climatology
);


// ============================================================================
// 5. ANALYSIS DATES
// ============================================================================

var start = ee.Date(START_DATE);

var endInclusive = ee.Date(END_DATE);

var endExclusive = endInclusive.advance(
  1,
  'day'
);


// ============================================================================
// 6. ADD TEMPORAL PADDING
// ============================================================================
//
// Example:
//
// If analysis starts:
//
//   2025-01-01
//
// but a heat wave began:
//
//   2024-12-29
//
// then January 1 belongs to the event.
//
// Therefore we load MIN_DAYS - 1 days before and after the requested
// analysis period.
//

var paddedStart = start.advance(
  -(MIN_DAYS - 1),
  'day'
);

var paddedEnd = endExclusive.advance(
  MIN_DAYS - 1,
  'day'
);


print(
  'Requested period',
  START_DATE,
  END_DATE
);

print(
  'Padded period',
  paddedStart,
  paddedEnd
);


// ============================================================================
// 7. DAILY CANDIDATE CONDITIONS
// ============================================================================
//
// These are NOT yet climatic-wave maps.
//
// HEAT:
//
// heat_candidate = 1 where
//
//   Tmax >= monthly climatological Tmax + 5 °C
//
// COLD:
//
// cold_candidate = 1 where
//
//   Tmin <= monthly climatological Tmin - 5 °C
//
// Persistence is handled afterward.
//

var candidates = era5
  .filterDate(
    paddedStart,
    paddedEnd
  )
  .map(function(img) {

    var date = ee.Date(
      img.get('system:time_start')
    );

    var month = date.get('month');


    // -----------------------------------------------------------------------
    // Get climatology corresponding to current month
    // -----------------------------------------------------------------------

    var clim = ee.Image(
      climatology
        .filter(
          ee.Filter.eq(
            'month',
            month
          )
        )
        .first()
    );


    // -----------------------------------------------------------------------
    // Heat-wave threshold
    // -----------------------------------------------------------------------

    var heatThreshold = clim
      .select('tmax_clim')
      .add(TEMP_THRESHOLD);


    var heatCandidate = img
      .select('tmax')
      .gte(heatThreshold)
      .rename('heat_candidate');


    // -----------------------------------------------------------------------
    // Cold-wave threshold
    // -----------------------------------------------------------------------

    var coldThreshold = clim
      .select('tmin_clim')
      .subtract(TEMP_THRESHOLD);


    var coldCandidate = img
      .select('tmin')
      .lte(coldThreshold)
      .rename('cold_candidate');


    // -----------------------------------------------------------------------
    // Return daily binary candidate image
    // -----------------------------------------------------------------------

    return heatCandidate
      .addBands(coldCandidate)
      .unmask(0)
      .toUint8()
      .clip(brazil)
      .set({
        'system:time_start': date.millis(),
        'date': date.format('YYYY-MM-dd')
      });

  });


print(
  'Daily candidate images',
  candidates.limit(10)
);


// ============================================================================
// 8. CREATE LIST OF OUTPUT DATES
// ============================================================================

var numberDays = endExclusive
  .difference(
    start,
    'day'
  );


var dates = ee.List.sequence(
  0,
  numberDays.subtract(1)
).map(function(n) {

  n = ee.Number(n);

  return start.advance(
    n,
    'day'
  );

});


print(
  'Number of daily output images',
  numberDays
);


// ============================================================================
// 9. FUNCTION TO IDENTIFY WHETHER CURRENT DAY BELONGS TO AN EVENT
// ============================================================================
//
// Important:
//
// Assume candidate sequence:
//
// Day        1 2 3 4 5 6 7 8
// candidate  0 1 1 1 1 1 1 0
//
// Since days 2-7 form a six-day event, desired output is:
//
// Day        1 2 3 4 5 6 7 8
// event      0 1 1 1 1 1 1 0
//
// We therefore evaluate every possible MIN_DAYS window containing
// the target date.
//
// If ANY such window consists entirely of candidate = 1,
// the current date belongs to a climatic-wave event.
//

function classifyEventDay(
  date,
  candidateBand,
  outputBand,
  eventType
) {

  date = ee.Date(date);


  // -------------------------------------------------------------------------
  // Build every MIN_DAYS window containing target date
  // -------------------------------------------------------------------------

  var windows = ee.List.sequence(
    0,
    MIN_DAYS - 1
  ).map(function(offset) {

    offset = ee.Number(offset);


    // Current date can occur at any position within the window.

    var windowStart = date.advance(
      offset.multiply(-1),
      'day'
    );


    var windowEnd = windowStart.advance(
      MIN_DAYS,
      'day'
    );


    var subset = candidates
      .select(candidateBand)
      .filterDate(
        windowStart,
        windowEnd
      );


    // min() = 1 only when every image in the window is 1.

    var allDaysTrue = subset
      .min();


    // Require exactly MIN_DAYS observations.

    var completeWindow = ee.Image.constant(
      subset.size()
    ).eq(MIN_DAYS);


    return allDaysTrue
      .and(completeWindow)
      .rename(outputBand)
      .toUint8();

  });


  // -------------------------------------------------------------------------
  // If any window qualifies, current day belongs to event
  // -------------------------------------------------------------------------

  var event = ee.ImageCollection
    .fromImages(windows)
    .max()

    // Explicitly force output to 0/1.
    .gt(0)

    .rename(outputBand)
    .unmask(0)
    .clip(brazil)
    .toUint8();


  // -------------------------------------------------------------------------
  // Metadata
  // -------------------------------------------------------------------------

  return event.set({

    // Temporal metadata
    'system:time_start': date.millis(),
    'date': date.format('YYYY-MM-dd'),
    'year': date.get('year'),
    'month': date.get('month'),
    'day': date.get('day'),

    // Product
    'event_type': eventType,
    'band_name': outputBand,
    'territory': 'Brazil',

    // Output semantics
    'value_0': 'no_event',
    'value_1': eventType,

    // Methodology
    'temperature_threshold_celsius': TEMP_THRESHOLD,
    'minimum_consecutive_days': MIN_DAYS,

    // Climatology
    'climatology_start': '1991-01-01',
    'climatology_end': '2020-12-31',
    'climatology_period': '1991-2020',
    'climatology_frequency': 'monthly',

    // Input
    'source_dataset': 'ECMWF/ERA5_LAND/DAILY_AGGR',
    'source_variable_heat': 'temperature_2m_max',
    'source_variable_cold': 'temperature_2m_min',
    'temperature_units': 'Celsius',

    // Project
    'collection': 'MapBiomas Brazil Degradation Collection 11',
    'theme': 'CLIMATIC_WAVES',

    // Version
    'version': VERSION

  });

}


// ============================================================================
// 10. BUILD DAILY HEAT-WAVE IMAGECOLLECTION
// ============================================================================

var heatWave = ee.ImageCollection.fromImages(

  dates.map(function(date) {

    return classifyEventDay(
      date,
      'heat_candidate',
      'heat_wave',
      'heat_wave'
    );

  })

).sort(
  'system:time_start'
);


print(
  'DAILY HEAT-WAVE COLLECTION',
  heatWave
);

print(
  'Number of heat-wave images',
  heatWave.size()
);


// ============================================================================
// 11. BUILD DAILY COLD-WAVE IMAGECOLLECTION
// ============================================================================

var coldWave = ee.ImageCollection.fromImages(

  dates.map(function(date) {

    return classifyEventDay(
      date,
      'cold_candidate',
      'cold_wave',
      'cold_wave'
    );

  })

).sort(
  'system:time_start'
);


print(
  'DAILY COLD-WAVE COLLECTION',
  coldWave
);

print(
  'Number of cold-wave images',
  coldWave.size()
);


// ============================================================================
// 12. QUICK VISUAL CHECK
// ============================================================================
//
// Change this date to inspect any day from the analysis period.
//

var EXAMPLE_DATE = '2025-01-15';

var exampleDate = ee.Date(
  EXAMPLE_DATE
);


// ---------------------------------------------------------------------------
// Heat
// ---------------------------------------------------------------------------

var exampleHeat = ee.Image(
  heatWave
    .filterDate(
      exampleDate,
      exampleDate.advance(1, 'day')
    )
    .first()
);


Map.addLayer(
  exampleHeat.selfMask(),
  {
    min: 1,
    max: 1,
    palette: ['red']
  },
  'Heat Wave - ' + EXAMPLE_DATE,
  true
);


// ---------------------------------------------------------------------------
// Cold
// ---------------------------------------------------------------------------

var exampleCold = ee.Image(
  coldWave
    .filterDate(
      exampleDate,
      exampleDate.advance(1, 'day')
    )
    .first()
);


Map.addLayer(
  exampleCold.selfMask(),
  {
    min: 1,
    max: 1,
    palette: ['blue']
  },
  'Cold Wave - ' + EXAMPLE_DATE,
  false
);


// ============================================================================
// 13. CHECK THAT VALUES ARE REALLY 0/1
// ============================================================================

print(
  'Heat-wave value histogram:',
  exampleHeat.reduceRegion({
    reducer: ee.Reducer.frequencyHistogram(),
    geometry: brazil,
    scale: EXPORT_SCALE,
    maxPixels: 1e13,
    bestEffort: true
  })
);


print(
  'Cold-wave value histogram:',
  exampleCold.reduceRegion({
    reducer: ee.Reducer.frequencyHistogram(),
    geometry: brazil,
    scale: EXPORT_SCALE,
    maxPixels: 1e13,
    bestEffort: true
  })
);


// ============================================================================
// 14. OPTIONAL CHECK: TOTAL EVENT DAYS
// ============================================================================
//
// This is ONLY for visual checking.
// These accumulated maps are NOT exported by the script.
//

var totalHeatDays = heatWave
  .sum()
  .rename('heat_wave_days');


var totalColdDays = coldWave
  .sum()
  .rename('cold_wave_days');


Map.addLayer(
  totalHeatDays,
  {
    min: 0,
    max: 50
  },
  'Total Heat-Wave Days',
  false
);


Map.addLayer(
  totalColdDays,
  {
    min: 0,
    max: 50
  },
  'Total Cold-Wave Days',
  false
);


// ============================================================================
// 15. CREATE DATE STRINGS FOR EXPORT
// ============================================================================
//
// Export.image.toAsset() is a client-side operation.
//
// We therefore convert the server-side date list to formatted date strings
// and use evaluate() to generate one export task per date.
//

var exportDateStrings = dates.map(function(date) {

  return ee.Date(date)
    .format('YYYY-MM-dd');

});


// ============================================================================
// 16. DAILY EXPORTS
// ============================================================================
//
// Naming convention:
//
// HEAT:
// heat_wave_2025_01_01_v1
//
// COLD:
// cold_wave_2025_01_01_v1
//
// Each image contains ONE uint8 band.
//
// Heat:
//   band = heat_wave
//
// Cold:
//   band = cold_wave
//
// Values:
//   0 = no event
//   1 = event
//
// ============================================================================

exportDateStrings.evaluate(function(dateList) {


  print(
    'Creating export tasks for ' +
    dateList.length +
    ' days.'
  );


  dateList.forEach(function(dateString) {


    // -----------------------------------------------------------------------
    // Convert YYYY-MM-DD to YYYY_MM_DD for asset names
    // -----------------------------------------------------------------------

    var dateName = dateString.replace(
      /-/g,
      '_'
    );


    var dayStart = ee.Date(
      dateString
    );


    var dayEnd = dayStart.advance(
      1,
      'day'
    );


    // =======================================================================
    // HEAT-WAVE EXPORT
    // =======================================================================

    if (EXPORT_HEAT) {


      var heatImage = ee.Image(
        heatWave
          .filterDate(
            dayStart,
            dayEnd
          )
          .first()
      )

      // Ensure final binary datatype.
      .gt(0)
      .rename('heat_wave')
      .toUint8()

      // Restore/update metadata after binary operation.
      .set({

        'system:time_start': dayStart.millis(),

        'date': dateString,

        'year': dayStart.get('year'),

        'month': dayStart.get('month'),

        'day': dayStart.get('day'),

        'event_type': 'heat_wave',

        'band_name': 'heat_wave',

        'territory': 'Brazil',

        'value_0': 'no_event',

        'value_1': 'heat_wave',

        'temperature_metric': 'daily_maximum_temperature',

        'temperature_threshold_celsius': TEMP_THRESHOLD,

        'criterion':
          'Tmax >= monthly climatological Tmax + 5C for >=5 consecutive days',

        'minimum_consecutive_days': MIN_DAYS,

        'climatology_start': '1991-01-01',

        'climatology_end': '2020-12-31',

        'climatology_period': '1991-2020',

        'climatology_frequency': 'monthly',

        'source_dataset':
          'ECMWF/ERA5_LAND/DAILY_AGGR',

        'source_variable':
          'temperature_2m_max',

        'temperature_units': 'Celsius',

        'collection':
          'MapBiomas Brazil Degradation Collection 11',

        'theme':
          'CLIMATIC_WAVES',

        'version': VERSION

      });


      var heatAssetName =
        'heat_wave_' +
        dateName +
        '_v' +
        VERSION;


      var heatAssetId =
        HEAT_ASSET_ROOT +
        '/' +
        heatAssetName;


      Export.image.toAsset({

        image: heatImage,

        description:
          'HW_' +
          dateName +
          '_v' +
          VERSION,

        assetId:
          heatAssetId,

        region:
          brazil,

        scale:
          EXPORT_SCALE,

        crs:
          EXPORT_CRS,

        maxPixels:
          1e13,

        pyramidingPolicy: {
          '.default': 'mode'
        }

      });

    }


    // =======================================================================
    // COLD-WAVE EXPORT
    // =======================================================================

    if (EXPORT_COLD) {


      var coldImage = ee.Image(
        coldWave
          .filterDate(
            dayStart,
            dayEnd
          )
          .first()
      )

      // Ensure final binary datatype.
      .gt(0)
      .rename('cold_wave')
      .toUint8()

      // Restore/update metadata after binary operation.
      .set({

        'system:time_start': dayStart.millis(),

        'date': dateString,

        'year': dayStart.get('year'),

        'month': dayStart.get('month'),

        'day': dayStart.get('day'),

        'event_type': 'cold_wave',

        'band_name': 'cold_wave',

        'territory': 'Brazil',

        'value_0': 'no_event',

        'value_1': 'cold_wave',

        'temperature_metric': 'daily_minimum_temperature',

        'temperature_threshold_celsius': TEMP_THRESHOLD,

        'criterion':
          'Tmin <= monthly climatological Tmin - 5C for >=5 consecutive days',

        'minimum_consecutive_days': MIN_DAYS,

        'climatology_start': '1991-01-01',

        'climatology_end': '2020-12-31',

        'climatology_period': '1991-2020',

        'climatology_frequency': 'monthly',

        'source_dataset':
          'ECMWF/ERA5_LAND/DAILY_AGGR',

        'source_variable':
          'temperature_2m_min',

        'temperature_units': 'Celsius',

        'collection':
          'MapBiomas Brazil Degradation Collection 11',

        'theme':
          'CLIMATIC_WAVES',

        'version': VERSION

      });


      var coldAssetName =
        'cold_wave_' +
        dateName +
        '_v' +
        VERSION;


      var coldAssetId =
        COLD_ASSET_ROOT +
        '/' +
        coldAssetName;


      Export.image.toAsset({

        image: coldImage,

        description:
          'CW_' +
          dateName +
          '_v' +
          VERSION,

        assetId:
          coldAssetId,

        region:
          brazil,

        scale:
          EXPORT_SCALE,

        crs:
          EXPORT_CRS,

        maxPixels:
          1e13,

        pyramidingPolicy: {
          '.default': 'mode'
        }

      });

    }

  });


  print(
    'Export tasks created.'
  );

});
