# ============================================================================
# MAPBIOMAS BRAZIL - CLIMATIC PRODUCTS
# PYTHON / EARTH ENGINE API
#
# DAILY BINARY EXPORTS, STRICT PRODUCT PRIORITY:
#   1) HeatWave  (full time series first)
#   2) Frost     (full time series second)
#   3) ColdWave  (full time series last)
#
# HeatWave:
#   Tmax >= monthly climatological Tmax + 5 C
#   for >= 5 consecutive days
#
# Frost:
#   Tmin <= 4 C on that day
#   no consecutive-day requirement
#
# ColdWave:
#   Tmin <= monthly climatological Tmin - 5 C
#   for >= 5 consecutive days
#
# Pixel values for all products:
#   0 = no event
#   1 = event
#
# IMPORTANT TEMPORAL DESIGN:
#   HeatWave and ColdWave DO NOT stack the full time series.
#   For one target day t, only candidates from t-4 ... t+4 are referenced.
#   Frost references only the target day's Tmin image.
#
# Project:
#   mapbiomas-brazil
#
# VERSION = 1
# ============================================================================


# ============================================================================
# 0. IMPORTS / AUTHENTICATION
# ============================================================================

import csv
import html
import math
import statistics
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone

import ee


PROJECT = 'mapbiomas-brazil'
PROJECT_PATH = f'projects/{PROJECT}'


ee.Authenticate()
ee.Initialize(project=PROJECT)

print(f'Earth Engine initialized with project: {PROJECT}')


# ============================================================================
# 1. PARAMETERS
# ============================================================================

VERSION = 1


# ---------------------------------------------------------------------------
# Analysis period
# END_DATE is inclusive.
# ---------------------------------------------------------------------------

START_DATE = '1985-01-01'
END_DATE   = '2026-09-06'


# ---------------------------------------------------------------------------
# Climatology
# 1991-01-01 through 2020-12-31.
# filterDate() uses an exclusive end.
# ---------------------------------------------------------------------------

CLIM_START = '1991-01-01'
CLIM_END   = '2021-01-01'
CLIMATOLOGY_LABEL = '1991-2020'


# ---------------------------------------------------------------------------
# Wave definitions
# ---------------------------------------------------------------------------

WAVE_ANOMALY_THRESHOLD_C = 5.0
MIN_WAVE_DAYS = 5


# ---------------------------------------------------------------------------
# Frost definition
# ERA5 temperature_2m_min is in Kelvin.
# 4 C = 277.15 K.
# ---------------------------------------------------------------------------

FROST_THRESHOLD_C = 4.0
KELVIN_OFFSET = 273.15
FROST_THRESHOLD_K = FROST_THRESHOLD_C + KELVIN_OFFSET


# ---------------------------------------------------------------------------
# Export switches
# ---------------------------------------------------------------------------

EXPORT_HEAT = True
EXPORT_FROST = True
EXPORT_COLD = True


# ---------------------------------------------------------------------------
# STRICT PRODUCT PRIORITY
# No new Frost tasks are submitted until HeatWave is complete.
# No new ColdWave tasks are submitted until Frost is complete.
# ---------------------------------------------------------------------------

PRODUCT_PRIORITY = (
    'heat',
    'frost',
    'cold',
)

STRICT_PRODUCT_PRIORITY = True

# If True, an unresolved missing output in one product stops the controller
# before it advances to the next product.
STOP_IF_PRODUCT_INCOMPLETE = True


# ---------------------------------------------------------------------------
# Output ImageCollections
# ---------------------------------------------------------------------------

HEAT_ASSET_ROOT = (
    'projects/mapbiomas-brazil/assets/'
    'DEGRADATION/COLLECTION-11/'
    'CLIMATIC_WAVES/heatWaves'
)

FROST_ASSET_ROOT = (
    'projects/mapbiomas-brazil/assets/'
    'DEGRADATION/COLLECTION-11/'
    'CLIMATIC_WAVES/frosts'
)

COLD_ASSET_ROOT = (
    'projects/mapbiomas-brazil/assets/'
    'DEGRADATION/COLLECTION-11/'
    'CLIMATIC_WAVES/coldWaves'
)


# ---------------------------------------------------------------------------
# Product configuration
# ---------------------------------------------------------------------------

PRODUCT_CONFIG = {
    'heat': {
        'enabled': EXPORT_HEAT,
        'label': 'HeatWave',
        'task_prefix': 'HW',
        'file_prefix': 'heat_wave',
        'band_name': 'heat_wave',
        'event_name': 'heat_wave',
        'asset_root': HEAT_ASSET_ROOT,
    },
    'frost': {
        'enabled': EXPORT_FROST,
        'label': 'Frost',
        'task_prefix': 'FR',
        'file_prefix': 'frost',
        'band_name': 'frost',
        'event_name': 'frost',
        'asset_root': FROST_ASSET_ROOT,
    },
    'cold': {
        'enabled': EXPORT_COLD,
        'label': 'ColdWave',
        'task_prefix': 'CW',
        'file_prefix': 'cold_wave',
        'band_name': 'cold_wave',
        'event_name': 'cold_wave',
        'asset_root': COLD_ASSET_ROOT,
    },
}


# ---------------------------------------------------------------------------
# ERA5-Land
# ---------------------------------------------------------------------------

ERA5_ID = 'ECMWF/ERA5_LAND/DAILY_AGGR'
TMAX_BAND = 'temperature_2m_max'
TMIN_BAND = 'temperature_2m_min'


# ---------------------------------------------------------------------------
# Export spatial parameters
# ERA5-Land nominal EE scale ~11.1 km.
# ---------------------------------------------------------------------------

EXPORT_SCALE = 11132
MAX_PIXELS = 1e13


# ---------------------------------------------------------------------------
# Batch / monitor parameters
# ---------------------------------------------------------------------------

BATCH_SIZE = 1500
MONITOR_REFRESH_SECONDS = 15
PROJECT_READY_LIMIT = 3000
QUEUE_SAFETY_MARGIN = 50
MAX_SCRIPT_RETRIES = 2
ASSET_LIST_PAGE_SIZE = 10000

# ETA evidence is based on a moving window of the last 50 concluded tasks,
# separately for each product.
TIMING_WINDOW_SIZE = 50
MIN_TIMING_SAMPLES_FOR_ETA = 5

MISSING_REPORT_CSV = 'climatic_products_missing_after_run.csv'
FAILED_REPORT_CSV = 'climatic_products_failed_tasks.csv'


# ============================================================================
# 2. BRAZIL REGION
# ============================================================================

# No clip(). Brazil is used only as export region.

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
    CLIM_END,
)


# ============================================================================
# 4. PYTHON DATE HELPERS
# ============================================================================


def parse_date(date_string):
    return datetime.strptime(
        date_string,
        '%Y-%m-%d',
    ).replace(tzinfo=timezone.utc)


def format_date(date):
    return date.strftime('%Y-%m-%d')


def date_range(start_date, end_date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def parse_rfc3339(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except Exception:
        return None


# ============================================================================
# 5. MONTHLY CLIMATOLOGY CACHE
# ============================================================================

climatology_cache = {}


def get_monthly_climatology(month, band):
    """Mean daily extreme for one calendar month over 1991-2020."""

    key = (month, band)

    if key not in climatology_cache:
        climatology_cache[key] = (
            era5_climatology
            .filter(
                ee.Filter.calendarRange(
                    month,
                    month,
                    'month',
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
    """Return one ERA5-Land daily temperature image."""

    date_string = format_date(date)
    start = ee.Date(date_string)
    end = start.advance(1, 'day')

    return ee.Image(
        era5
        .filterDate(start, end)
        .select(band)
        .first()
    )


# ============================================================================
# 7. DAILY HEAT/COLD CANDIDATE
# ============================================================================


def get_wave_candidate(date, event_type):
    """
    Generate one daily heat/cold candidate.

    HEAT:
        Tmax >= monthly climatological Tmax + 5 K

    COLD:
        Tmin <= monthly climatological Tmin - 5 K

    A temperature DIFFERENCE of 5 K equals a difference of 5 C.
    """

    month = date.month

    if event_type == 'heat':
        band = TMAX_BAND
        temperature = get_daily_temperature(date, band)
        climatology = get_monthly_climatology(month, band)
        return temperature.gte(
            climatology.add(WAVE_ANOMALY_THRESHOLD_C)
        )

    if event_type == 'cold':
        band = TMIN_BAND
        temperature = get_daily_temperature(date, band)
        climatology = get_monthly_climatology(month, band)
        return temperature.lte(
            climatology.subtract(WAVE_ANOMALY_THRESHOLD_C)
        )

    raise ValueError("event_type must be 'heat' or 'cold'")


# ============================================================================
# 8. CREATE FINAL DAILY HEAT/COLD WAVE IMAGE
# ============================================================================


def create_wave_image(target_date, event_type):
    """
    Determine whether each pixel belongs to a >=5-day wave on target_date.

    MEMORY / GRAPH DESIGN:
      This is a MOVING WINDOW, not a full-time-series stack.

      For MIN_WAVE_DAYS = 5, target day t can belong to:

          [t-4, t]
          [t-3, t+1]
          [t-2, t+2]
          [t-1, t+3]
          [t,   t+4]

      Therefore one target-date export graph references only 9 daily candidate
      images (t-4 ... t+4), evaluates the five possible 5-day windows, and ORs
      the five window results.
    """

    candidates = []

    for offset in range(
        -(MIN_WAVE_DAYS - 1),
        MIN_WAVE_DAYS,
    ):
        candidate_date = target_date + timedelta(days=offset)
        candidates.append(
            get_wave_candidate(candidate_date, event_type)
        )

    windows = []

    for start_index in range(MIN_WAVE_DAYS):
        window_result = candidates[start_index]

        for j in range(1, MIN_WAVE_DAYS):
            window_result = window_result.And(
                candidates[start_index + j]
            )

        windows.append(window_result)

    event = windows[0]

    for window in windows[1:]:
        event = event.Or(window)

    date_string = format_date(target_date)

    if event_type == 'heat':
        output_band = 'heat_wave'
        event_name = 'heat_wave'
        source_band = TMAX_BAND
        temperature_metric = 'daily_maximum_temperature'
        criterion = (
            'Tmax >= monthly climatological Tmax + '
            '5C for >=5 consecutive days'
        )

    elif event_type == 'cold':
        output_band = 'cold_wave'
        event_name = 'cold_wave'
        source_band = TMIN_BAND
        temperature_metric = 'daily_minimum_temperature'
        criterion = (
            'Tmin <= monthly climatological Tmin - '
            '5C for >=5 consecutive days'
        )

    else:
        raise ValueError("event_type must be 'heat' or 'cold'")

    event = (
        event
        .gt(0)
        .rename(output_band)
        .unmask(0)
        .toUint8()
    )

    event = event.set({
        'system:time_start': ee.Date(date_string).millis(),
        'date': date_string,
        'year': target_date.year,
        'month': target_date.month,
        'day': target_date.day,

        'event_type': event_name,
        'band_name': output_band,
        'territory': 'Brazil',
        'pixel_type': 'uint8',
        'value_0': 'no_event',
        'value_1': event_name,

        'method': 'moving_5_day_windows_containing_target_day',
        'temperature_metric': temperature_metric,
        'temperature_threshold_celsius': WAVE_ANOMALY_THRESHOLD_C,
        'minimum_consecutive_days': MIN_WAVE_DAYS,
        'criterion': criterion,

        'climatology_start': CLIM_START,
        'climatology_end': '2020-12-31',
        'climatology_period': CLIMATOLOGY_LABEL,
        'climatology_frequency': 'monthly',
        'climatology_statistic': (
            'mean_daily_temperature_extreme_for_calendar_month'
        ),

        'source_dataset': ERA5_ID,
        'source_band': source_band,
        'source_temperature_units': 'Kelvin',
        'anomaly_difference_units': (
            'Kelvin_equivalent_to_Celsius_difference'
        ),

        'collection': 'MapBiomas Brazil Degradation Collection 11',
        'theme': 'CLIMATIC_WAVES',
        'version': VERSION,
    })

    return event


# ============================================================================
# 9. CREATE DAILY FROST IMAGE
# ============================================================================


def create_frost_image(target_date):
    """
    Daily binary Frost product.

    Criterion:
        daily Tmin <= 4 C

    ERA5-Land temperature_2m_min is stored in Kelvin, therefore:
        4 C = 277.15 K

    No climatology.
    No consecutive-day window.
    Only the target day's Tmin image is referenced.
    """

    date_string = format_date(target_date)

    temperature = get_daily_temperature(
        target_date,
        TMIN_BAND,
    )

    frost = (
        temperature
        .lte(FROST_THRESHOLD_K)
        .rename('frost')
        .unmask(0)
        .toUint8()
    )

    frost = frost.set({
        'system:time_start': ee.Date(date_string).millis(),
        'date': date_string,
        'year': target_date.year,
        'month': target_date.month,
        'day': target_date.day,

        'event_type': 'frost',
        'band_name': 'frost',
        'territory': 'Brazil',
        'pixel_type': 'uint8',
        'value_0': 'no_event',
        'value_1': 'frost',

        'method': 'absolute_daily_minimum_temperature_threshold',
        'temperature_metric': 'daily_minimum_temperature',
        'temperature_threshold_celsius': FROST_THRESHOLD_C,
        'temperature_threshold_kelvin': FROST_THRESHOLD_K,
        'minimum_consecutive_days': 1,
        'criterion': 'Tmin <= 4C on the target day',

        'source_dataset': ERA5_ID,
        'source_band': TMIN_BAND,
        'source_temperature_units': 'Kelvin',

        'collection': 'MapBiomas Brazil Degradation Collection 11',
        'theme': 'CLIMATIC_WAVES',
        'version': VERSION,
    })

    return frost


# ============================================================================
# 10. PRODUCT IMAGE DISPATCH
# ============================================================================


def create_product_image(target_date, product):
    if product == 'heat':
        return create_wave_image(target_date, 'heat')

    if product == 'frost':
        return create_frost_image(target_date)

    if product == 'cold':
        return create_wave_image(target_date, 'cold')

    raise ValueError(f'Unknown product: {product}')


# ============================================================================
# 11. OUTPUT COLLECTION / ASSET INVENTORY HELPERS
# ============================================================================


def enabled_products():
    return [
        product
        for product in PRODUCT_PRIORITY
        if PRODUCT_CONFIG[product]['enabled']
    ]


def ensure_image_collection(asset_id):
    """Create the output ImageCollection if it does not already exist."""

    try:
        info = ee.data.getAsset(asset_id)
        asset_type = info.get('type')
        print(f'Collection exists: {asset_id} [{asset_type}]')

    except Exception:
        print(f'Creating ImageCollection: {asset_id}')
        ee.data.createAsset(
            {'type': 'IMAGE_COLLECTION'},
            asset_id,
        )


def list_asset_names(parent):
    """Return all immediate child asset resource names under a collection."""

    names = set()
    page_token = None

    while True:
        params = {
            'parent': parent,
            'pageSize': ASSET_LIST_PAGE_SIZE,
            'view': 'BASIC',
        }

        if page_token:
            params['pageToken'] = page_token

        response = ee.data.listAssets(params)

        for asset in response.get('assets', []):
            name = asset.get('name')
            if name:
                names.add(name)

        page_token = response.get('nextPageToken')

        if not page_token:
            break

    return names


def inventory_existing_outputs(products=None):
    """Return {product: set(asset_names)} for requested enabled products."""

    if products is None:
        products = enabled_products()

    inventory = {}

    for product in products:
        inventory[product] = list_asset_names(
            PRODUCT_CONFIG[product]['asset_root']
        )

    return inventory


# ============================================================================
# 12. EXPECTED JOB SPECIFICATIONS
# ============================================================================


def build_job_spec(current_date, product):
    config = PRODUCT_CONFIG[product]
    date_string = format_date(current_date)
    date_name = date_string.replace('-', '_')

    name = (
        f"{config['file_prefix']}_"
        f'{date_name}_'
        f'v{VERSION}'
    )

    asset_id = (
        f"{config['asset_root']}/"
        f'{name}'
    )

    description = (
        f"{config['task_prefix']}_"
        f'{date_name}_'
        f'v{VERSION}'
    )

    return {
        'date': current_date,
        'date_string': date_string,
        'year': current_date.year,
        'product': product,
        'event_type': product,
        'name': name,
        'asset_id': asset_id,
        'description': description,
        'attempts': 0,
        'last_error': '',
    }


def build_all_job_specs():
    """
    Build jobs in STRICT product order:
      all HeatWave dates -> all Frost dates -> all ColdWave dates.
    """

    start_date = parse_date(START_DATE)
    end_date = parse_date(END_DATE)
    jobs = []

    for product in enabled_products():
        for current_date in date_range(start_date, end_date):
            jobs.append(
                build_job_spec(current_date, product)
            )

    return jobs


# ============================================================================
# 13. OPERATION HELPERS
# ============================================================================

ACTIVE_OPERATION_STATES = {
    'PENDING',
    'RUNNING',
    'CANCELLING',
}

TERMINAL_OPERATION_STATES = {
    'SUCCEEDED',
    'FAILED',
    'CANCELLED',
}


def operation_state(operation):
    return (
        operation
        .get('metadata', {})
        .get('state', 'UNKNOWN')
    )


def operation_description(operation):
    return (
        operation
        .get('metadata', {})
        .get('description', '')
    )


def list_operations():
    """
    List Earth Engine operations for the initialized project/user.

    Newer EE clients may accept project=; older clients list from the
    initialized project directly. This wrapper supports both conventions.
    """

    try:
        return ee.data.listOperations(project=PROJECT_PATH)
    except TypeError:
        return ee.data.listOperations()


def current_pending_operation_count(operations=None):
    if operations is None:
        operations = list_operations()

    return sum(
        1
        for operation in operations
        if operation_state(operation) == 'PENDING'
    )


def get_preexisting_active_operations(job_by_description):
    active = {}

    for operation in list_operations():
        state = operation_state(operation)
        description = operation_description(operation)

        if (
            state in ACTIVE_OPERATION_STATES
            and description in job_by_description
        ):
            job = job_by_description[description]

            active[operation['name']] = {
                'operation_name': operation['name'],
                'task_id': operation['name'].split('/')[-1],
                'job': job,
                'state': state,
                'error': '',
                'timing_recorded': False,
            }

    return active


# ============================================================================
# 14. MONITOR STATE + TIMING EVIDENCE
# ============================================================================

# One independent last-50 timing window per product.
TIMING_WINDOWS = {
    product: deque(maxlen=TIMING_WINDOW_SIZE)
    for product in PRODUCT_PRIORITY
}

TIMING_SEEN_OPERATIONS = set()

# Observed RUNNING task count over recent monitor refreshes, per product.
RUNNING_OBSERVATIONS = {
    product: deque(maxlen=40)
    for product in PRODUCT_PRIORITY
}


def record_operation_timing(operation, job):
    """Store timing evidence once for a terminal operation."""

    operation_name = operation.get('name')

    if not operation_name:
        return

    if operation_name in TIMING_SEEN_OPERATIONS:
        return

    state = operation_state(operation)

    if state not in TERMINAL_OPERATION_STATES:
        return

    metadata = operation.get('metadata', {})

    create_time = parse_rfc3339(metadata.get('createTime'))
    start_time = parse_rfc3339(metadata.get('startTime'))
    end_time = parse_rfc3339(metadata.get('endTime'))

    if end_time is None:
        return

    runtime_seconds = None
    lifecycle_seconds = None

    if start_time is not None:
        runtime_seconds = max(
            0.0,
            (end_time - start_time).total_seconds(),
        )

    if create_time is not None:
        lifecycle_seconds = max(
            0.0,
            (end_time - create_time).total_seconds(),
        )

    sample = {
        'operation_name': operation_name,
        'description': job['description'],
        'product': job['product'],
        'state': state,
        'end_epoch': end_time.timestamp(),
        'runtime_seconds': runtime_seconds,
        'lifecycle_seconds': lifecycle_seconds,
    }

    TIMING_WINDOWS[job['product']].append(sample)
    TIMING_SEEN_OPERATIONS.add(operation_name)


def timing_stats(product):
    """
    Moving-window timing statistics from the last <=50 concluded tasks.

    ETA rate uses completion throughput measured from server endTime values:
        (n - 1) completions / span(first endTime, last endTime)

    Runtime/lifecycle medians are shown as supporting evidence.
    """

    samples = list(TIMING_WINDOWS[product])

    if not samples:
        return {
            'n': 0,
            'rate_per_min': None,
            'median_runtime': None,
            'median_lifecycle': None,
            'window_span': None,
        }

    samples.sort(key=lambda sample: sample['end_epoch'])

    runtime_values = [
        sample['runtime_seconds']
        for sample in samples
        if sample['runtime_seconds'] is not None
    ]

    lifecycle_values = [
        sample['lifecycle_seconds']
        for sample in samples
        if sample['lifecycle_seconds'] is not None
    ]

    median_runtime = (
        statistics.median(runtime_values)
        if runtime_values
        else None
    )

    median_lifecycle = (
        statistics.median(lifecycle_values)
        if lifecycle_values
        else None
    )

    rate_per_min = None
    window_span = None

    if len(samples) >= 2:
        raw_span = (
            samples[-1]['end_epoch']
            - samples[0]['end_epoch']
        )

        # Avoid unstable infinite-like rates when many tasks share nearly the
        # same completion timestamp. At minimum use one refresh interval.
        window_span = max(
            float(MONITOR_REFRESH_SECONDS),
            raw_span,
        )

        rate_per_sec = (
            (len(samples) - 1)
            / window_span
        )

        rate_per_min = rate_per_sec * 60.0

    return {
        'n': len(samples),
        'rate_per_min': rate_per_min,
        'median_runtime': median_runtime,
        'median_lifecycle': median_lifecycle,
        'window_span': window_span,
    }


# ============================================================================
# 15. DASHBOARD HELPERS
# ============================================================================

try:
    from IPython.display import HTML, clear_output, display
    _HAS_IPYTHON = True
except Exception:
    HTML = None
    clear_output = None
    display = None
    _HAS_IPYTHON = False


COLOR_GREEN = '#198754'
COLOR_ORANGE = '#fd7e14'
COLOR_GRAY = '#6c757d'
COLOR_RED = '#dc3545'
COLOR_BLUE = '#0d6efd'
COLOR_BG = '#f7f8fa'
COLOR_CARD = '#ffffff'
COLOR_TEXT = '#212529'


def format_duration(seconds):
    if seconds is None:
        return '—'

    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    if days:
        return f'{days}d {hours:02d}:{minutes:02d}'

    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def compress_years(years):
    """Convert [1985,1986,1987,1989] -> '1985-1987, 1989'."""

    years = sorted(set(years))

    if not years:
        return '—'

    ranges = []
    start = previous = years[0]

    for year in years[1:]:
        if year == previous + 1:
            previous = year
            continue

        ranges.append((start, previous))
        start = previous = year

    ranges.append((start, previous))

    parts = []

    for start, end in ranges:
        if start == end:
            parts.append(str(start))
        else:
            parts.append(f'{start}-{end}')

    return ', '.join(parts)


def build_year_summary(product, jobs, status_by_description, current_product):
    grouped = defaultdict(list)

    for job in jobs:
        if job['product'] == product:
            grouped[job['year']].append(job)

    complete_years = []
    running_years = []
    waiting_years = []
    running_detail = []

    for year in sorted(grouped):
        year_jobs = grouped[year]
        expected = len(year_jobs)

        statuses = [
            status_by_description.get(job['description'], 'WAITING')
            for job in year_jobs
        ]

        complete = sum(status == 'COMPLETE' for status in statuses)
        active = sum(
            status in ACTIVE_OPERATION_STATES
            for status in statuses
        )

        if complete == expected:
            complete_years.append(year)

        elif active > 0 or (
            product == current_product
            and complete > 0
        ):
            running_years.append(year)
            running_detail.append(
                f'{year}: {complete:,}/{expected:,}'
            )

        else:
            waiting_years.append(year)

    expected_count_groups = Counter(
        len(year_jobs)
        for year_jobs in grouped.values()
    )

    files_per_year_text = ' · '.join(
        f'{files:,} files × {year_count} year' + ('s' if year_count != 1 else '')
        for files, year_count in sorted(expected_count_groups.items())
    )

    return {
        'complete_years': complete_years,
        'running_years': running_years,
        'waiting_years': waiting_years,
        'running_detail': running_detail,
        'files_per_year_text': files_per_year_text,
    }


def product_metrics(product, jobs, status_by_description, current_product):
    product_jobs = [
        job
        for job in jobs
        if job['product'] == product
    ]

    expected = len(product_jobs)
    statuses = [
        status_by_description.get(job['description'], 'WAITING')
        for job in product_jobs
    ]

    complete = sum(status == 'COMPLETE' for status in statuses)
    active = sum(status in ACTIVE_OPERATION_STATES for status in statuses)
    failed = sum(
        status in {'FAILED', 'CANCELLED', 'SUBMIT_FAILED'}
        for status in statuses
    )
    waiting = max(0, expected - complete - active)

    year_summary = build_year_summary(
        product,
        jobs,
        status_by_description,
        current_product,
    )

    return {
        'expected': expected,
        'complete': complete,
        'active': active,
        'failed': failed,
        'waiting': waiting,
        'year_summary': year_summary,
    }


def product_status_color(metrics, product, current_product):
    if metrics['expected'] > 0 and metrics['complete'] == metrics['expected']:
        return COLOR_GREEN, 'COMPLETED'

    if product == current_product or metrics['active'] > 0:
        return COLOR_ORANGE, 'RUNNING'

    return COLOR_GRAY, 'WAITING'


def compute_eta(all_jobs, status_by_description, current_product):
    """
    Evidence-based ETA.

    Each product has an independent moving window of the last <=50 concluded
    tasks. The primary observed rate is tasks/min from their server endTime
    spacing. For products with no evidence yet, the current product's rate is
    used only as a provisional fallback for an overall estimate.
    """

    remaining_by_product = {}

    for product in enabled_products():
        remaining_by_product[product] = sum(
            1
            for job in all_jobs
            if (
                job['product'] == product
                and status_by_description.get(
                    job['description'],
                    'WAITING',
                ) != 'COMPLETE'
            )
        )

    current_stats = (
        timing_stats(current_product)
        if current_product
        else None
    )

    current_eta = None

    if (
        current_product
        and current_stats
        and current_stats['n'] >= MIN_TIMING_SAMPLES_FOR_ETA
        and current_stats['rate_per_min']
        and current_stats['rate_per_min'] > 0
    ):
        current_eta = (
            remaining_by_product[current_product]
            / current_stats['rate_per_min']
            * 60.0
        )

    overall_seconds = 0.0
    overall_known = True
    provisional = False

    fallback_rate = (
        current_stats['rate_per_min']
        if current_stats
        else None
    )

    for product in enabled_products():
        remaining = remaining_by_product[product]

        if remaining == 0:
            continue

        stats = timing_stats(product)
        rate = None

        if (
            stats['n'] >= MIN_TIMING_SAMPLES_FOR_ETA
            and stats['rate_per_min']
            and stats['rate_per_min'] > 0
        ):
            rate = stats['rate_per_min']

        elif fallback_rate and fallback_rate > 0:
            rate = fallback_rate
            provisional = True

        else:
            overall_known = False
            break

        overall_seconds += remaining / rate * 60.0

    return {
        'remaining_by_product': remaining_by_product,
        'current_stats': current_stats,
        'current_eta': current_eta,
        'overall_eta': overall_seconds if overall_known else None,
        'overall_provisional': provisional,
    }


def render_html_panel(
    phase,
    all_jobs,
    status_by_description,
    current_product,
    controller_start,
    batch_number=None,
    batch_total=None,
    batch_size=0,
    batch_states=None,
    counters=None,
    note=None,
):
    """Render a colored Colab/Jupyter dashboard."""

    counters = Counter(counters or {})
    batch_states = Counter(batch_states or {})

    elapsed = time.monotonic() - controller_start
    eta = compute_eta(
        all_jobs,
        status_by_description,
        current_product,
    )

    if current_product:
        running_now = sum(
            1
            for job in all_jobs
            if (
                job['product'] == current_product
                and status_by_description.get(job['description']) == 'RUNNING'
            )
        )
        RUNNING_OBSERVATIONS[current_product].append(running_now)

    cards = []

    for product in enabled_products():
        config = PRODUCT_CONFIG[product]
        metrics = product_metrics(
            product,
            all_jobs,
            status_by_description,
            current_product,
        )

        color, label = product_status_color(
            metrics,
            product,
            current_product,
        )

        progress = (
            100.0 * metrics['complete'] / metrics['expected']
            if metrics['expected']
            else 100.0
        )

        years = metrics['year_summary']

        running_text = (
            '; '.join(years['running_detail'])
            if years['running_detail']
            else '—'
        )

        stats = timing_stats(product)

        rate_text = (
            f"{stats['rate_per_min']:.1f} tasks/min"
            if stats['rate_per_min']
            else 'collecting evidence'
        )

        cards.append(f"""
        <div class="product-card" style="border-top:6px solid {color};">
          <div class="product-title-row">
            <div class="product-title">{html.escape(config['label'])}</div>
            <div class="badge" style="background:{color};">{label}</div>
          </div>

          <div class="progress-shell">
            <div class="progress-fill" style="width:{progress:.2f}%; background:{color};"></div>
          </div>

          <div class="metric-grid">
            <div><b>{metrics['complete']:,}</b><span>complete</span></div>
            <div><b>{metrics['active']:,}</b><span>active</span></div>
            <div><b>{metrics['waiting']:,}</b><span>waiting</span></div>
            <div><b>{metrics['expected']:,}</b><span>expected</span></div>
          </div>

          <div class="year-row"><b>Expected files/year</b><br>{html.escape(years['files_per_year_text'])}</div>
          <div class="year-row"><b style="color:{COLOR_GREEN};">Completed years</b><br>{html.escape(compress_years(years['complete_years']))}</div>
          <div class="year-row"><b style="color:{COLOR_ORANGE};">Running / partial years</b><br>{html.escape(running_text)}</div>
          <div class="year-row"><b style="color:{COLOR_GRAY};">Waiting years</b><br>{html.escape(compress_years(years['waiting_years']))}</div>

          <div class="evidence-row">
            Last {stats['n']}/{TIMING_WINDOW_SIZE} concluded tasks · {html.escape(rate_text)}
          </div>
        </div>
        """)

    current_label = (
        PRODUCT_CONFIG[current_product]['label']
        if current_product
        else '—'
    )

    batch_label = '—'
    if batch_number is not None:
        batch_label = str(batch_number)
        if batch_total is not None:
            batch_label = f'{batch_number}/{batch_total}'

    current_stats = eta['current_stats'] or {
        'n': 0,
        'rate_per_min': None,
        'median_runtime': None,
        'median_lifecycle': None,
    }

    current_rate_text = (
        f"{current_stats['rate_per_min']:.1f} tasks/min"
        if current_stats.get('rate_per_min')
        else 'collecting evidence'
    )

    overall_eta_text = format_duration(eta['overall_eta'])
    if eta['overall_provisional'] and eta['overall_eta'] is not None:
        overall_eta_text += ' *'

    note_html = ''
    if note:
        note_html = (
            '<div class="note"><b>Note</b><br>'
            + html.escape(str(note)).replace('\n', '<br>')
            + '</div>'
        )

    state_html = ''
    if batch_states:
        state_parts = []
        state_order = [
            'PENDING',
            'RUNNING',
            'CANCELLING',
            'SUCCEEDED',
            'FAILED',
            'CANCELLED',
            'SUBMIT_FAILED',
            'UNKNOWN',
        ]

        state_colors = {
            'PENDING': COLOR_GRAY,
            'RUNNING': COLOR_ORANGE,
            'CANCELLING': COLOR_ORANGE,
            'SUCCEEDED': COLOR_GREEN,
            'FAILED': COLOR_RED,
            'CANCELLED': COLOR_RED,
            'SUBMIT_FAILED': COLOR_RED,
            'UNKNOWN': COLOR_GRAY,
        }

        for state in state_order:
            if batch_states.get(state, 0):
                state_parts.append(
                    f'<span class="state-chip" style="border-color:{state_colors[state]};">'
                    f'<b>{state}</b> {batch_states[state]:,}</span>'
                )

        state_html = '<div class="states">' + ''.join(state_parts) + '</div>'

    html_output = f"""
    <style>
      .mb-wrap {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
        color: {COLOR_TEXT}; background:{COLOR_BG}; border-radius:16px;
        padding:18px; border:1px solid #e5e7eb;
      }}
      .mb-header {{display:flex; justify-content:space-between; gap:12px; align-items:flex-start; margin-bottom:14px;}}
      .mb-title {{font-size:22px; font-weight:800;}}
      .mb-subtitle {{font-size:12px; color:#6b7280; margin-top:4px;}}
      .phase {{background:{COLOR_BLUE}; color:white; padding:7px 10px; border-radius:999px; font-size:12px; font-weight:700;}}
      .top-grid {{display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin-bottom:12px;}}
      .top-card {{background:white; border:1px solid #e5e7eb; border-radius:12px; padding:11px 12px;}}
      .top-card b {{font-size:18px; display:block;}}
      .top-card span {{font-size:11px; color:#6b7280; text-transform:uppercase; letter-spacing:.04em;}}
      .products {{display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px;}}
      .product-card {{background:{COLOR_CARD}; border:1px solid #e5e7eb; border-radius:14px; padding:13px; box-shadow:0 1px 2px rgba(0,0,0,.04);}}
      .product-title-row {{display:flex; justify-content:space-between; align-items:center; gap:8px;}}
      .product-title {{font-size:18px; font-weight:800;}}
      .badge {{color:white; font-size:10px; font-weight:800; padding:4px 7px; border-radius:999px;}}
      .progress-shell {{height:8px; background:#eceff2; border-radius:999px; overflow:hidden; margin:10px 0 12px;}}
      .progress-fill {{height:100%; border-radius:999px;}}
      .metric-grid {{display:grid; grid-template-columns:repeat(4,1fr); gap:6px; margin-bottom:10px;}}
      .metric-grid div {{background:#f8f9fa; border-radius:8px; padding:7px; text-align:center;}}
      .metric-grid b {{display:block; font-size:14px;}}
      .metric-grid span {{font-size:9px; color:#6b7280; text-transform:uppercase;}}
      .year-row {{font-size:11px; line-height:1.35; padding:5px 0; border-top:1px solid #f0f0f0;}}
      .evidence-row {{font-size:10px; color:#6b7280; margin-top:7px; padding-top:7px; border-top:1px dashed #ddd;}}
      .states {{display:flex; flex-wrap:wrap; gap:7px; margin-top:12px;}}
      .state-chip {{background:white; border:1px solid; border-radius:999px; padding:5px 8px; font-size:11px;}}
      .note {{margin-top:12px; background:#fff8e6; border:1px solid #ffe0a3; border-radius:10px; padding:10px 12px; font-size:11px;}}
      .footer {{margin-top:12px; font-size:10px; color:#6b7280;}}
      @media (max-width: 1000px) {{
        .products {{grid-template-columns:1fr;}}
        .top-grid {{grid-template-columns:repeat(2,1fr);}}
      }}
    </style>

    <div class="mb-wrap">
      <div class="mb-header">
        <div>
          <div class="mb-title">MapBiomas Brazil · Climatic Products Export Monitor</div>
          <div class="mb-subtitle">
            {START_DATE} → {END_DATE} · strict priority: HeatWave → Frost → ColdWave · refresh every {MONITOR_REFRESH_SECONDS}s
          </div>
        </div>
        <div class="phase">{html.escape(str(phase))}</div>
      </div>

      <div class="top-grid">
        <div class="top-card"><b>{html.escape(current_label)}</b><span>current product</span></div>
        <div class="top-card"><b>{html.escape(batch_label)}</b><span>batch · size {batch_size:,}</span></div>
        <div class="top-card"><b>{format_duration(eta['current_eta'])}</b><span>current product ETA</span></div>
        <div class="top-card"><b>{html.escape(overall_eta_text)}</b><span>overall ETA</span></div>
      </div>

      <div class="top-grid">
        <div class="top-card"><b>{current_stats['n']}/{TIMING_WINDOW_SIZE}</b><span>timing samples</span></div>
        <div class="top-card"><b>{html.escape(current_rate_text)}</b><span>moving completion rate</span></div>
        <div class="top-card"><b>{format_duration(current_stats.get('median_runtime'))}</b><span>median runtime · last window</span></div>
        <div class="top-card"><b>{format_duration(elapsed)}</b><span>controller elapsed</span></div>
      </div>

      <div class="products">
        {''.join(cards)}
      </div>

      {state_html}
      {note_html}

      <div class="footer">
        Green = completed · Orange = running / current phase · Gray = waiting.
        ETA uses the moving completion evidence from the last {TIMING_WINDOW_SIZE} concluded tasks per product.
        * Overall ETA is provisional when a future product has no timing evidence yet and the current-product rate is used as fallback.
      </div>
    </div>
    """

    clear_output(wait=True)
    display(HTML(html_output))


def render_text_panel(
    phase,
    all_jobs,
    status_by_description,
    current_product,
    controller_start,
    batch_number=None,
    batch_total=None,
    batch_size=0,
    batch_states=None,
    counters=None,
    note=None,
):
    """Fallback text dashboard for non-IPython terminals."""

    print('\033[2J\033[H', end='')

    elapsed = time.monotonic() - controller_start
    eta = compute_eta(
        all_jobs,
        status_by_description,
        current_product,
    )

    print('=' * 96)
    print('MAPBIOMAS BRAZIL - CLIMATIC PRODUCTS | EARTH ENGINE EXPORT MONITOR')
    print('=' * 96)
    print(f'Phase: {phase}')
    print(f'Period: {START_DATE} -> {END_DATE}')
    print(f'Current product: {PRODUCT_CONFIG[current_product]["label"] if current_product else "-"}')
    print(f'Elapsed: {format_duration(elapsed)}')
    print(f'Current ETA: {format_duration(eta["current_eta"])}')
    print(f'Overall ETA: {format_duration(eta["overall_eta"])}')

    if batch_number is not None:
        print(f'Batch: {batch_number}/{batch_total} | size={batch_size:,}')

    print('-' * 96)

    for product in enabled_products():
        metrics = product_metrics(
            product,
            all_jobs,
            status_by_description,
            current_product,
        )
        years = metrics['year_summary']
        color, label = product_status_color(metrics, product, current_product)
        _ = color

        print(
            f"{PRODUCT_CONFIG[product]['label']:<10} {label:<10} "
            f"complete={metrics['complete']:,}/{metrics['expected']:,} "
            f"active={metrics['active']:,} waiting={metrics['waiting']:,}"
        )
        print(f"  completed years: {compress_years(years['complete_years'])}")
        print(f"  running years:   {'; '.join(years['running_detail']) if years['running_detail'] else '-'}")
        print(f"  waiting years:   {compress_years(years['waiting_years'])}")

    if batch_states:
        print('-' * 96)
        print('Batch states:', dict(Counter(batch_states)))

    if note:
        print('-' * 96)
        print(note)

    print('=' * 96)


def render_panel(**kwargs):
    if _HAS_IPYTHON:
        render_html_panel(**kwargs)
    else:
        render_text_panel(**kwargs)


# ============================================================================
# 16. QUEUE CAPACITY
# ============================================================================


def wait_for_queue_capacity(
    requested_slots,
    panel_context,
):
    while True:
        operations = list_operations()
        pending = current_pending_operation_count(operations)
        allowed = PROJECT_READY_LIMIT - QUEUE_SAFETY_MARGIN

        if pending + requested_slots <= allowed:
            return

        render_panel(
            phase='WAITING_FOR_QUEUE_CAPACITY',
            note=(
                f'Current project PENDING operations: {pending:,}\n'
                f'Requested new slots: {requested_slots:,}\n'
                f'Safety ceiling: {allowed:,}'
            ),
            **panel_context,
        )

        time.sleep(MONITOR_REFRESH_SECONDS)


# ============================================================================
# 17. EXPORT CREATION
# ============================================================================


def create_export_task(job):
    image = create_product_image(
        job['date'],
        job['product'],
    )

    return ee.batch.Export.image.toAsset(
        image=image,
        description=job['description'],
        assetId=job['asset_id'],
        region=brazil,
        scale=EXPORT_SCALE,
        maxPixels=MAX_PIXELS,
        pyramidingPolicy={
            '.default': 'mode',
        },
    )


def submit_job(job, status_by_description):
    task = create_export_task(job)
    task.start()

    job['attempts'] += 1
    status_by_description[job['description']] = 'PENDING'

    return {
        'operation_name': task.operation_name,
        'task_id': task.id,
        'job': job,
        'state': 'PENDING',
        'error': '',
        'timing_recorded': False,
    }


# ============================================================================
# 18. LIVE OPERATION MONITOR
# ============================================================================


def refresh_watched_operations(
    watched,
    status_by_description,
):
    """Refresh watched operation states with one listOperations() fast path."""

    operations = list_operations()

    listed = {
        operation.get('name'): operation
        for operation in operations
        if operation.get('name')
    }

    newly_terminal_operations = []

    for operation_name, record in watched.items():
        if record['state'] in TERMINAL_OPERATION_STATES:
            continue

        operation = listed.get(operation_name)

        if operation is None:
            try:
                operation = ee.data.getOperation(operation_name)
            except Exception as exc:
                record['error'] = str(exc)
                continue

        state = operation_state(operation)
        record['state'] = state

        description = record['job']['description']

        if state == 'SUCCEEDED':
            status_by_description[description] = 'COMPLETE'

        elif state == 'FAILED':
            status_by_description[description] = 'FAILED'
            error = operation.get('error', {})
            record['error'] = error.get('message', str(error))

        elif state == 'CANCELLED':
            status_by_description[description] = 'CANCELLED'
            record['error'] = 'Earth Engine operation was cancelled.'

        else:
            status_by_description[description] = state

        if (
            state in TERMINAL_OPERATION_STATES
            and not record.get('timing_recorded', False)
        ):
            newly_terminal_operations.append(
                (operation, record)
            )
            record['timing_recorded'] = True

    # Sort by server endTime before appending into the moving windows so the
    # deque truly retains the latest concluded tasks.
    def end_sort_key(item):
        operation, _record = item
        metadata = operation.get('metadata', {})
        end_time = parse_rfc3339(metadata.get('endTime'))
        return end_time.timestamp() if end_time else 0.0

    newly_terminal_operations.sort(key=end_sort_key)

    for operation, record in newly_terminal_operations:
        record_operation_timing(
            operation,
            record['job'],
        )

    return watched


def watched_state_counts(watched):
    return Counter(
        record.get('state', 'UNKNOWN')
        for record in watched.values()
    )


def all_watched_terminal(watched):
    return all(
        record.get('state') in TERMINAL_OPERATION_STATES
        for record in watched.values()
    )


def monitor_watched_operations(
    watched,
    panel_context,
    phase,
    status_by_description,
):
    while True:
        refresh_watched_operations(
            watched,
            status_by_description,
        )

        states = watched_state_counts(watched)

        render_panel(
            phase=phase,
            batch_states=states,
            **panel_context,
        )

        if all_watched_terminal(watched):
            return watched

        time.sleep(MONITOR_REFRESH_SECONDS)


# ============================================================================
# 19. ONE STRICT BATCH
# ============================================================================


def run_strict_batch(
    batch_jobs,
    batch_number,
    batch_total,
    counters,
    all_jobs,
    status_by_description,
    current_product,
    controller_start,
    phase_prefix='RUNNING',
):
    """Submit one product-homogeneous batch and wait until it is terminal."""

    panel_context = {
        'all_jobs': all_jobs,
        'status_by_description': status_by_description,
        'current_product': current_product,
        'controller_start': controller_start,
        'batch_number': batch_number,
        'batch_total': batch_total,
        'batch_size': len(batch_jobs),
        'counters': counters,
    }

    wait_for_queue_capacity(
        requested_slots=len(batch_jobs),
        panel_context=panel_context,
    )

    watched = {}
    submit_failures = []
    last_panel = 0.0

    for index, job in enumerate(batch_jobs, start=1):
        try:
            record = submit_job(
                job,
                status_by_description,
            )
            watched[record['operation_name']] = record
            counters['submitted'] += 1

        except Exception as exc:
            job['attempts'] += 1
            job['last_error'] = str(exc)
            submit_failures.append(job)
            counters['failed'] += 1
            status_by_description[job['description']] = 'SUBMIT_FAILED'

        now = time.monotonic()

        if (
            now - last_panel >= MONITOR_REFRESH_SECONDS
            or index == len(batch_jobs)
        ):
            submit_states = Counter({'PENDING': len(watched)})

            if submit_failures:
                submit_states['SUBMIT_FAILED'] = len(submit_failures)

            render_panel(
                phase=f'{phase_prefix}_SUBMITTING',
                batch_states=submit_states,
                note=(
                    f'Submission progress: {index:,}/{len(batch_jobs):,}'
                ),
                **panel_context,
            )

            last_panel = now

    if watched:
        monitor_watched_operations(
            watched=watched,
            panel_context=panel_context,
            phase=f'{phase_prefix}_BATCH',
            status_by_description=status_by_description,
        )

    succeeded_jobs = []
    failed_jobs = list(submit_failures)

    for record in watched.values():
        job = record['job']
        state = record['state']

        if state == 'SUCCEEDED':
            succeeded_jobs.append(job)
            counters['completed'] += 1

        else:
            job['last_error'] = record.get('error', '')
            failed_jobs.append(job)
            counters['failed'] += 1

    return succeeded_jobs, failed_jobs


# ============================================================================
# 20. RETRY FAILED JOBS
# ============================================================================


def retry_failed_jobs(
    failed_jobs,
    batch_number,
    batch_total,
    counters,
    all_jobs,
    status_by_description,
    current_product,
    controller_start,
):
    retry_queue = list(failed_jobs)
    permanently_failed = []
    retry_round = 0

    while retry_queue:
        eligible = [
            job
            for job in retry_queue
            if job['attempts'] <= MAX_SCRIPT_RETRIES
        ]

        exhausted = [
            job
            for job in retry_queue
            if job['attempts'] > MAX_SCRIPT_RETRIES
        ]

        permanently_failed.extend(exhausted)

        if not eligible:
            break

        retry_round += 1
        next_retry_queue = []

        for start_index in range(0, len(eligible), BATCH_SIZE):
            retry_chunk = eligible[
                start_index:start_index + BATCH_SIZE
            ]

            _, failed_again = run_strict_batch(
                batch_jobs=retry_chunk,
                batch_number=batch_number,
                batch_total=batch_total,
                counters=counters,
                all_jobs=all_jobs,
                status_by_description=status_by_description,
                current_product=current_product,
                controller_start=controller_start,
                phase_prefix=f'RETRY_{retry_round}',
            )

            next_retry_queue.extend(failed_again)

        retry_queue = next_retry_queue

    return permanently_failed


# ============================================================================
# 21. REPORT HELPERS
# ============================================================================


def write_jobs_csv(path, jobs):
    with open(path, 'w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                'date',
                'product',
                'description',
                'asset_id',
                'attempts',
                'last_error',
            ],
        )

        writer.writeheader()

        for job in jobs:
            writer.writerow({
                'date': job['date_string'],
                'product': job['product'],
                'description': job['description'],
                'asset_id': job['asset_id'],
                'attempts': job['attempts'],
                'last_error': job.get('last_error', ''),
            })


# ============================================================================
# 22. STATUS INITIALIZATION / INVENTORY APPLICATION
# ============================================================================


def apply_inventory_to_status(
    jobs,
    inventory,
    status_by_description,
    product=None,
):
    for job in jobs:
        if product is not None and job['product'] != product:
            continue

        product_inventory = inventory.get(job['product'], set())

        if job['asset_id'] in product_inventory:
            status_by_description[job['description']] = 'COMPLETE'

        elif status_by_description.get(job['description']) == 'COMPLETE':
            # An asset previously marked complete disappeared between checks.
            status_by_description[job['description']] = 'WAITING'


def missing_jobs_for_product(
    product,
    product_jobs,
    product_inventory,
):
    return [
        job
        for job in product_jobs
        if job['asset_id'] not in product_inventory
    ]


# ============================================================================
# 23. CONTROLLER
# ============================================================================


def main():
    controller_start = time.monotonic()

    # ------------------------------------------------------------------------
    # 23.1 Ensure all enabled output collections exist.
    # ------------------------------------------------------------------------

    for product in enabled_products():
        ensure_image_collection(
            PRODUCT_CONFIG[product]['asset_root']
        )

    # ------------------------------------------------------------------------
    # 23.2 Build all expected jobs in strict product priority order.
    # ------------------------------------------------------------------------

    all_jobs = build_all_job_specs()

    jobs_by_product = {
        product: [
            job
            for job in all_jobs
            if job['product'] == product
        ]
        for product in enabled_products()
    }

    job_by_description = {
        job['description']: job
        for job in all_jobs
    }

    status_by_description = {
        job['description']: 'WAITING'
        for job in all_jobs
    }

    counters = Counter({
        'submitted': 0,
        'completed': 0,
        'failed': 0,
        'skipped_later': 0,
    })

    # ------------------------------------------------------------------------
    # 23.3 CHECKER FIRST: inventory all three output collections.
    # ------------------------------------------------------------------------

    render_panel(
        phase='CHECKING_EXISTING_ASSETS',
        all_jobs=all_jobs,
        status_by_description=status_by_description,
        current_product=None,
        controller_start=controller_start,
        counters=counters,
        note=(
            'Scanning HeatWave, Frost and ColdWave collections before any '
            'new task submission.'
        ),
    )

    inventory = inventory_existing_outputs()

    apply_inventory_to_status(
        all_jobs,
        inventory,
        status_by_description,
    )

    # ------------------------------------------------------------------------
    # 23.4 Detect already-active operations from a restarted run.
    # ------------------------------------------------------------------------

    preexisting_active = get_preexisting_active_operations(
        job_by_description
    )

    # Ignore operations whose output asset already exists.
    filtered_preexisting = {}

    for operation_name, record in preexisting_active.items():
        job = record['job']

        if job['asset_id'] in inventory[job['product']]:
            continue

        filtered_preexisting[operation_name] = record
        status_by_description[job['description']] = record['state']

    preexisting_active = filtered_preexisting

    # ------------------------------------------------------------------------
    # 23.5 Strict-priority guard against active lower-priority tasks.
    # ------------------------------------------------------------------------

    if STRICT_PRODUCT_PRIORITY and preexisting_active:
        first_incomplete_product = None

        for product in enabled_products():
            product_missing = missing_jobs_for_product(
                product,
                jobs_by_product[product],
                inventory[product],
            )

            if product_missing:
                first_incomplete_product = product
                break

        out_of_priority = [
            record
            for record in preexisting_active.values()
            if record['job']['product'] != first_incomplete_product
        ]

        if out_of_priority:
            render_panel(
                phase='STOPPED_OUT_OF_PRIORITY_ACTIVE_TASKS',
                all_jobs=all_jobs,
                status_by_description=status_by_description,
                current_product=first_incomplete_product,
                controller_start=controller_start,
                counters=counters,
                note=(
                    f'Found {len(out_of_priority):,} active workflow tasks '
                    'belonging to a later product. Strict priority would be '
                    'violated. Cancel those tasks first, then rerun.'
                ),
            )
            return

    permanently_failed_all = []

    # ========================================================================
    # 23.6 PRODUCT-BY-PRODUCT EXECUTION
    # ========================================================================

    for product_index, product in enumerate(
        enabled_products(),
        start=1,
    ):
        config = PRODUCT_CONFIG[product]
        product_jobs = jobs_by_product[product]

        # --------------------------------------------------------------------
        # Wait for an interrupted/restarted active batch of THIS product.
        # --------------------------------------------------------------------

        product_preexisting = {
            name: record
            for name, record in preexisting_active.items()
            if record['job']['product'] == product
        }

        if product_preexisting:
            monitor_watched_operations(
                watched=product_preexisting,
                panel_context={
                    'all_jobs': all_jobs,
                    'status_by_description': status_by_description,
                    'current_product': product,
                    'controller_start': controller_start,
                    'batch_size': len(product_preexisting),
                    'counters': counters,
                },
                phase=f'{config["label"]}_WAITING_FOR_PREEXISTING',
                status_by_description=status_by_description,
            )

        # --------------------------------------------------------------------
        # Fresh checker for current product.
        # --------------------------------------------------------------------

        product_inventory = list_asset_names(
            config['asset_root']
        )

        inventory[product] = product_inventory

        apply_inventory_to_status(
            all_jobs,
            inventory,
            status_by_description,
            product=product,
        )

        pending_jobs = missing_jobs_for_product(
            product,
            product_jobs,
            product_inventory,
        )

        if not pending_jobs:
            render_panel(
                phase=f'{config["label"]}_ALREADY_COMPLETE',
                all_jobs=all_jobs,
                status_by_description=status_by_description,
                current_product=product,
                controller_start=controller_start,
                counters=counters,
                note=(
                    f'{config["label"]}: all expected daily assets already exist. '
                    'Advancing to the next product.'
                ),
            )
            continue

        # --------------------------------------------------------------------
        # Strict homogeneous batches for this product only.
        # --------------------------------------------------------------------

        main_batch_number = 0
        permanently_failed_product = []

        while pending_jobs:
            # Re-scan current product before each new batch. This handles assets
            # created by another notebook/user since the previous checker.
            product_inventory = list_asset_names(
                config['asset_root']
            )

            inventory[product] = product_inventory

            refreshed_pending = []
            skipped_now = 0

            for job in pending_jobs:
                if job['asset_id'] in product_inventory:
                    status_by_description[job['description']] = 'COMPLETE'
                    skipped_now += 1
                else:
                    refreshed_pending.append(job)

            counters['skipped_later'] += skipped_now
            pending_jobs = refreshed_pending

            if not pending_jobs:
                break

            main_batch_number += 1

            batches_remaining = math.ceil(
                len(pending_jobs) / BATCH_SIZE
            )

            batch_total_display = (
                main_batch_number
                + batches_remaining
                - 1
            )

            batch_jobs = pending_jobs[:BATCH_SIZE]
            pending_jobs = pending_jobs[BATCH_SIZE:]

            # Hard assertion: never mix products in one batch.
            assert all(
                job['product'] == product
                for job in batch_jobs
            )

            _, failed_jobs = run_strict_batch(
                batch_jobs=batch_jobs,
                batch_number=main_batch_number,
                batch_total=batch_total_display,
                counters=counters,
                all_jobs=all_jobs,
                status_by_description=status_by_description,
                current_product=product,
                controller_start=controller_start,
                phase_prefix=config['label'].upper(),
            )

            if failed_jobs:
                exhausted = retry_failed_jobs(
                    failed_jobs=failed_jobs,
                    batch_number=main_batch_number,
                    batch_total=batch_total_display,
                    counters=counters,
                    all_jobs=all_jobs,
                    status_by_description=status_by_description,
                    current_product=product,
                    controller_start=controller_start,
                )

                permanently_failed_product.extend(exhausted)

        permanently_failed_all.extend(
            permanently_failed_product
        )

        # --------------------------------------------------------------------
        # PRODUCT GATE: verify the ENTIRE product series before advancing.
        # --------------------------------------------------------------------

        final_product_inventory = list_asset_names(
            config['asset_root']
        )

        inventory[product] = final_product_inventory

        apply_inventory_to_status(
            all_jobs,
            inventory,
            status_by_description,
            product=product,
        )

        final_product_missing = missing_jobs_for_product(
            product,
            product_jobs,
            final_product_inventory,
        )

        if final_product_missing:
            write_jobs_csv(
                f'{product}_missing_after_product_phase.csv',
                final_product_missing,
            )

            render_panel(
                phase=f'{config["label"]}_INCOMPLETE',
                all_jobs=all_jobs,
                status_by_description=status_by_description,
                current_product=product,
                controller_start=controller_start,
                counters=counters,
                note=(
                    f'{config["label"]} still has '
                    f'{len(final_product_missing):,} missing outputs after retries.\n'
                    f'Report: {product}_missing_after_product_phase.csv'
                ),
            )

            if STOP_IF_PRODUCT_INCOMPLETE:
                # Strict priority: do not begin the next product if the current
                # product time series is not complete.
                if permanently_failed_all:
                    write_jobs_csv(
                        FAILED_REPORT_CSV,
                        permanently_failed_all,
                    )
                return

        else:
            render_panel(
                phase=f'{config["label"]}_COMPLETE',
                all_jobs=all_jobs,
                status_by_description=status_by_description,
                current_product=product,
                controller_start=controller_start,
                counters=counters,
                note=(
                    f'{config["label"]}: full {START_DATE} -> {END_DATE} '
                    'daily series verified. Product gate passed.'
                ),
            )

    # ========================================================================
    # 23.7 FINAL VERIFICATION OF ALL PRODUCTS
    # ========================================================================

    final_inventory = inventory_existing_outputs()

    apply_inventory_to_status(
        all_jobs,
        final_inventory,
        status_by_description,
    )

    final_missing_jobs = [
        job
        for job in all_jobs
        if job['asset_id'] not in final_inventory[job['product']]
    ]

    if final_missing_jobs:
        write_jobs_csv(
            MISSING_REPORT_CSV,
            final_missing_jobs,
        )

    if permanently_failed_all:
        write_jobs_csv(
            FAILED_REPORT_CSV,
            permanently_failed_all,
        )

    note_lines = [
        f'Total expected: {len(all_jobs):,}',
        f'Total present:  {len(all_jobs) - len(final_missing_jobs):,}',
        f'Total missing:  {len(final_missing_jobs):,}',
    ]

    if final_missing_jobs:
        note_lines.append(
            f'Missing report: {MISSING_REPORT_CSV}'
        )

    if permanently_failed_all:
        note_lines.append(
            f'Failure report: {FAILED_REPORT_CSV}'
        )

    render_panel(
        phase=(
            'COMPLETE'
            if not final_missing_jobs
            else 'COMPLETE_WITH_MISSING_OUTPUTS'
        ),
        all_jobs=all_jobs,
        status_by_description=status_by_description,
        current_product=None,
        controller_start=controller_start,
        counters=counters,
        note='\n'.join(note_lines),
    )


# ============================================================================
# 24. RUN
# ============================================================================

if __name__ == '__main__':
    main()
