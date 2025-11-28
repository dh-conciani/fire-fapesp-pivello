// compute burned area for the amazon 
// dhemerson.costa@ipam.org.br

// set years to be used
var years = [
  1985, 1986, 1987, 1988, 1989, 1990, 1991, 1992, 1993, 1994, 1995, 1996, 1997,
  1998, 1999, 2000, 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010, 
  2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 
  2024
  ];


// read fire area
var fire = ee.Image('projects/mapbiomas-public/assets/brazil/lulc/collection10/mapbiomas_brazil_collection10_integration_v2');

var recipe = fire;

// carregar eco regioes
var territory = ee.FeatureCollection('users/dh-conciani/help/fire-fapesp/fato-v2-2025-11-28')
  .map(function(feature) {
    return feature.buffer(1000);
  })

territory = ee.Image().paint(territory, 'Ev_FgID').rename('territory');

Map.addLayer(territory.randomVisualizer(), {}, 'territory');

// change the scale if you need.
var scale = 30;

// define a Google Drive output folder 
var driverFolder = 'STAT';

// get the classification for the file[i] 
var asset_i = recipe.selfMask();

// Image area in hectares
var pixelArea = ee.Image.pixelArea().divide(10000);

// Geometry to export
var geometry = fire.geometry();

// convert a complex object to a simple feature collection 
var convert2table = function (obj) {
  obj = ee.Dictionary(obj);
    var territory = obj.get('territory');
    var classesAndAreas = ee.List(obj.get('groups'));
    
    var tableRows = classesAndAreas.map(
        function (classAndArea) {
            classAndArea = ee.Dictionary(classAndArea);
            var classId = classAndArea.get('class');
            var area = classAndArea.get('sum');
            var tableColumns = ee.Feature(null)
                .set('territory', territory)
                .set('class_id', classId)
                .set('area', area);
                
            return tableColumns;
        }
    );
  
    return ee.FeatureCollection(ee.List(tableRows));
};

// compute the area
var calculateArea = function (image, territory, geometry) {
    var territotiesData = pixelArea.addBands(territory).addBands(image)
        .reduceRegion({
            reducer: ee.Reducer.sum().group(1, 'class').group(1, 'territory'),
            geometry: geometry,
            scale: scale,
            maxPixels: 1e13
        });
        
    territotiesData = ee.List(territotiesData.get('groups'));
    var areas = territotiesData.map(convert2table);
    areas = ee.FeatureCollection(areas).flatten();
    return areas;
};

// perform per year 
var areas = years.map(
    function (year) {
        var image = asset_i.select('classification_' + year);
        var areas = calculateArea(image, territory, geometry);
        // set additional properties
        areas = areas.map(
            function (feature) {
                return feature.set('year', year);
            }
        );
        return areas;
    }
);

areas = ee.FeatureCollection(areas).flatten();
  
Export.table.toDrive({
    collection: areas,
    description: 'lcluc_fire_fapesp_v2',
    folder: driverFolder,
    fileFormat: 'CSV'
});
