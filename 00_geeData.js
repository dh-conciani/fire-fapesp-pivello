// get burned area and climate data 
// fapesp fire project 
// dhemerson.costa@ipam.org.br

// set months to be assesed
var months = [9];

// set years to be acessed
var years = [2024];

// set native vegetation classes (3: forest, 4: savanna, 5:mangrove, 11: wetlands, 12: grassland)
var classes = [3, 4, 5, 11, 12];

// read monthly burned area 
var ba_monthly = ee.Image('projects/mapbiomas-public/assets/brazil/fire/collection4/mapbiomas_fire_collection4_monthly_burned_v1');
var ba_lulc = ee.Image('projects/mapbiomas-public/assets/brazil/fire/collection4/mapbiomas_fire_collection4_annual_burned_coverage_v1');

// read rainfall data
var chirps_rainfall = ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY')

// define lulc palette
var vis = {
          'min': 0,
          'max': 62,
          'palette': require('users/mapbiomas/modules:Palettes.js').get('classification7'),
          'format': 'png'
      };

// define rainfall palette
var vis2 = {
  min: 1,
  max: 100,
  palette: ['001137', '0aab1e', 'e7eb05', 'ff4a2d', 'e90000'],
};

// for each year
years.forEach(function(year_i) {
  
  // read monthyl ba
  var ba_monthly_i = ba_monthly.select('burned_monthly_' + year_i);
  
  // get ba lulc
  var ba_lulc_i = ba_lulc.select('burned_coverage_' + year_i);
  
  // get only native vegetation burned area
  var ba_lulc_i_bin = ba_lulc_i.remap({
        'from': classes,
        'to': [1, 1, 1, 1, 1]
        }
      );
    
  // compute the 1km buffer from burned native vegetation 
  var buffer_i = ba_lulc_i_bin.distance(ee.Kernel.euclidean(1010, 'meters'), false).gte(0);
  
  // get burned area within the buffer
  ba_lulc_i = ba_lulc_i.updateMask(buffer_i).rename('burned_' + year_i);
  
  
  // for each month 
  months.forEach(function(month_j) {
    
    // get burned area only for the month j
    var ba_lulc_ij = ba_lulc_i.updateMask(ba_monthly_i.eq(month_j));
    Map.addLayer(ba_lulc_ij, vis, 'Burned LULC ' + year_i + '-' + month_j);
    
    // get rainfall 
    var reference_date = ee.Date(String(year_i) + '-' + String(month_j) + '-28');
    
    var chirps_rainfall_1m = chirps_rainfall
      .filter(ee.Filter.date( reference_date.advance(-1, 'month'), reference_date))
      .select('precipitation')
      .sum()
      .updateMask(ba_lulc_ij);
      
    var chirps_rainfall_3m = chirps_rainfall
      .filter(ee.Filter.date(reference_date.advance(-3, 'month'), reference_date))
      .select('precipitation')
      .sum()
      .updateMask(ba_lulc_ij);
      print( reference_date.advance(-3, 'month'))
      
      print(chirps_rainfall_3m)
    var chirps_rainfall_12m = chirps_rainfall
      .filter(ee.Filter.date(reference_date.advance(-12, 'month'), reference_date))
      .select('precipitation')
      .sum()
      .updateMask(ba_lulc_ij);
      
    Map.addLayer(chirps_rainfall_1m, vis2, 'Rainfall 1M ' + year_i + '-' + month_j)
    Map.addLayer(chirps_rainfall_3m, vis2,  'Rainfall 3M ' + year_i + '-' + month_j)
    Map.addLayer(chirps_rainfall_12m, vis2,  'Rainfall 12M ' + year_i + '-' + month_j)


    
  });

  
});
