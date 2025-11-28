// ------------------------------------------
// INPUTS
// ------------------------------------------

// Fire (MapBiomas) image
var fire = ee.Image('projects/mapbiomas-public/assets/brazil/lulc/collection10/mapbiomas_brazil_collection10_integration_v2');

// Territory polygons (each feature has a property "Ano")
var territory = ee.FeatureCollection('users/dh-conciani/help/fire-fapesp/fato-v2-2025-11-28');

// ------------------------------------------
// PARAMETERS
// ------------------------------------------
var pixelScale = 30;        // MapBiomas native resolution
var areaBandName = 'area_ha';
var classBandName = 'class';

// ------------------------------------------
// FUNCTION: compute area by class for one feature,
//           using its own "Ano" property
// ------------------------------------------
function areaByClassForFeature(feat) {
  // Get the reference year stored in the feature and force to integer
  var year = ee.Number(feat.get('Ano')).int();

  // Build the classification band name, e.g. "classification_2005"
  // IMPORTANT: use '%d' to avoid decimal places
  var bandName = ee.String('classification_').cat(year.format('%d'));

  // Classification band for this feature's year
  var classImg = fire.select(bandName).rename(classBandName);

  var areaImg = ee.Image.pixelArea().divide(10000).rename(areaBandName);
  var stack = areaImg.addBands(classImg);

  var reducer = ee.Reducer
    .sum()
    .group({
      groupField: 1,
      groupName: 'class'
    });

  var reduced = stack.reduceRegion({
    reducer: reducer,
    geometry: feat.geometry(),
    scale: pixelScale,
    maxPixels: 1e13
  });

  var groups = ee.List(reduced.get('groups'));
  var baseProps = feat.toDictionary(feat.propertyNames());

  var classFeatures = groups.map(function (g) {
    g = ee.Dictionary(g);
    return ee.Feature(
      null,
      baseProps.combine({
        year: year,                 // will be an integer, e.g. 2009
        class: g.get('class'),
        area_ha: g.get('sum')
      }, true)
    );
  });

  return ee.FeatureCollection(classFeatures);
}
// ------------------------------------------
// APPLY TO ALL FEATURES
// ------------------------------------------
var results = ee.FeatureCollection(
  territory.map(areaByClassForFeature)
).flatten();

// Inspect the results
print('Area by class for each feature using its Ano (ha)', results.limit(50));

// ------------------------------------------
// OPTIONAL: EXPORT TO DRIVE
// ------------------------------------------
Export.table.toDrive({
  collection: results,
  description: 'lulc-fapesp-fire-v3',
  fileFormat: 'CSV'
});
