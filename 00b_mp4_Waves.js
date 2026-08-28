// ============================================================================
// MAPBIOMAS CLIMATIC WAVES — BEAUTIFUL DAILY ANIMATIONS — 2025
//
// NO EXTERNAL FONT PACKAGE REQUIRED
//
// HEAT:
//   consecutive heat-wave days per pixel
//
// COLD:
//   consecutive cold-wave days per pixel
//
// Counter:
//   0 = no event
//   1 = first consecutive day
//   2 = second consecutive day
//   ...
//
// VIDEO:
//   white background
//   gray international boundaries
//   black Brazilian state boundaries
//   strong Brazil national border
//   YYYY-MM-DD date in upper-right
//
// ============================================================================


// ============================================================================
// 1. SOURCE COLLECTIONS
// ============================================================================

var heatRaw = ee.ImageCollection(
  'projects/mapbiomas-brazil/assets/DEGRADATION/COLLECTION-11/CLIMATIC_WAVES/heatWaves'
).sort('system:time_start');


var coldRaw = ee.ImageCollection(
  'projects/mapbiomas-brazil/assets/DEGRADATION/COLLECTION-11/CLIMATIC_WAVES/coldWaves'
).sort('system:time_start');


print('Heat collection:', heatRaw);
print('Cold collection:', coldRaw);

print('Heat images:', heatRaw.size());
print('Cold images:', coldRaw.size());


// ============================================================================
// 2. PERIOD
// ============================================================================
//
// END is exclusive.
//
// This gives exactly 365 frames for 2025.
// ============================================================================

var START = ee.Date('2025-01-01');
var END   = ee.Date('2026-01-01');


var nDays = END
  .difference(START, 'day')
  .toInt();


print('Start:', START);
print('End:', END);
print('Number of days:', nDays);


// ============================================================================
// 3. ADMINISTRATIVE BOUNDARIES
// ============================================================================

// Countries
var countries = ee.FeatureCollection(
  'FAO/GAUL/2015/level0'
);


// Brazil
var brazilFc = countries.filter(
  ee.Filter.eq(
    'ADM0_NAME',
    'Brazil'
  )
);


var brazil = brazilFc.geometry();


// Brazilian states / first-order administrative units
var brazilStates = ee.FeatureCollection(
  'FAO/GAUL/2015/level1'
)
.filter(
  ee.Filter.eq(
    'ADM0_NAME',
    'Brazil'
  )
);


print('Brazil states:', brazilStates);
print('Brazil administrative features:', brazilStates.size());


Map.centerObject(
  brazil,
  4
);


// ============================================================================
// 4. VIDEO EXTENT
// ============================================================================
//
// Rectangular region so the surrounding area stays WHITE.
// ============================================================================

var VIDEO_REGION = ee.Geometry.Rectangle(
  [
    -76.0,
    -36.0,
    -28.0,
      7.5
  ],
  null,
  false
);


// ============================================================================
// 5. ZERO TEMPLATE
// ============================================================================

function makeTemplate(collection) {

  return ee.Image(collection.first())
    .select(0)
    .multiply(0)
    .unmask(0)
    .rename('event')
    .toUint8();

}


var heatTemplate = makeTemplate(
  heatRaw
);


var coldTemplate = makeTemplate(
  coldRaw
);


// ============================================================================
// 6. BUILD ONE IMAGE FOR EVERY DAY
// ============================================================================

function makeDailyCollection(
  source,
  template
) {

  var days = ee.List.sequence(
    0,
    nDays.subtract(1)
  );


  var images = days.map(function(i) {

    i = ee.Number(i);


    var date = START.advance(
      i,
      'day'
    );


    // Images belonging to this calendar date
    var thisDay = source.filterDate(
      date,
      date.advance(1, 'day')
    );


    // Convert to binary:
    // 1 = event
    // 0 = not event
    var binary = thisDay.map(function(img) {

      return img
        .select(0)
        .eq(1)
        .unmask(0)
        .rename('event')
        .toUint8();

    });


    // Zero fallback image
    var zeroImage = template
      .rename('event')
      .toUint8();


    // Daily event.
    //
    // If multiple source images occur on the same day,
    // any 1 means event = 1.
    var event = binary
      .merge(
        ee.ImageCollection.fromImages([
          zeroImage
        ])
      )
      .max()
      .rename('event')
      .toUint8();


    // Day index:
    //
    // Jan 1 = 0
    // Jan 2 = 1
    // ...
    var dayIndex = ee.Image
      .constant(i)
      .rename('day_index')
      .toInt32();


    // Store day index only where event == 0.
    //
    // Later this tells us when each pixel last reset.
    var zeroDay = dayIndex
      .updateMask(
        event.eq(0)
      )
      .rename('zero_day')
      .toInt32();


    var result = event
      .addBands(zeroDay)
      .cast(
        {
          event: 'uint8',
          zero_day: 'int32'
        },
        [
          'event',
          'zero_day'
        ]
      );


    return result
      .set(
        'system:time_start',
        date.millis()
      )
      .set(
        'day_index',
        i
      )
      .set(
        'date',
        date.format('YYYY-MM-dd')
      );

  });


  return ee.ImageCollection
    .fromImages(images)
    .cast(
      {
        event: 'uint8',
        zero_day: 'int32'
      },
      [
        'event',
        'zero_day'
      ]
    );

}


// ============================================================================
// 7. DAILY COLLECTIONS
// ============================================================================

var heatDaily = makeDailyCollection(
  heatRaw,
  heatTemplate
);


var coldDaily = makeDailyCollection(
  coldRaw,
  coldTemplate
);


print(
  'Heat daily frames:',
  heatDaily.size()
);


print(
  'Cold daily frames:',
  coldDaily.size()
);


// ============================================================================
// 8. CONSECUTIVE-DAY COUNTER
// ============================================================================
//
// event:
//
// 0 0 1 1 1 1 0 1 1
//
// count:
//
// 0 0 1 2 3 4 0 1 2
//
// ============================================================================

function makeConsecutive(
  daily,
  outputBand
) {

  var result = daily.map(function(img) {

    var date = ee.Date(
      img.get('system:time_start')
    );


    var i = ee.Number(
      img.get('day_index')
    );


    // All dates from Jan 1 through today
    var history = daily.filterDate(
      START,
      date.advance(1, 'day')
    );


    // Most recent zero for every pixel
    var lastZero = history
      .select('zero_day')
      .max()
      .unmask(-1)
      .toInt32();


    // Today's temporal index
    var currentIndex = ee.Image
      .constant(i)
      .toInt32();


    // Today's binary event
    var currentEvent = img
      .select('event')
      .toInt32();


    // Consecutive duration
    var consecutive = currentIndex
      .subtract(lastZero)
      .multiply(currentEvent)
      .rename(outputBand)
      .toInt16();


    return consecutive
      .set(
        'system:time_start',
        img.get('system:time_start')
      )
      .set(
        'date',
        img.get('date')
      )
      .set(
        'day_index',
        i
      );

  });


  return ee.ImageCollection(result)
    .map(function(img) {

      return img.toInt16();

    });

}


// ============================================================================
// 9. BUILD HEAT + COLD COUNTERS
// ============================================================================

var heatCount = makeConsecutive(
  heatDaily,
  'consecutive_heat_days'
);


var coldCount = makeConsecutive(
  coldDaily,
  'consecutive_cold_days'
);


print(
  'Consecutive heat frames:',
  heatCount.size()
);


print(
  'Consecutive cold frames:',
  coldCount.size()
);


// ============================================================================
// 10. VISUALIZATION
// ============================================================================

var DISPLAY_MAX = 15;


// HEAT:
// yellow -> orange -> red -> dark red
var heatPalette = [

  'FFFFCC',
  'FFEDA0',
  'FED976',
  'FEB24C',
  'FD8D3C',

  'FC4E2A',
  'EF3B2C',
  'E31A1C',
  'BD0026',

  '99000D',
  '67000D'

];


// COLD:
// pale cyan -> blue -> navy -> purple
var coldPalette = [

  'E0F3F8',
  'D0E7F2',
  'C6DBEF',
  '9ECAE1',
  '6BAED6',

  '4292C6',
  '2171B5',
  '08519C',
  '08306B',

  '253494',
  '54278F',
  '756BB1',
  '9E9AC8'

];


// ============================================================================
// 11. WHITE RGB BACKGROUND
// ============================================================================

var whiteBackground = ee.Image
  .constant([
    255,
    255,
    255
  ])
  .rename([
    'vis-red',
    'vis-green',
    'vis-blue'
  ])
  .toUint8()
  .clip(
    VIDEO_REGION
  );


// ============================================================================
// 12. COUNTRY BOUNDARIES
// ============================================================================

var visibleCountries = countries.filterBounds(
  VIDEO_REGION
);


// Light gray international boundaries
var countryLines = visibleCountries.style({

  color:
    'B8B8B8',

  fillColor:
    '00000000',

  width:
    1

});


// ============================================================================
// 13. BRAZILIAN STATE BOUNDARIES
// ============================================================================
//
// Black state boundaries
// ============================================================================

var stateLines = brazilStates.style({

  color:
    '222222',

  fillColor:
    '00000000',

  width:
    1

});


// ============================================================================
// 14. BRAZIL NATIONAL BORDER
// ============================================================================

var brazilOutline = brazilFc.style({

  color:
    '000000',

  fillColor:
    '00000000',

  width:
    2

});


// ============================================================================
// 15. STATIC BASE
// ============================================================================

var cartographicBase = whiteBackground
  .blend(
    countryLines
  );


// ============================================================================
// 16. CUSTOM DATE FONT
// ============================================================================
//
// IMPORTANT:
//
// No users/gena font assets.
//
// The date is drawn as a seven-segment digital display using rectangles.
//
// Example:
//
//     2025-07-15
//
// ============================================================================


// --------------------------------------------------------------------------
// Position and dimensions.
//
// Longitude/latitude coordinates.
// --------------------------------------------------------------------------

var DATE_X = -39.10;

var DATE_Y = 5.00;


// Digit width / height
var DIGIT_W = 0.72;
var DIGIT_H = 1.35;


// Segment thickness
var DIGIT_T = 0.12;


// Space between characters
var CHAR_GAP = 0.12;


// Hyphen width
var HYPHEN_W = 0.36;


// ============================================================================
// 17. DATE BACKGROUND BOX
// ============================================================================

var dateBoxGeometry = ee.Geometry.Rectangle(
  [
    -39.45,
      4.72,
    -31.15,
      6.62
  ],
  null,
  false
);


var dateBox = ee.FeatureCollection([
  ee.Feature(dateBoxGeometry)
])
.style({

  color:
    'C8C8C8',

  fillColor:
    'FFFFFFFF',

  width:
    1

});


// ============================================================================
// 18. HELPER — CREATE ONE RECTANGULAR SEGMENT
// ============================================================================

function makeSegment(
  xmin,
  ymin,
  xmax,
  ymax,
  active
) {

  var geometry = ee.Geometry.Rectangle(
    [
      xmin,
      ymin,
      xmax,
      ymax
    ],
    null,
    false
  );


  // Convert boolean/conditional result to 0 or 1.
  var activeNumber = ee.Number(
    ee.Algorithms.If(
      active,
      1,
      0
    )
  );


  return ee.Image
    .constant(1)

    .updateMask(
      ee.Image.constant(
        activeNumber
      )
    )

    .clip(
      geometry
    )

    .selfMask();

}


// ============================================================================
// 19. HELPER — TEST IF DIGIT BELONGS TO LIST
// ============================================================================

function digitIs(
  digit,
  values
) {

  return ee.List(values)
    .contains(
      ee.Number(digit)
    );

}


// ============================================================================
// 20. DRAW ONE SEVEN-SEGMENT DIGIT
// ============================================================================
//
//        A
//      -----
//   F |     | B
//     |  G  |
//      -----
//   E |     | C
//     |     |
//      -----
//        D
//
// ============================================================================

function drawDigitMask(
  digit,
  x,
  y
) {

  digit = ee.Number(digit);


  var w = DIGIT_W;
  var h = DIGIT_H;
  var t = DIGIT_T;


  var mid = y + h / 2;


  // ------------------------------------------------------------------------
  // Determine active segments
  // ------------------------------------------------------------------------

  var A = digitIs(
    digit,
    [
      0, 2, 3, 5, 6, 7, 8, 9
    ]
  );


  var B = digitIs(
    digit,
    [
      0, 1, 2, 3, 4, 7, 8, 9
    ]
  );


  var C = digitIs(
    digit,
    [
      0, 1, 3, 4, 5, 6, 7, 8, 9
    ]
  );


  var D = digitIs(
    digit,
    [
      0, 2, 3, 5, 6, 8, 9
    ]
  );


  var E = digitIs(
    digit,
    [
      0, 2, 6, 8
    ]
  );


  var F = digitIs(
    digit,
    [
      0, 4, 5, 6, 8, 9
    ]
  );


  var G = digitIs(
    digit,
    [
      2, 3, 4, 5, 6, 8, 9
    ]
  );


  // ------------------------------------------------------------------------
  // Segment A
  // ------------------------------------------------------------------------

  var segA = makeSegment(

    x + t,
    y + h - t,

    x + w - t,
    y + h,

    A

  );


  // ------------------------------------------------------------------------
  // Segment B
  // ------------------------------------------------------------------------

  var segB = makeSegment(

    x + w - t,
    mid + t / 2,

    x + w,
    y + h - t,

    B

  );


  // ------------------------------------------------------------------------
  // Segment C
  // ------------------------------------------------------------------------

  var segC = makeSegment(

    x + w - t,
    y + t,

    x + w,
    mid - t / 2,

    C

  );


  // ------------------------------------------------------------------------
  // Segment D
  // ------------------------------------------------------------------------

  var segD = makeSegment(

    x + t,
    y,

    x + w - t,
    y + t,

    D

  );


  // ------------------------------------------------------------------------
  // Segment E
  // ------------------------------------------------------------------------

  var segE = makeSegment(

    x,
    y + t,

    x + t,
    mid - t / 2,

    E

  );


  // ------------------------------------------------------------------------
  // Segment F
  // ------------------------------------------------------------------------

  var segF = makeSegment(

    x,
    mid + t / 2,

    x + t,
    y + h - t,

    F

  );


  // ------------------------------------------------------------------------
  // Segment G
  // ------------------------------------------------------------------------

  var segG = makeSegment(

    x + t,
    mid - t / 2,

    x + w - t,
    mid + t / 2,

    G

  );


  return ee.ImageCollection
    .fromImages([
      segA,
      segB,
      segC,
      segD,
      segE,
      segF,
      segG
    ])
    .mosaic()
    .selfMask();

}


// ============================================================================
// 21. DRAW HYPHEN
// ============================================================================

function drawHyphenMask(
  x,
  y
) {

  var mid = y + DIGIT_H / 2;


  return ee.Image
    .constant(1)

    .clip(
      ee.Geometry.Rectangle(
        [
          x,
          mid - DIGIT_T / 2,

          x + HYPHEN_W,
          mid + DIGIT_T / 2
        ],
        null,
        false
      )
    )

    .selfMask();

}


// ============================================================================
// 22. DRAW YYYY-MM-DD
// ============================================================================

function drawDateLabel(
  date
) {

  date = ee.Date(date);


  // ------------------------------------------------------------------------
  // Get date components
  // ------------------------------------------------------------------------

  var year = ee.Number(
    date.get('year')
  );


  var month = ee.Number(
    date.get('month')
  );


  var day = ee.Number(
    date.get('day')
  );


  // ------------------------------------------------------------------------
  // Year digits
  // ------------------------------------------------------------------------

  var y1 = year
    .divide(1000)
    .floor()
    .mod(10);


  var y2 = year
    .divide(100)
    .floor()
    .mod(10);


  var y3 = year
    .divide(10)
    .floor()
    .mod(10);


  var y4 = year
    .mod(10);


  // ------------------------------------------------------------------------
  // Month digits
  // ------------------------------------------------------------------------

  var m1 = month
    .divide(10)
    .floor();


  var m2 = month
    .mod(10);


  // ------------------------------------------------------------------------
  // Day digits
  // ------------------------------------------------------------------------

  var d1 = day
    .divide(10)
    .floor();


  var d2 = day
    .mod(10);


  // ------------------------------------------------------------------------
  // Draw characters from left to right
  // ------------------------------------------------------------------------

  var x = DATE_X;


  var parts = [];


  // YYYY

  parts.push(
    drawDigitMask(
      y1,
      x,
      DATE_Y
    )
  );

  x += DIGIT_W + CHAR_GAP;


  parts.push(
    drawDigitMask(
      y2,
      x,
      DATE_Y
    )
  );

  x += DIGIT_W + CHAR_GAP;


  parts.push(
    drawDigitMask(
      y3,
      x,
      DATE_Y
    )
  );

  x += DIGIT_W + CHAR_GAP;


  parts.push(
    drawDigitMask(
      y4,
      x,
      DATE_Y
    )
  );

  x += DIGIT_W + CHAR_GAP;


  // -

  parts.push(
    drawHyphenMask(
      x,
      DATE_Y
    )
  );

  x += HYPHEN_W + CHAR_GAP;


  // MM

  parts.push(
    drawDigitMask(
      m1,
      x,
      DATE_Y
    )
  );

  x += DIGIT_W + CHAR_GAP;


  parts.push(
    drawDigitMask(
      m2,
      x,
      DATE_Y
    )
  );

  x += DIGIT_W + CHAR_GAP;


  // -

  parts.push(
    drawHyphenMask(
      x,
      DATE_Y
    )
  );

  x += HYPHEN_W + CHAR_GAP;


  // DD

  parts.push(
    drawDigitMask(
      d1,
      x,
      DATE_Y
    )
  );

  x += DIGIT_W + CHAR_GAP;


  parts.push(
    drawDigitMask(
      d2,
      x,
      DATE_Y
    )
  );


  // ------------------------------------------------------------------------
  // Merge all date pieces
  // ------------------------------------------------------------------------

  var dateMask = ee.ImageCollection
    .fromImages(parts)

    .mosaic()

    .selfMask();


  // Dark date text
  var dateText = dateMask.visualize({

    min:
      0,

    max:
      1,

    palette: [
      '151515'
    ]

  });


  // White box + date
  return dateBox
    .blend(
      dateText
    );

}


// ============================================================================
// 23. TEST DATE LABEL
// ============================================================================
//
// This lets you inspect the date style directly on the map.
// ============================================================================

var testDateLabel = drawDateLabel(
  ee.Date('2025-07-15')
);


Map.addLayer(
  testDateLabel,
  {},
  'DATE LABEL TEST',
  true
);


// ============================================================================
// 24. CREATE FINAL VIDEO COLLECTION
// ============================================================================
//
// Layer order:
//
// 1 white background
// 2 gray international borders
// 3 wave colors
// 4 black state boundaries
// 5 black Brazil boundary
// 6 date box / date
//
// ============================================================================

function createBeautifulVideo(
  counts,
  palette
) {

  return counts.map(function(img) {


    // ----------------------------------------------------------------------
    // Wave colors
    // ----------------------------------------------------------------------

    var waveRGB = img

      .min(
        DISPLAY_MAX
      )

      .selfMask()

      .visualize({

        min:
          1,

        max:
          DISPLAY_MAX,

        palette:
          palette,

        opacity:
          0.94

      });


    // ----------------------------------------------------------------------
    // Date
    // ----------------------------------------------------------------------

    var date = ee.Date(
      img.get(
        'system:time_start'
      )
    );


    var dateLabel = drawDateLabel(
      date
    );


    // ----------------------------------------------------------------------
    // Final RGB frame
    // ----------------------------------------------------------------------

    var frame = cartographicBase

      .blend(
        waveRGB
      )

      .blend(
        stateLines
      )

      .blend(
        brazilOutline
      )

      .blend(
        dateLabel
      )

      .clip(
        VIDEO_REGION
      )

      .copyProperties(
        img,
        [
          'system:time_start',
          'date',
          'day_index'
        ]
      );


    return frame;

  });

}


// ============================================================================
// 25. TWO SEPARATE VIDEO COLLECTIONS
// ============================================================================

var heatVideo = createBeautifulVideo(
  heatCount,
  heatPalette
);


var coldVideo = createBeautifulVideo(
  coldCount,
  coldPalette
);


print(
  'Heat video:',
  heatVideo
);


print(
  'Cold video:',
  coldVideo
);


print(
  'Heat video frames:',
  heatVideo.size()
);


print(
  'Cold video frames:',
  coldVideo.size()
);


// ============================================================================
// 26. TEST A SINGLE DAY ON MAP
// ============================================================================

var checkDate = ee.Date(
  '2025-07-15'
);


var heatCheck = ee.Image(
  heatCount
    .filterDate(
      checkDate,
      checkDate.advance(1, 'day')
    )
    .first()
);


var coldCheck = ee.Image(
  coldCount
    .filterDate(
      checkDate,
      checkDate.advance(1, 'day')
    )
    .first()
);


// Heat
Map.addLayer(

  heatCheck.selfMask(),

  {
    min:
      1,

    max:
      DISPLAY_MAX,

    palette:
      heatPalette
  },

  'HEAT - consecutive days',

  true

);


// Cold
Map.addLayer(

  coldCheck.selfMask(),

  {
    min:
      1,

    max:
      DISPLAY_MAX,

    palette:
      coldPalette
  },

  'COLD - consecutive days',

  false

);


// ============================================================================
// 27. MAP BOUNDARIES
// ============================================================================

Map.addLayer(

  countryLines,

  {},

  'Country borders',

  true

);


Map.addLayer(

  stateLines,

  {},

  'Brazil states',

  true

);


Map.addLayer(

  brazilOutline,

  {},

  'Brazil border',

  true

);


// ============================================================================
// 28. GIF PREVIEW
// ============================================================================
//
// TEST WITH ONLY ONE MONTH FIRST.
//
// This reduces synchronous GIF load.
//
// ============================================================================

var previewStart = ee.Date(
  '2025-06-01'
);


var previewEnd = ee.Date(
  '2025-07-01'
);


var heatPreview = heatVideo.filterDate(
  previewStart,
  previewEnd
);


var coldPreview = coldVideo.filterDate(
  previewStart,
  previewEnd
);


print(
  'Heat preview frames:',
  heatPreview.size()
);


print(
  'Cold preview frames:',
  coldPreview.size()
);


// ============================================================================
// 29. GIF SETTINGS
// ============================================================================

var gifParams = {

  region:
    VIDEO_REGION,

  dimensions:
    650,

  framesPerSecond:
    8,

  format:
    'gif',

  crs:
    'EPSG:3857'

};


// ============================================================================
// 30. HEAT GIF LINK
// ============================================================================

heatPreview.getVideoThumbURL(

  gifParams,

  function(url) {

    print(

      ui.Label(

        '🔥 OPEN HEAT-WAVE GIF',

        {

          color:
            '#B2182B',

          fontWeight:
            'bold',

          fontSize:
            '15px',

          padding:
            '8px'

        },

        url

      )

    );

  }

);


// ============================================================================
// 31. COLD GIF LINK
// ============================================================================

coldPreview.getVideoThumbURL(

  gifParams,

  function(url) {

    print(

      ui.Label(

        '❄ OPEN COLD-WAVE GIF',

        {

          color:
            '#2166AC',

          fontWeight:
            'bold',

          fontSize:
            '15px',

          padding:
            '8px'

        },

        url

      )

    );

  }

);


// ============================================================================
// 32. EXPORT FULL HEAT VIDEO
// ============================================================================
//
// 365 frames
// 12 fps
// ~30 seconds
//
// ============================================================================

Export.video.toDrive({

  collection:
    heatVideo,

  description:
    'MapBiomas_HeatWaves_Consecutive_2025',

  folder:
    'MapBiomas_Climatic_Waves',

  fileNamePrefix:
    'HeatWaves_Consecutive_2025_WhiteMap_Date',

  region:
    VIDEO_REGION,

  framesPerSecond:
    12,

  dimensions:
    900,

  crs:
    'EPSG:3857',

  maxFrames:
    500,

  maxPixels:
    1e13

});


// ============================================================================
// 33. EXPORT FULL COLD VIDEO
// ============================================================================

Export.video.toDrive({

  collection:
    coldVideo,

  description:
    'MapBiomas_ColdWaves_Consecutive_2025',

  folder:
    'MapBiomas_Climatic_Waves',

  fileNamePrefix:
    'ColdWaves_Consecutive_2025_WhiteMap_Date',

  region:
    VIDEO_REGION,

  framesPerSecond:
    12,

  dimensions:
    900,

  crs:
    'EPSG:3857',

  maxFrames:
    500,

  maxPixels:
    1e13

});


// ============================================================================
// 34. LEGEND FUNCTION
// ============================================================================
//
// Code Editor only.
// The legend is NOT burned into the exported MP4.
//
// ============================================================================

function makeLegendRow(
  color,
  label
) {

  var box = ui.Label(
    '',
    {
      backgroundColor:
        '#' + color,

      padding:
        '9px',

      margin:
        '0 7px 4px 0'
    }
  );


  var textLabel = ui.Label(
    label,
    {
      margin:
        '2px 0 4px 0'
    }
  );


  return ui.Panel({

    widgets: [
      box,
      textLabel
    ],

    layout:
      ui.Panel.Layout.Flow(
        'horizontal'
      )

  });

}


// ============================================================================
// 35. HEAT LEGEND
// ============================================================================

var heatLegend = ui.Panel({

  style: {

    position:
      'bottom-left',

    padding:
      '10px 14px',

    backgroundColor:
      'white'

  }

});


heatLegend.add(

  ui.Label({

    value:
      '🔥 HEAT WAVES',

    style: {

      fontWeight:
        'bold',

      fontSize:
        '16px'

    }

  })

);


heatLegend.add(
  ui.Label(
    'Consecutive days'
  )
);


heatLegend.add(
  makeLegendRow(
    heatPalette[0],
    '1 day'
  )
);


heatLegend.add(
  makeLegendRow(
    heatPalette[2],
    '≈ 3 days'
  )
);


heatLegend.add(
  makeLegendRow(
    heatPalette[4],
    '≈ 6 days'
  )
);


heatLegend.add(
  makeLegendRow(
    heatPalette[6],
    '≈ 9 days'
  )
);


heatLegend.add(
  makeLegendRow(
    heatPalette[8],
    '≈ 12 days'
  )
);


heatLegend.add(
  makeLegendRow(
    heatPalette[10],
    '≥ ' + DISPLAY_MAX + ' days'
  )
);


Map.add(
  heatLegend
);


// ============================================================================
// 36. COLD LEGEND
// ============================================================================

var coldLegend = ui.Panel({

  style: {

    position:
      'bottom-right',

    padding:
      '10px 14px',

    backgroundColor:
      'white'

  }

});


coldLegend.add(

  ui.Label({

    value:
      '❄ COLD WAVES',

    style: {

      fontWeight:
        'bold',

      fontSize:
        '16px'

    }

  })

);


coldLegend.add(
  ui.Label(
    'Consecutive days'
  )
);


coldLegend.add(
  makeLegendRow(
    coldPalette[0],
    '1 day'
  )
);


coldLegend.add(
  makeLegendRow(
    coldPalette[2],
    '≈ 3 days'
  )
);


coldLegend.add(
  makeLegendRow(
    coldPalette[4],
    '≈ 6 days'
  )
);


coldLegend.add(
  makeLegendRow(
    coldPalette[7],
    '≈ 9 days'
  )
);


coldLegend.add(
  makeLegendRow(
    coldPalette[9],
    '≈ 12 days'
  )
);


coldLegend.add(
  makeLegendRow(
    coldPalette[12],
    '≥ ' + DISPLAY_MAX + ' days'
  )
);


Map.add(
  coldLegend
);


// ============================================================================
// 37. FINAL DIAGNOSTICS
// ============================================================================

print(
  '------------------------------------------------------'
);


print(
  'READY'
);


print(
  'Expected daily frames: 365'
);


print(
  'Heat video frames:',
  heatVideo.size()
);


print(
  'Cold video frames:',
  coldVideo.size()
);


print(
  'Date renderer: INTERNAL SEVEN-SEGMENT'
);


print(
  'External font dependency: NONE'
);


print(
  'Date format: YYYY-MM-DD'
);


print(
  'Background: WHITE'
);


print(
  'International boundaries: LIGHT GRAY'
);


print(
  'Brazil state boundaries: BLACK'
);


print(
  'Brazil national border: STRONG BLACK'
);


print(
  'Full videos available under TASKS'
);


print(
  'Drive folder: MapBiomas_Climatic_Waves'
);


print(
  '------------------------------------------------------'
);
