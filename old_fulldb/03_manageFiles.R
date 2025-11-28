library(googledrive)

## Search all folder with same name 
results <- drive_find(pattern = "FAPESP_FIRE2", type = "folder") ## In this case i'm using FAPESP_FIRE2

## ID of the folder in which files will be placed
dest <- '1UoKPW15TOGfhcnugYN_NIZr4AlNV_dTQ'

## For each folder 
for (i in 1:nrow(results)) {
  
  ## List the files in the folder
  files <- drive_ls(path = as_id(results$id[i]))
  
  if (nrow(files) == 0) {
    next
  }
  
  ## For each file
  for (k in 1:nrow(files)) {
    
    ## Move file for the destination folder
    drive_update(
      
      file = files$id[k],
      add_parents = dest
    )
    
  }
  
  ## Delete empty folder 
  drive_trash(results$id[i])
  
}
