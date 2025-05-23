## build tables for the fapesp fire project 
## dhemerson.costa@ipam.org.br

## read libraries
library(rgee)
library(stringr)
library(googledrive)

## Initialize RGEE API 
ee_Initialize(project='chrome-formula-341513')

## define native vegetation classes
native <- c(3, 4, 5, 11, 12)

## get biomes
biomes <- ee$Image('projects/mapbiomas-workspace/AUXILIAR/biomas-2019-raster')
biomes_fc <- ee$FeatureCollection('projects/mapbiomas-workspace/AUXILIAR/biomas-2019')

## get states 
states <- ee$Image('projects/mapbiomas-workspace/AUXILIAR/estados-2016-raster')

## get cartas
cartas_fc <- ee$FeatureCollection('users/wallacesilva/vetor/sul_americar_hex_grid_50km')$
  filterBounds(biomes_fc$filterMetadata('Bioma', 'equals', 'Cerrado'))

cartas_fc_25 <- ee$FeatureCollection('users/wallacesilva/vetor/sul_americar_hex_grid_25km')$
  filterBounds(biomes_fc$filterMetadata('Bioma', 'equals', 'Cerrado'))

## get regions
regions <- cartas_fc$aggregate_array('FID')$getInfo()

## check already processed files
files_in_folder <- drive_ls(path = drive_get("FAPESP_FIRE"))

## extract processed tiles
processed_files <- str_extract(files_in_folder$name, "^[^_]+")

# Find missing entries
missing <- regions[!regions %in% processed_files]

## get only missing tiles
cartas_fc <- cartas_fc$filter(ee$Filter$inList('FID', missing))

## filter 25km grids by yhe missing 50km grids
cartas_fc2 <- cartas_fc_25$filterBounds(cartas_fc)

## to process
print('gathering information from tiles to process')
toProcess <- cartas_fc2$aggregate_array('FID')$getInfo()
print(paste0(length(toProcess), ' tiles found'))

## get burned area maps with land cover and land use
fire <- ee$Image('projects/mapbiomas-public/assets/brazil/fire/collection3/mapbiomas_fire_collection3_annual_burned_coverage_v1')

## set years
years <- seq(1985, 2023)

## for each tile
for(i in 1:length(toProcess)) {
  print(paste0('processing tile ', i, ' of ', length(toProcess)))
  
  ## get carta
  carta_i <- cartas_fc2$filter(ee$Filter$eq('FID', toProcess[i]))
  
  ## get only native vegetation burned and in areas 1km around
  fire_native <- ee$Image()
  
  ## for each year
  for(j in 1:length(years)) {
    
    ## build native vegetation binary mnask for the year j
    native_ij <- fire$select(paste0('burned_coverage_', years[j]))$
      remap(from= native,
            to= c (1, 1, 1, 1, 1))$clip(carta_i)
    
    ## compute the 1km buffer
    buffer_ij <- native_ij$distance(ee$Kernel$euclidean(1010, 'meters'), FALSE)$gte(0) 
    
    ## get burned area within the buffer
    fire_ij <- fire$select(paste0('burned_coverage_', years[j]))$
      updateMask(buffer_ij)$
      rename(paste0('burned_', years[j]))
    
    ## stack in the recipe
    fire_native <- fire_native$addBands(fire_ij)
    
  }

  # Remove "constant"
  bands_filtered <- c('burned_1985', 'burned_1986', 'burned_1987', 'burned_1988', 'burned_1989', 'burned_1990',
                      'burned_1991', 'burned_1992', 'burned_1993', 'burned_1994', 'burned_1995',
                      'burned_1996', 'burned_1997', 'burned_1998', 'burned_1999',
                      'burned_2000', 'burned_2001', 'burned_2002', 'burned_2003', 'burned_2004', 'burned_2005',
                      'burned_2006', 'burned_2007', 'burned_2008', 'burned_2009', 'burned_2010', 'burned_2011',
                      'burned_2012', 'burned_2013', 'burned_2014', 'burned_2015', 'burned_2016', 'burned_2017',
                      'burned_2018', 'burned_2019', 'burned_2020', 'burned_2021', 'burned_2022', 'burned_2023')
  
  # Select only desired bands
  fire_native <- fire_native$select(bands_filtered)
  
  # create a mask for pixels that are valid in at least one band
  validMask <- fire_native$reduce(ee$Reducer$max())$mask()
  
  ## create geocoordinates
  geo_coordinates = ee$Image$pixelLonLat()$updateMask(validMask)
  states_i = states$updateMask(validMask)
  
  ## Step 2: Unmask all bands with 0, then reapply the valid mask
  fire_native <- fire_native$unmask(0)$updateMask(validMask)$
    addBands(geo_coordinates)$
    addBands(states_i$rename('state'))
  
  ## Create one point per pixel using sample()
  points <- fire_native$sample(
    region = carta_i$geometry(),
    scale = 30,          ##  native resolution
    geometries= FALSE    ## ensures output is point features
  )

  ## build task to export data
  task <- ee$batch$Export$table$toDrive(
    points,
    paste0(toProcess[i], '_burned_areas'),
    'FAPESP_FIRE2',
    NULL,
    'CSV'
  )
  
  task$start()
  print('==============================================')
  
}
