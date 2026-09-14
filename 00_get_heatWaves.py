# ============================================================================
# MAPBIOMAS BRAZIL - HEAT WAVES V2 (WSDI / TX90)
# PYTHON / GOOGLE EARTH ENGINE API
#
# Daily binary HeatWave product based on the ETCCDI/WSDI temperature criterion:
#   1) Reference period: 1991-2020
#   2) TX90 for each calendar day from a centered 5-day window
#      (d-2, d-1, d, d+1, d+2), 30 years x 5 days = 150 values/pixel
#   3) Hot day: daily Tmax > TX90(calendar day)
#   4) Heat wave: target day belongs to a sequence of >= 6 consecutive hot days
#   5) Daily output: 0 = no heat wave, 1 = heat wave
#
# Leap-day rule:
#   - Feb 29 is EXCLUDED from the 365-day climatology.
#   - In leap years, Feb 29 is retained in the analysed daily series and uses
#     the TX90 threshold of Feb 28.
#   - Dates after Feb 29 are matched by month/day, so there is no DOY shift.
#
# Bootstrap:
#   - NOT applied, by methodological decision.
#
# Output:
#   projects/mapbiomas-brazil/assets/DEGRADATION/COLLECTION-11/
#   CLIMATIC_WAVES/heatWaves_v2
#
# Batch/monitor design retained from the previous workflow:
#   - batches of 1500 tasks
#   - project queue safety check
#   - live HTML/text dashboard
#   - restart-safe inventory scan
#   - detection/monitoring of pre-existing active tasks from this workflow
#   - retries for failed tasks
#   - final missing/failed CSV reports
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

# In Colab this opens the usual authentication flow when needed.
ee.Authenticate()
ee.Initialize(project=PROJECT)

print(f'Earth Engine initialized with project: {PROJECT}')


# ============================================================================
# 1. PARAMETERS
# ============================================================================

VERSION = 2

# ---------------------------------------------------------------------------
# Analysis period
# END_DATE is inclusive.
# ---------------------------------------------------------------------------

START_DATE = '1985-01-01'
END_DATE   = '2026-09-06'


# ---------------------------------------------------------------------------
# Climatological reference period
# filterDate() uses an exclusive end.
# ---------------------------------------------------------------------------

CLIM_START = '1991-01-01'
CLIM_END   = '2021-01-01'
CLIMATOLOGY_LABEL = '1991-2020'


# ---------------------------------------------------------------------------
# WSDI / TX90 definition
# ---------------------------------------------------------------------------

TX90_PERCENTILE = 90
CLIM_WINDOW_RADIUS_DAYS = 2
CLIM_WINDOW_DAYS = 5
MIN_WAVE_DAYS = 6
NOMINAL_CLIM_SAMPLE_SIZE = 30 * CLIM_WINDOW_DAYS  # 150

# A non-leap template year used only to build the 365-day climatological
# calendar and centered month/day windows.
CLIM_CALENDAR_TEMPLATE_YEAR = 2001


# ---------------------------------------------------------------------------
# Output ImageCollection
# ---------------------------------------------------------------------------

HEAT_ASSET_ROOT = (
    'projects/mapbiomas-brazil/assets/'
    'DEGRADATION/COLLECTION-11/'
    'CLIMATIC_WAVES/heatWaves_v2'
)

TASK_PREFIX = 'HW2'
FILE_PREFIX = 'heat_wave'
BAND_NAME = 'heat_wave'
EVENT_NAME = 'heat_wave'


# ---------------------------------------------------------------------------
# ERA5-Land
# ---------------------------------------------------------------------------

ERA5_ID = 'ECMWF/ERA5_LAND/DAILY_AGGR'
TMAX_BAND = 'temperature_2m_max'


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

# ETA evidence based on the latest concluded workflow tasks.
TIMING_WINDOW_SIZE = 50
MIN_TIMING_SAMPLES_FOR_ETA = 5

MISSING_REPORT_CSV = 'heatwaves_v2_missing_after_run.csv'
FAILED_REPORT_CSV = 'heatwaves_v2_failed_tasks.csv'


# ============================================================================
# 2. BRAZIL REGION
# ============================================================================

# No clip(). Brazil is used only as the export region.
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

era5_climatology = (
    era5
    .filterDate(CLIM_START, CLIM_END)
    .select(TMAX_BAND)
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
# 5. 365-DAY CLIMATOLOGICAL CALENDAR / TX90 CACHE
# ============================================================================

# Cache one server-side TX90 image per climatological month/day.
# There are at most 365 entries.
tx90_cache = {}



def climatological_month_day(date):
    """
    Map a real date to the 365-day climatological calendar.

    Feb 29 is retained in the analysed series but uses Feb 28 TX90.
    Every other date maps to its own month/day, preventing leap-year DOY shift.
    """

    if date.month == 2 and date.day == 29:
        return 2, 28

    return date.month, date.day



def centered_365_month_days(month, day):
    """
    Return the 5 climatological month/day pairs centered on (month, day)
    using a non-leap calendar.

    Examples:
      Jan 01 -> Dec 30, Dec 31, Jan 01, Jan 02, Jan 03
      Feb 28 -> Feb 26, Feb 27, Feb 28, Mar 01, Mar 02
      Mar 01 -> Feb 27, Feb 28, Mar 01, Mar 02, Mar 03

    Therefore Feb 29 never enters the climatology.
    """

    anchor = datetime(
        CLIM_CALENDAR_TEMPLATE_YEAR,
        month,
        day,
        tzinfo=timezone.utc,
    )

    pairs = []

    for offset in range(
        -CLIM_WINDOW_RADIUS_DAYS,
        CLIM_WINDOW_RADIUS_DAYS + 1,
    ):
        shifted = anchor + timedelta(days=offset)
        pairs.append((shifted.month, shifted.day))

    return pairs



def month_day_filter(month, day):
    """Earth Engine filter for one calendar month/day pair."""

    return ee.Filter.And(
        ee.Filter.calendarRange(month, month, 'month'),
        ee.Filter.calendarRange(day, day, 'day_of_month'),
    )



def get_tx90_threshold(date):
    """
    Return the pixel-wise TX90 threshold for date's climatological month/day.

    The sample is the 5-day centered climatological window pooled over
    1991-2020. Because the month/day window is defined on a 365-day calendar,
    Feb 29 is excluded and each threshold has a nominal 150 daily values/pixel.
    """

    month, day = climatological_month_day(date)
    key = (month, day)

    if key not in tx90_cache:
        month_days = centered_365_month_days(month, day)

        filters = [
            month_day_filter(m, d)
            for m, d in month_days
        ]

        window_collection = era5_climatology.filter(
            ee.Filter.Or(*filters)
        )

        tx90_cache[key] = (
            window_collection
            .reduce(ee.Reducer.percentile([TX90_PERCENTILE]))
            .rename('tx90')
        )

    return tx90_cache[key]


# ============================================================================
# 6. GET ONE DAILY ERA5 TMAX IMAGE
# ============================================================================


def get_daily_tmax(date):
    """Return one ERA5-Land daily Tmax image."""

    date_string = format_date(date)
    start = ee.Date(date_string)
    end = start.advance(1, 'day')

    return ee.Image(
        era5
        .filterDate(start, end)
        .select(TMAX_BAND)
        .first()
    )


# ============================================================================
# 7. DAILY HOT-DAY CANDIDATE
# ============================================================================


def get_hot_day_candidate(date):
    """
    Pixel-wise daily WSDI temperature condition:

        Tmax(date) > TX90(climatological calendar day)

    Note the strict '>' operator.
    """

    temperature = get_daily_tmax(date)
    tx90 = get_tx90_threshold(date)

    return temperature.gt(tx90)


# ============================================================================
# 8. CREATE FINAL DAILY HEAT-WAVE IMAGE
# ============================================================================


def create_heat_wave_image(target_date):
    """
    Determine whether each pixel on target_date belongs to a heat spell of
    >= 6 consecutive hot days.

    For MIN_WAVE_DAYS = 6, target day t can belong to one of six minimal
    six-day windows:

        [t-5, t]
        [t-4, t+1]
        [t-3, t+2]
        [t-2, t+3]
        [t-1, t+4]
        [t,   t+5]

    Therefore each target-date export graph references only 11 hot-day
    candidates (t-5 ... t+5), not the full time series.
    """

    candidates = []

    for offset in range(
        -(MIN_WAVE_DAYS - 1),
        MIN_WAVE_DAYS,
    ):
        candidate_date = target_date + timedelta(days=offset)
        candidates.append(
            get_hot_day_candidate(candidate_date)
        )

    windows = []

    # There are MIN_WAVE_DAYS possible 6-day windows containing target_date.
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

    event = (
        event
        .gt(0)
        .rename(BAND_NAME)
        .unmask(0)
        .toUint8()
    )

    event = event.set({
        'system:time_start': ee.Date(date_string).millis(),
        'date': date_string,
        'year': target_date.year,
        'month': target_date.month,
        'day': target_date.day,

        'event_type': EVENT_NAME,
        'index_family': 'WSDI',
        'band_name': BAND_NAME,
        'territory': 'Brazil',
        'pixel_type': 'uint8',
        'value_0': 'no_heat_wave',
        'value_1': 'heat_wave',

        'method': 'daily_binary_WSDI_TX90_membership',
        'temperature_metric': 'daily_maximum_temperature',
        'criterion': 'Tmax > calendar-day TX90 for >=6 consecutive days',
        'comparison_operator': '>',
        'percentile': TX90_PERCENTILE,
        'minimum_consecutive_days': MIN_WAVE_DAYS,

        'climatology_start': CLIM_START,
        'climatology_end': '2020-12-31',
        'climatology_period': CLIMATOLOGY_LABEL,
        'climatology_calendar_days': 365,
        'climatology_window_days': CLIM_WINDOW_DAYS,
        'climatology_window': 'd-2,d-1,d,d+1,d+2',
        'nominal_climatology_sample_size': NOMINAL_CLIM_SAMPLE_SIZE,
        'climatology_statistic': '90th_percentile_of_daily_Tmax',
        'percentile_reducer': 'ee.Reducer.percentile([90])',
        'bootstrap_applied': False,

        'feb29_in_climatology': False,
        'feb29_analysis_rule': 'retain_day_and_use_Feb28_TX90',
        'calendar_mapping': 'month_day_on_365_day_climatological_calendar',

        'source_dataset': ERA5_ID,
        'source_band': TMAX_BAND,
        'source_temperature_units': 'Kelvin',
        'note_temperature_units': (
            'Percentile comparison is unit-invariant; ERA5-Land Tmax remains Kelvin'
        ),

        'collection': 'MapBiomas Brazil Degradation Collection 11',
        'theme': 'CLIMATIC_WAVES',
        'version': VERSION,
    })

    return event


# ============================================================================
# 9. OUTPUT COLLECTION / ASSET INVENTORY HELPERS
# ============================================================================


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


# ============================================================================
# 10. EXPECTED JOB SPECIFICATIONS
# ============================================================================


def build_job_spec(current_date):
    date_string = format_date(current_date)
    date_name = date_string.replace('-', '_')

    name = f'{FILE_PREFIX}_{date_name}_v{VERSION}'
    asset_id = f'{HEAT_ASSET_ROOT}/{name}'
    description = f'{TASK_PREFIX}_{date_name}_v{VERSION}'

    return {
        'date': current_date,
        'date_string': date_string,
        'year': current_date.year,
        'product': 'heat',
        'event_type': EVENT_NAME,
        'name': name,
        'asset_id': asset_id,
        'description': description,
        'attempts': 0,
        'last_error': '',
    }



def build_all_job_specs():
    start_date = parse_date(START_DATE)
    end_date = parse_date(END_DATE)

    return [
        build_job_spec(current_date)
        for current_date in date_range(start_date, end_date)
    ]


# ============================================================================
# 11. EARTH ENGINE OPERATION HELPERS
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

    Supports both newer and older Earth Engine Python clients.
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
    """Find active operations belonging to this exact workflow/date set."""

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
# 12. MONITOR STATE + TIMING EVIDENCE
# ============================================================================

TIMING_WINDOW = deque(maxlen=TIMING_WINDOW_SIZE)
TIMING_SEEN_OPERATIONS = set()



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
        'state': state,
        'end_epoch': end_time.timestamp(),
        'runtime_seconds': runtime_seconds,
        'lifecycle_seconds': lifecycle_seconds,
    }

    TIMING_WINDOW.append(sample)
    TIMING_SEEN_OPERATIONS.add(operation_name)



def timing_stats():
    """
    Moving-window timing statistics from the latest <=50 concluded tasks.

    ETA rate uses completion throughput measured from server endTime values:
        (n - 1) completions / span(first endTime, last endTime)
    """

    samples = list(TIMING_WINDOW)

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

        # Prevent unstable near-infinite rates when several tasks share the
        # same completion timestamp. At minimum use one monitor interval.
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
# 13. DASHBOARD HELPERS
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



def build_year_summary(jobs, status_by_description):
    grouped = defaultdict(list)

    for job in jobs:
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

        elif active > 0 or complete > 0:
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



def heat_metrics(jobs, status_by_description):
    expected = len(jobs)

    statuses = [
        status_by_description.get(job['description'], 'WAITING')
        for job in jobs
    ]

    complete = sum(status == 'COMPLETE' for status in statuses)
    active = sum(status in ACTIVE_OPERATION_STATES for status in statuses)
    failed = sum(
        status in {'FAILED', 'CANCELLED', 'SUBMIT_FAILED'}
        for status in statuses
    )
    waiting = max(0, expected - complete - active)

    return {
        'expected': expected,
        'complete': complete,
        'active': active,
        'failed': failed,
        'waiting': waiting,
        'year_summary': build_year_summary(jobs, status_by_description),
    }



def compute_eta(jobs, status_by_description):
    remaining = sum(
        1
        for job in jobs
        if status_by_description.get(job['description'], 'WAITING') != 'COMPLETE'
    )

    stats = timing_stats()
    eta_seconds = None

    if (
        stats['n'] >= MIN_TIMING_SAMPLES_FOR_ETA
        and stats['rate_per_min']
        and stats['rate_per_min'] > 0
    ):
        eta_seconds = remaining / stats['rate_per_min'] * 60.0

    return {
        'remaining': remaining,
        'stats': stats,
        'eta': eta_seconds,
    }



def render_html_panel(
    phase,
    all_jobs,
    status_by_description,
    controller_start,
    batch_number=None,
    batch_total=None,
    batch_size=0,
    batch_states=None,
    counters=None,
    note=None,
):
    counters = Counter(counters or {})
    batch_states = Counter(batch_states or {})

    elapsed = time.monotonic() - controller_start
    eta = compute_eta(all_jobs, status_by_description)
    metrics = heat_metrics(all_jobs, status_by_description)
    years = metrics['year_summary']
    stats = eta['stats']

    progress = (
        100.0 * metrics['complete'] / metrics['expected']
        if metrics['expected']
        else 100.0
    )

    if metrics['expected'] > 0 and metrics['complete'] == metrics['expected']:
        color = COLOR_GREEN
        status_label = 'COMPLETED'
    elif metrics['active'] > 0 or metrics['complete'] > 0:
        color = COLOR_ORANGE
        status_label = 'RUNNING'
    else:
        color = COLOR_GRAY
        status_label = 'WAITING'

    running_text = (
        '; '.join(years['running_detail'])
        if years['running_detail']
        else '—'
    )

    rate_text = (
        f"{stats['rate_per_min']:.1f} tasks/min"
        if stats['rate_per_min']
        else 'collecting evidence'
    )

    batch_label = '—'
    if batch_number is not None:
        batch_label = str(batch_number)
        if batch_total is not None:
            batch_label = f'{batch_number}/{batch_total}'

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
        color:{COLOR_TEXT}; background:{COLOR_BG}; border-radius:16px;
        padding:18px; border:1px solid #e5e7eb;
      }}
      .mb-header {{display:flex; justify-content:space-between; gap:12px; align-items:flex-start; margin-bottom:14px;}}
      .mb-title {{font-size:22px; font-weight:800;}}
      .mb-subtitle {{font-size:12px; color:#6b7280; margin-top:4px; line-height:1.4;}}
      .phase {{background:{COLOR_BLUE}; color:white; padding:7px 10px; border-radius:999px; font-size:12px; font-weight:700;}}
      .top-grid {{display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin-bottom:12px;}}
      .top-card {{background:white; border:1px solid #e5e7eb; border-radius:12px; padding:11px 12px;}}
      .top-card b {{font-size:18px; display:block;}}
      .top-card span {{font-size:11px; color:#6b7280; text-transform:uppercase; letter-spacing:.04em;}}
      .product-card {{background:{COLOR_CARD}; border:1px solid #e5e7eb; border-radius:14px; padding:13px; box-shadow:0 1px 2px rgba(0,0,0,.04); border-top:6px solid {color};}}
      .product-title-row {{display:flex; justify-content:space-between; align-items:center; gap:8px;}}
      .product-title {{font-size:18px; font-weight:800;}}
      .badge {{background:{color}; color:white; font-size:10px; font-weight:800; padding:4px 7px; border-radius:999px;}}
      .progress-shell {{height:8px; background:#eceff2; border-radius:999px; overflow:hidden; margin:10px 0 12px;}}
      .progress-fill {{height:100%; border-radius:999px; width:{progress:.2f}%; background:{color};}}
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
      @media (max-width: 900px) {{
        .top-grid {{grid-template-columns:repeat(2,1fr);}}
      }}
    </style>

    <div class="mb-wrap">
      <div class="mb-header">
        <div>
          <div class="mb-title">MapBiomas Brazil · HeatWaves V2 Export Monitor</div>
          <div class="mb-subtitle">
            WSDI/TX90 · 1991–2020 · 5-day centered climatology · Tmax &gt; TX90 · ≥6 consecutive days<br>
            {START_DATE} → {END_DATE} · batch size {BATCH_SIZE:,} · refresh every {MONITOR_REFRESH_SECONDS}s
          </div>
        </div>
        <div class="phase">{html.escape(str(phase))}</div>
      </div>

      <div class="top-grid">
        <div class="top-card"><b>{html.escape(batch_label)}</b><span>batch · size {batch_size:,}</span></div>
        <div class="top-card"><b>{format_duration(eta['eta'])}</b><span>ETA</span></div>
        <div class="top-card"><b>{stats['n']}/{TIMING_WINDOW_SIZE}</b><span>timing samples</span></div>
        <div class="top-card"><b>{format_duration(elapsed)}</b><span>controller elapsed</span></div>
      </div>

      <div class="top-grid">
        <div class="top-card"><b>{html.escape(rate_text)}</b><span>moving completion rate</span></div>
        <div class="top-card"><b>{format_duration(stats.get('median_runtime'))}</b><span>median runtime</span></div>
        <div class="top-card"><b>{counters.get('submitted', 0):,}</b><span>submitted this run</span></div>
        <div class="top-card"><b>{counters.get('skipped_later', 0):,}</b><span>found after re-scan</span></div>
      </div>

      <div class="product-card">
        <div class="product-title-row">
          <div class="product-title">HeatWave V2 · WSDI daily binary</div>
          <div class="badge">{status_label}</div>
        </div>

        <div class="progress-shell"><div class="progress-fill"></div></div>

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
        <div class="evidence-row">Last {stats['n']}/{TIMING_WINDOW_SIZE} concluded tasks · {html.escape(rate_text)}</div>
      </div>

      {state_html}
      {note_html}

      <div class="footer">
        Daily product: 0 = no heat wave; 1 = day belonging to a ≥6-day WSDI warm spell.
        Feb 29 is retained in the analysed series and uses Feb 28 TX90. No bootstrap.
      </div>
    </div>
    """

    clear_output(wait=True)
    display(HTML(html_output))



def render_text_panel(
    phase,
    all_jobs,
    status_by_description,
    controller_start,
    batch_number=None,
    batch_total=None,
    batch_size=0,
    batch_states=None,
    counters=None,
    note=None,
):
    counters = Counter(counters or {})
    batch_states = Counter(batch_states or {})

    elapsed = time.monotonic() - controller_start
    eta = compute_eta(all_jobs, status_by_description)
    metrics = heat_metrics(all_jobs, status_by_description)
    years = metrics['year_summary']
    stats = eta['stats']

    print('\033[2J\033[H', end='')
    print('=' * 100)
    print('MAPBIOMAS BRAZIL - HEATWAVES V2 | WSDI/TX90 | EARTH ENGINE EXPORT MONITOR')
    print('=' * 100)
    print(f'Phase: {phase}')
    print(f'Period: {START_DATE} -> {END_DATE}')
    print('Method: Tmax > TX90(calendar day), 5-day centered climatology, >=6 consecutive days')
    print('Reference: 1991-2020 | 365-day climatology | Feb29 -> Feb28 TX90 | no bootstrap')
    print(f'Elapsed: {format_duration(elapsed)}')
    print(f'ETA: {format_duration(eta["eta"])}')

    if batch_number is not None:
        print(f'Batch: {batch_number}/{batch_total} | size={batch_size:,}')

    print('-' * 100)
    print(
        f"complete={metrics['complete']:,}/{metrics['expected']:,} "
        f"active={metrics['active']:,} waiting={metrics['waiting']:,} "
        f"failed={metrics['failed']:,}"
    )
    print(f"completed years: {compress_years(years['complete_years'])}")
    print(f"running years:   {'; '.join(years['running_detail']) if years['running_detail'] else '-'}")
    print(f"waiting years:   {compress_years(years['waiting_years'])}")
    print(
        f"timing: {stats['n']}/{TIMING_WINDOW_SIZE} samples | "
        f"rate={stats['rate_per_min']:.1f} tasks/min"
        if stats['rate_per_min']
        else f"timing: {stats['n']}/{TIMING_WINDOW_SIZE} samples | collecting evidence"
    )

    if batch_states:
        print('-' * 100)
        print('Batch states:', dict(batch_states))

    if note:
        print('-' * 100)
        print(note)

    print('=' * 100)



def render_panel(**kwargs):
    if _HAS_IPYTHON:
        render_html_panel(**kwargs)
    else:
        render_text_panel(**kwargs)


# ============================================================================
# 14. QUEUE CAPACITY
# ============================================================================


def wait_for_queue_capacity(requested_slots, panel_context):
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
# 15. EXPORT CREATION
# ============================================================================


def create_export_task(job):
    image = create_heat_wave_image(job['date'])

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
# 16. LIVE OPERATION MONITOR
# ============================================================================


def refresh_watched_operations(watched, status_by_description):
    """Refresh watched states with one listOperations() fast path."""

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
# 17. ONE STRICT BATCH
# ============================================================================


def run_strict_batch(
    batch_jobs,
    batch_number,
    batch_total,
    counters,
    all_jobs,
    status_by_description,
    controller_start,
    phase_prefix='HEATWAVE_V2',
):
    """Submit one batch and wait until every task in it is terminal."""

    panel_context = {
        'all_jobs': all_jobs,
        'status_by_description': status_by_description,
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
# 18. RETRY FAILED JOBS
# ============================================================================


def retry_failed_jobs(
    failed_jobs,
    batch_number,
    batch_total,
    counters,
    all_jobs,
    status_by_description,
    controller_start,
):
    retry_queue = list(failed_jobs)
    permanently_failed = []
    retry_round = 0

    while retry_queue:
        # attempts == 1 after initial failure. With MAX_SCRIPT_RETRIES = 2,
        # attempts 1 and 2 are eligible, giving at most 3 total submissions.
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
                controller_start=controller_start,
                phase_prefix=f'RETRY_{retry_round}',
            )

            next_retry_queue.extend(failed_again)

        retry_queue = next_retry_queue

    return permanently_failed


# ============================================================================
# 19. REPORT HELPERS
# ============================================================================


def write_jobs_csv(path, jobs):
    with open(path, 'w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                'date',
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
                'description': job['description'],
                'asset_id': job['asset_id'],
                'attempts': job['attempts'],
                'last_error': job.get('last_error', ''),
            })


# ============================================================================
# 20. STATUS / INVENTORY HELPERS
# ============================================================================


def apply_inventory_to_status(
    jobs,
    inventory,
    status_by_description,
):
    for job in jobs:
        if job['asset_id'] in inventory:
            status_by_description[job['description']] = 'COMPLETE'

        elif status_by_description.get(job['description']) == 'COMPLETE':
            # An asset previously marked complete disappeared between checks.
            status_by_description[job['description']] = 'WAITING'



def missing_jobs(jobs, inventory):
    return [
        job
        for job in jobs
        if job['asset_id'] not in inventory
    ]


# ============================================================================
# 21. CONTROLLER
# ============================================================================


def main():
    controller_start = time.monotonic()

    # ------------------------------------------------------------------------
    # 21.1 Ensure output collection exists.
    # ------------------------------------------------------------------------

    ensure_image_collection(HEAT_ASSET_ROOT)

    # ------------------------------------------------------------------------
    # 21.2 Build all expected daily jobs.
    # ------------------------------------------------------------------------

    all_jobs = build_all_job_specs()

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
    # 21.3 Checker first: scan existing outputs before submitting anything.
    # ------------------------------------------------------------------------

    render_panel(
        phase='CHECKING_EXISTING_ASSETS',
        all_jobs=all_jobs,
        status_by_description=status_by_description,
        controller_start=controller_start,
        counters=counters,
        note=(
            'Scanning heatWaves_v2 before any new task submission.'
        ),
    )

    inventory = list_asset_names(HEAT_ASSET_ROOT)

    apply_inventory_to_status(
        all_jobs,
        inventory,
        status_by_description,
    )

    # ------------------------------------------------------------------------
    # 21.4 Detect active operations from an interrupted/restarted run.
    # ------------------------------------------------------------------------

    preexisting_active = get_preexisting_active_operations(
        job_by_description
    )

    # Ignore active operations whose output asset already exists.
    filtered_preexisting = {}

    for operation_name, record in preexisting_active.items():
        job = record['job']

        if job['asset_id'] in inventory:
            continue

        filtered_preexisting[operation_name] = record
        status_by_description[job['description']] = record['state']

    preexisting_active = filtered_preexisting

    if preexisting_active:
        monitor_watched_operations(
            watched=preexisting_active,
            panel_context={
                'all_jobs': all_jobs,
                'status_by_description': status_by_description,
                'controller_start': controller_start,
                'batch_size': len(preexisting_active),
                'counters': counters,
            },
            phase='WAITING_FOR_PREEXISTING_TASKS',
            status_by_description=status_by_description,
        )

    # Fresh inventory after pre-existing operations finish.
    inventory = list_asset_names(HEAT_ASSET_ROOT)

    apply_inventory_to_status(
        all_jobs,
        inventory,
        status_by_description,
    )

    pending_jobs = missing_jobs(all_jobs, inventory)

    if not pending_jobs:
        render_panel(
            phase='ALREADY_COMPLETE',
            all_jobs=all_jobs,
            status_by_description=status_by_description,
            controller_start=controller_start,
            counters=counters,
            note=(
                f'All expected outputs already exist in {HEAT_ASSET_ROOT}.'
            ),
        )
        return

    # ------------------------------------------------------------------------
    # 21.5 Main batches of 1500.
    # ------------------------------------------------------------------------

    main_batch_number = 0
    permanently_failed_all = []

    while pending_jobs:
        # Re-scan before each new batch. This prevents duplicate submissions if
        # another notebook/process created assets since the previous scan.
        inventory = list_asset_names(HEAT_ASSET_ROOT)

        refreshed_pending = []
        skipped_now = 0

        for job in pending_jobs:
            if job['asset_id'] in inventory:
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

        _, failed_jobs = run_strict_batch(
            batch_jobs=batch_jobs,
            batch_number=main_batch_number,
            batch_total=batch_total_display,
            counters=counters,
            all_jobs=all_jobs,
            status_by_description=status_by_description,
            controller_start=controller_start,
            phase_prefix='HEATWAVE_V2',
        )

        if failed_jobs:
            exhausted = retry_failed_jobs(
                failed_jobs=failed_jobs,
                batch_number=main_batch_number,
                batch_total=batch_total_display,
                counters=counters,
                all_jobs=all_jobs,
                status_by_description=status_by_description,
                controller_start=controller_start,
            )

            permanently_failed_all.extend(exhausted)

    # ------------------------------------------------------------------------
    # 21.6 Final verification.
    # ------------------------------------------------------------------------

    final_inventory = list_asset_names(HEAT_ASSET_ROOT)

    apply_inventory_to_status(
        all_jobs,
        final_inventory,
        status_by_description,
    )

    final_missing_jobs = missing_jobs(
        all_jobs,
        final_inventory,
    )

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
        f'Output: {HEAT_ASSET_ROOT}',
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
        controller_start=controller_start,
        counters=counters,
        note='\n'.join(note_lines),
    )


# ============================================================================
# 22. RUN
# ============================================================================

if __name__ == '__main__':
    main()
