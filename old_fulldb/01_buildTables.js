// build burned area statistics table
// dhemerson.costa@ipam.org.br

// define native vegetation classes
var native = [3, 4, 5, 11, 12];

// get biomes
var biomes = ee.Image('projects/mapbiomas-workspace/AUXILIAR/biomas-2019-raster');
var biomes_fc = ee.FeatureCollection('projects/mapbiomas-workspace/AUXILIAR/biomas-2019');

// get states 
var states = ee.Image('projects/mapbiomas-workspace/AUXILIAR/estados-2016-raster');

// get cartas
var cartas_fc = ee.FeatureCollection('users/wallacesilva/vetor/sul_americar_hex_grid_50km')
  .filterBounds(biomes_fc.filterMetadata('Bioma', 'equals', 'Cerrado'));
  //Map.addLayer(cartas_fc)
  
var regions = cartas_fc.aggregate_array('FID')//.slice(0,1000); // get unique region IDs
print(regions.length())

// get burned area maps with land cover and land use
var fire = ee.Image('projects/mapbiomas-public/assets/brazil/fire/collection3/mapbiomas_fire_collection3_annual_burned_coverage_v1');

// set years to be computed
var years = ee.List.sequence({'start': 1985, 'end': 2023}).getInfo();

// for each state
regions.evaluate(function(regionList) {
  regionList.forEach(function(regionId) {
  
  // get carta
  var carta_i = cartas_fc.filter(ee.Filter.eq('FID', regionId));
  
  // get only native vegetation burned areas and a buffer 1km around 
  var fire_native = ee.Image([]);
  years.forEach(function(year_j) {
    
    // build a native vegetation binary mask for the year i
    var native_ij = fire.select('burned_coverage_' + year_j)
      .remap({
        'from': native,
        'to': [1, 1, 1, 1, 1]
      }).clip(carta_i);
      
      
    // compute the 1km buffer from native vegetation 
    var buffer_ij = native_ij.distance(ee.Kernel.euclidean(1010, 'meters'), false).gte(0);

    // get burned area within the buffer
    var fire_ij = fire.select('burned_coverage_' + year_j)
      .updateMask(buffer_ij)
      .rename('burned_' + year_j);
      
    // stack into recipe
    fire_native = fire_native.addBands(fire_ij);
  });
  
  // create a mask for pixels that are valid in at least one band
  var validMask = fire_native.reduce(ee.Reducer.max()).mask();
  
  // create geocoordinates
  var geo_coordinates = ee.Image.pixelLonLat().updateMask(validMask);
  var states_i = states.updateMask(validMask);

  // Step 2: Unmask all bands with 0, then reapply the valid mask
  fire_native = fire_native.unmask(0).updateMask(validMask)
    .addBands(geo_coordinates)
    .addBands(states_i.rename('state'));

  // Create one point per pixel using sample()
  var points = fire_native.sample({
    'region': carta_i.geometry(),
    'scale': 30,          // native resolution
    'geometries': false   // ensures output is point features
  });
  
    Export.table.toDrive({
  'collection': points,
  'description': regionId + '_burned_area',
  'folder': 'FAPESP_FIRE',
  'fileFormat': 'CSV'
    });
  });
});


