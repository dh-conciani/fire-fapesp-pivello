# ============================================================================
# MAPBIOMAS BRAZIL - CLIMATIC WAVES
# PYTHON / EARTH ENGINE API
#
# DAILY BINARY HEAT-WAVE AND COLD-WAVE EXPORTS
#
# Project:
#   mapbiomas-brazil
#
# Heat output:
#   projects/mapbiomas-brazil/assets/DEGRADATION/COLLECTION-11/
#   CLIMATIC_WAVES/heatWaves
#
# Cold output:
#   projects/mapbiomas-brazil/assets/DEGRADATION/COLLECTION-11/
#   CLIMATIC_WAVES/coldWaves
#
# Pixel values:
#   0 = no event
#   1 = event
#
# Heat:
#   Tmax >= monthly climatological Tmax + 5 C
#   for >= 5 consecutive days
#
# Cold:
#   Tmin <= monthly climatological Tmin - 5 C
#   for >= 5 consecutive days
#
# IMPORTANT:
#   Every day belonging to a qualifying >=5-day sequence receives 1.
#
# No:
#   - clip()
#   - Map visualization
#   - temporal aggregation
#   - Brazil-wide reduceRegion()
#
# VERSION = 1
# ============================================================================


# ============================================================================
# 0. IMPORTS / AUTHENTICATION
# ============================================================================

import ee

from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# Authenticate once if necessary.
#
# In Colab, uncomment this the first time:
# ---------------------------------------------------------------------------

# ee.Authenticate()


# Use MapBiomas Brazil as the Earth Engine Cloud project.
ee.Authenticate()
ee.Initialize(project='mapbiomas-brazil')


print('Earth Engine initialized with project: mapbiomas-brazil')


# ============================================================================
# 1. PARAMETERS
# ============================================================================

VERSION = 1


# ---------------------------------------------------------------------------
# Analysis period
#
# END_DATE is inclusive.
# ---------------------------------------------------------------------------

START_DATE = '2025-12-01'
END_DATE   = '2025-12-31'


# ---------------------------------------------------------------------------
# Climatology
#
# 1991-01-01 through 2020-12-31.
#
# Earth Engine filterDate() uses an EXCLUSIVE end,
# therefore CLIM_END must be 2021-01-01.
# ---------------------------------------------------------------------------

CLIM_START = '1991-01-01'
CLIM_END   = '2021-01-01'

CLIMATOLOGY_LABEL = '1991-2020'


# ---------------------------------------------------------------------------
# Wave definition
# ---------------------------------------------------------------------------

TEMP_THRESHOLD = 5
MIN_DAYS = 5


# ---------------------------------------------------------------------------
# Export switches
# ---------------------------------------------------------------------------

EXPORT_HEAT = True
EXPORT_COLD = True


# ---------------------------------------------------------------------------
# Automatically submit/start batch tasks?
#
# True:
#   task.start() is called automatically.
#
# False:
#   tasks are constructed but not started.
# ---------------------------------------------------------------------------

START_TASKS = True


# ---------------------------------------------------------------------------
# Skip assets that already exist?
#
# Recommended = True.
# ---------------------------------------------------------------------------

SKIP_EXISTING = True


# ---------------------------------------------------------------------------
# Output ImageCollections
# ---------------------------------------------------------------------------

HEAT_ASSET_ROOT = (
    'projects/mapbiomas-brazil/assets/'
    'DEGRADATION/COLLECTION-11/'
    'CLIMATIC_WAVES/heatWaves'
)

COLD_ASSET_ROOT = (
    'projects/mapbiomas-brazil/assets/'
    'DEGRADATION/COLLECTION-11/'
    'CLIMATIC_WAVES/coldWaves'
)


# ---------------------------------------------------------------------------
# ERA5-Land
# ---------------------------------------------------------------------------

ERA5_ID = 'ECMWF/ERA5_LAND/DAILY_AGGR'

TMAX_BAND = 'temperature_2m_max'
TMIN_BAND = 'temperature_2m_min'


# ---------------------------------------------------------------------------
# Spatial resolution
#
# ERA5-Land nominal EE scale ~11.1 km.
# ---------------------------------------------------------------------------

EXPORT_SCALE = 11132

MAX_PIXELS = 1e13


# ============================================================================
# 2. BRAZIL REGION
# ============================================================================

# No clipping.
#
# Brazil is used only as the export region.

brazil = (
    ee.FeatureCollection('USDOS/LSIB_SIMPLE/2017')
    .filter(ee.Filter.eq('country_na', 'Brazil'))
    .first()
    .geometry()
)


# ============================================================================
# 3. ERA5-LAND COLLECTIONS
# ============================================================================

era5 = ee.ImageCollection(ERA5_ID)

era5_climatology = era5.filterDate(
    CLIM_START,
    CLIM_END
)


# ============================================================================
# 4. PYTHON DATE HELPERS
# ============================================================================

def parse_date(date_string):
    """
    Convert YYYY-MM-DD to Python UTC datetime.
    """

    return datetime.strptime(
        date_string,
        '%Y-%m-%d'
    ).replace(
        tzinfo=timezone.utc
    )


def format_date(date):
    """
    Python datetime -> YYYY-MM-DD.
    """

    return date.strftime('%Y-%m-%d')


def date_range(start_date, end_date):
    """
    Inclusive date generator.
    """

    current = start_date

    while current <= end_date:

        yield current

        current += timedelta(days=1)


# ============================================================================
# 5. MONTHLY CLIMATOLOGY CACHE
# ============================================================================

# Client-side Python cache.
#
# If several daily tasks use December Tmax climatology,
# the same ee.Image graph object is reused in this Python process.

climatology_cache = {}


def get_monthly_climatology(month, band):
    """
    Mean daily extreme for a calendar month over 1991-2020.

    Example:
      month = 12
      band  = temperature_2m_max

    Gives:
      mean of all daily Tmax values occurring in December
      during 1991-2020.
    """

    key = (month, band)

    if key not in climatology_cache:

        climatology_cache[key] = (
            era5_climatology

            .filter(
                ee.Filter.calendarRange(
                    month,
                    month,
                    'month'
                )
            )

            .select(band)

            .mean()
        )

    return climatology_cache[key]


# ============================================================================
# 6. GET ONE DAILY ERA5 TEMPERATURE IMAGE
# ============================================================================

def get_daily_temperature(date, band):
    """
    Return one ERA5-Land daily temperature image.
    """

    date_string = format_date(date)

    start = ee.Date(date_string)

    end = start.advance(
        1,
        'day'
    )

    return ee.Image(
        era5

        .filterDate(
            start,
            end
        )

        .select(band)

        .first()
    )


# ============================================================================
# 7. DAILY THRESHOLD CANDIDATE
# ============================================================================

def get_candidate(date, event_type):
    """
    Generate daily binary candidate.

    HEAT:
        Tmax >= monthly climatological Tmax + 5 K

    COLD:
        Tmin <= monthly climatological Tmin - 5 K

    Note:
        Differences in Kelvin have the same magnitude as
        differences in degrees Celsius.

        Therefore no conversion from K to C is necessary.
    """

    month = date.month


    # ------------------------------------------------------------------------
    # Heat
    # ------------------------------------------------------------------------

    if event_type == 'heat':

        band = TMAX_BAND

        temperature = get_daily_temperature(
            date,
            band
        )

        climatology = get_monthly_climatology(
            month,
            band
        )

        candidate = temperature.gte(
            climatology.add(
                TEMP_THRESHOLD
            )
        )


    # ------------------------------------------------------------------------
    # Cold
    # ------------------------------------------------------------------------

    elif event_type == 'cold':

        band = TMIN_BAND

        temperature = get_daily_temperature(
            date,
            band
        )

        climatology = get_monthly_climatology(
            month,
            band
        )

        candidate = temperature.lte(
            climatology.subtract(
                TEMP_THRESHOLD
            )
        )


    else:

        raise ValueError(
            "event_type must be 'heat' or 'cold'"
        )


    return candidate


# ============================================================================
# 8. CREATE FINAL DAILY WAVE IMAGE
# ============================================================================

def create_event_image(target_date, event_type):
    """
    Determine whether each pixel belongs to a >=5-day event
    on target_date.

    For MIN_DAYS = 5, target day t can belong to:

        [t-4, t]
        [t-3, t+1]
        [t-2, t+2]
        [t-1, t+3]
        [t,   t+4]

    If ANY of those 5-day windows consists entirely of
    candidate == 1, target date receives wave == 1.

    Therefore:

        candidate:
          1 1 1 1 1 1

        final:
          1 1 1 1 1 1

    rather than:

          0 0 0 0 1 1
    """


    # ========================================================================
    # 8.1 Candidate images t-4 ... t+4
    # ========================================================================

    candidates = []


    for offset in range(
        -(MIN_DAYS - 1),
        MIN_DAYS
    ):

        candidate_date = (
            target_date +
            timedelta(days=offset)
        )

        candidate = get_candidate(
            candidate_date,
            event_type
        )

        candidates.append(
            candidate
        )


    # ========================================================================
    # 8.2 Five possible consecutive windows
    # ========================================================================

    windows = []


    for start_index in range(MIN_DAYS):

        window_result = candidates[
            start_index
        ]


        for j in range(
            1,
            MIN_DAYS
        ):

            window_result = window_result.And(
                candidates[
                    start_index + j
                ]
            )


        windows.append(
            window_result
        )


    # ========================================================================
    # 8.3 OR all windows
    # ========================================================================

    event = windows[0]


    for window in windows[1:]:

        event = event.Or(
            window
        )


    # ========================================================================
    # 8.4 Metadata
    # ========================================================================

    date_string = format_date(
        target_date
    )


    if event_type == 'heat':

        output_band = 'heat_wave'

        event_name = 'heat_wave'

        source_band = TMAX_BAND

        temperature_metric = (
            'daily_maximum_temperature'
        )

        criterion = (
            'Tmax >= monthly climatological Tmax + '
            '5C for >=5 consecutive days'
        )


    else:

        output_band = 'cold_wave'

        event_name = 'cold_wave'

        source_band = TMIN_BAND

        temperature_metric = (
            'daily_minimum_temperature'
        )

        criterion = (
            'Tmin <= monthly climatological Tmin - '
            '5C for >=5 consecutive days'
        )


    # ========================================================================
    # 8.5 Final binary raster
    # ========================================================================

    event = (
        event

        .gt(0)

        .rename(
            output_band
        )

        .unmask(0)

        .toUint8()
    )


    # ========================================================================
    # 8.6 Metadata
    # ========================================================================

    event = event.set({

        # --------------------------------------------------------------------
        # Temporal
        # --------------------------------------------------------------------

        'system:time_start':
            ee.Date(date_string).millis(),

        'date':
            date_string,

        'year':
            target_date.year,

        'month':
            target_date.month,

        'day':
            target_date.day,


        # --------------------------------------------------------------------
        # Product
        # --------------------------------------------------------------------

        'event_type':
            event_name,

        'band_name':
            output_band,

        'territory':
            'Brazil',

        'pixel_type':
            'uint8',

        'value_0':
            'no_event',

        'value_1':
            event_name,


        # --------------------------------------------------------------------
        # Method
        # --------------------------------------------------------------------

        'temperature_metric':
            temperature_metric,

        'temperature_threshold_celsius':
            TEMP_THRESHOLD,

        'minimum_consecutive_days':
            MIN_DAYS,

        'criterion':
            criterion,


        # --------------------------------------------------------------------
        # Climatology
        # --------------------------------------------------------------------

        'climatology_start':
            CLIM_START,

        'climatology_end':
            '2020-12-31',

        'climatology_period':
            CLIMATOLOGY_LABEL,

        'climatology_frequency':
            'monthly',

        'climatology_statistic':
            'mean_daily_temperature_extreme_for_calendar_month',


        # --------------------------------------------------------------------
        # Source
        # --------------------------------------------------------------------

        'source_dataset':
            ERA5_ID,

        'source_band':
            source_band,

        'source_temperature_units':
            'Kelvin',

        'anomaly_difference_units':
            'Kelvin_equivalent_to_Celsius_difference',


        # --------------------------------------------------------------------
        # MapBiomas
        # --------------------------------------------------------------------

        'collection':
            'MapBiomas Brazil Degradation Collection 11',

        'theme':
            'CLIMATIC_WAVES',

        'version':
            VERSION

    })


    return event


# ============================================================================
# 9. ASSET HELPERS
# ============================================================================

def asset_exists(asset_id):
    """
    Return True if Earth Engine asset exists.
    """

    try:

        ee.data.getAsset(
            asset_id
        )

        return True

    except Exception:

        return False


def ensure_image_collection(asset_id):
    """
    Create ImageCollection if it does not exist.
    """

    try:

        info = ee.data.getAsset(
            asset_id
        )

        asset_type = info.get('type')

        print(
            f'Collection exists: {asset_id} '
            f'[{asset_type}]'
        )


    except Exception:

        print(
            f'Creating ImageCollection: {asset_id}'
        )

        ee.data.createAsset(
            {
                'type': 'IMAGE_COLLECTION'
            },
            asset_id
        )


# ============================================================================
# 10. ENSURE OUTPUT COLLECTIONS EXIST
# ============================================================================

if EXPORT_HEAT:

    ensure_image_collection(
        HEAT_ASSET_ROOT
    )


if EXPORT_COLD:

    ensure_image_collection(
        COLD_ASSET_ROOT
    )


# ============================================================================
# 11. EXPORT HELPER
# ============================================================================

def submit_export(
    image,
    description,
    asset_id
):
    """
    Create and optionally start Earth Engine batch export.
    """


    # ------------------------------------------------------------------------
    # Skip existing output
    # ------------------------------------------------------------------------

    if SKIP_EXISTING:

        if asset_exists(asset_id):

            print(
                f'SKIP existing: {asset_id}'
            )

            return None


    # ------------------------------------------------------------------------
    # Create export task
    # ------------------------------------------------------------------------

    task = ee.batch.Export.image.toAsset(

        image=image,

        description=description,

        assetId=asset_id,

        region=brazil,

        scale=EXPORT_SCALE,

        maxPixels=MAX_PIXELS,

        pyramidingPolicy={
            '.default': 'mode'
        }
    )


    # ------------------------------------------------------------------------
    # Start automatically
    # ------------------------------------------------------------------------

    if START_TASKS:

        task.start()

        print(
            f'STARTED | '
            f'{description} | '
            f'{task.id}'
        )

    else:

        print(
            f'CREATED | '
            f'{description}'
        )


    return task


# ============================================================================
# 12. DAILY BATCH EXPORT
# ============================================================================

start_date = parse_date(
    START_DATE
)

end_date = parse_date(
    END_DATE
)


tasks = []


for current_date in date_range(
    start_date,
    end_date
):

    date_string = format_date(
        current_date
    )

    date_name = date_string.replace(
        '-',
        '_'
    )


    # ========================================================================
    # HEAT WAVE
    # ========================================================================

    if EXPORT_HEAT:

        heat_image = create_event_image(
            current_date,
            'heat'
        )


        heat_name = (
            f'heat_wave_'
            f'{date_name}_'
            f'v{VERSION}'
        )


        heat_asset_id = (
            f'{HEAT_ASSET_ROOT}/'
            f'{heat_name}'
        )


        heat_description = (
            f'HW_'
            f'{date_name}_'
            f'v{VERSION}'
        )


        task = submit_export(

            image=heat_image,

            description=heat_description,

            asset_id=heat_asset_id

        )


        if task is not None:

            tasks.append(
                task
            )


    # ========================================================================
    # COLD WAVE
    # ========================================================================

    if EXPORT_COLD:

        cold_image = create_event_image(
            current_date,
            'cold'
        )


        cold_name = (
            f'cold_wave_'
            f'{date_name}_'
            f'v{VERSION}'
        )


        cold_asset_id = (
            f'{COLD_ASSET_ROOT}/'
            f'{cold_name}'
        )


        cold_description = (
            f'CW_'
            f'{date_name}_'
            f'v{VERSION}'
        )


        task = submit_export(

            image=cold_image,

            description=cold_description,

            asset_id=cold_asset_id

        )


        if task is not None:

            tasks.append(
                task
            )


# ============================================================================
# 13. SUMMARY
# ============================================================================

print()
print('==============================================================')
print('BATCH SUBMISSION COMPLETE')
print('==============================================================')

print(
    f'Period: {START_DATE} -> {END_DATE}'
)

print(
    f'Climatology: {CLIMATOLOGY_LABEL}'
)

print(
    f'Version: {VERSION}'
)

print(
    f'New tasks: {len(tasks)}'
)

print(
    f'Auto-start: {START_TASKS}'
)

print('==============================================================')
