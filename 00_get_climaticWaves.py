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
import csv
import math
import time
from collections import Counter
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

START_DATE = '1985-01-01'
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
# 9. BATCH / MONITOR PARAMETERS
# ============================================================================

# Strict submission batches requested by the workflow.
BATCH_SIZE = 1500

# Refresh the terminal / Colab panel every 15 seconds.
MONITOR_REFRESH_SECONDS = 15

# Earth Engine currently allows at most 3000 READY tasks in a project queue.
# This script keeps a small safety margin before submitting a new batch.
PROJECT_READY_LIMIT = 3000
QUEUE_SAFETY_MARGIN = 50

# Earth Engine already retries some transient failures internally.
# These are additional script-level retries after a task reaches a terminal
# FAILED or CANCELLED state.
MAX_SCRIPT_RETRIES = 2

# BASIC asset listing allows large pages and is much faster than checking
# every expected asset individually.
ASSET_LIST_PAGE_SIZE = 10000

# Optional local reports written by the controller.
MISSING_REPORT_CSV = 'climatic_waves_missing_after_run.csv'
FAILED_REPORT_CSV = 'climatic_waves_failed_tasks.csv'


# ============================================================================
# 10. MONITOR / DISPLAY HELPERS
# ============================================================================

try:
    from IPython.display import clear_output as _ipython_clear_output
except Exception:
    _ipython_clear_output = None


def clear_monitor_screen():
    """Clear a Colab/Jupyter output cell or a normal terminal."""

    if _ipython_clear_output is not None:
        _ipython_clear_output(wait=True)
    else:
        print('\033[2J\033[H', end='')


def utc_now_string():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def format_duration(seconds):
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    if days:
        return f'{days}d {hours:02d}:{minutes:02d}:{seconds:02d}'

    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def render_panel(
    phase,
    total_expected,
    initial_existing,
    pending_total,
    batch_number=None,
    batch_total=None,
    batch_size=0,
    states=None,
    submitted_this_run=0,
    completed_this_run=0,
    failed_this_run=0,
    skipped_since_start=0,
    active_preexisting=0,
    start_monotonic=None,
    note=None,
):
    """Print the live monitor panel."""

    states = Counter(states or {})
    elapsed = (
        time.monotonic() - start_monotonic
        if start_monotonic is not None
        else 0
    )

    clear_monitor_screen()

    print('=' * 78)
    print('MAPBIOMAS BRAZIL - CLIMATIC WAVES | EARTH ENGINE EXPORT MONITOR')
    print('=' * 78)
    print(f'Time:                 {utc_now_string()}')
    print(f'Phase:                {phase}')
    print(f'Period:               {START_DATE} -> {END_DATE} (inclusive)')
    print(f'Refresh:              every {MONITOR_REFRESH_SECONDS}s')
    print(f'Elapsed:              {format_duration(elapsed)}')
    print('-' * 78)
    print(f'Expected outputs:     {total_expected:,}')
    print(f'Existing at checker:  {initial_existing:,}')
    print(f'Pending after checks: {pending_total:,}')
    print(f'Pre-existing active:  {active_preexisting:,}')
    print(f'Newly skipped later:  {skipped_since_start:,}')
    print('-' * 78)

    if batch_number is not None:
        if batch_total is None:
            batch_label = str(batch_number)
        else:
            batch_label = f'{batch_number}/{batch_total}'

        print(f'Batch:                {batch_label}')
        print(f'Batch size:           {batch_size:,}')

    print(f'Submitted this run:   {submitted_this_run:,}')
    print(f'Completed this run:   {completed_this_run:,}')
    print(f'Failed attempts:      {failed_this_run:,}')

    if states:
        print('-' * 78)
        print('Current batch / watched-operation states:')

        order = [
            'PENDING',
            'RUNNING',
            'CANCELLING',
            'SUCCEEDED',
            'FAILED',
            'CANCELLED',
            'SUBMIT_FAILED',
            'UNKNOWN',
        ]

        for state in order:
            if states.get(state, 0):
                print(f'  {state:<14} {states[state]:>7,}')

        other_states = [
            key for key in states.keys()
            if key not in order
        ]

        for state in sorted(other_states):
            print(f'  {state:<14} {states[state]:>7,}')

    if note:
        print('-' * 78)
        print(note)

    print('=' * 78)


# ============================================================================
# 11. OUTPUT COLLECTION / ASSET INVENTORY HELPERS
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
            asset_id
        )


def list_asset_names(parent):
    """
    Return all immediate child asset resource names under an ImageCollection.

    Uses paginated BASIC listings instead of one getAsset() call per day.
    """

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


def inventory_existing_outputs():
    """Read both output ImageCollections and return one set of asset names."""

    existing = set()

    if EXPORT_HEAT:
        existing.update(list_asset_names(HEAT_ASSET_ROOT))

    if EXPORT_COLD:
        existing.update(list_asset_names(COLD_ASSET_ROOT))

    return existing


# ============================================================================
# 12. EXPECTED JOB SPECIFICATIONS
# ============================================================================


def build_job_spec(current_date, event_type):
    """Build a lightweight expected-output specification for one date/type."""

    date_string = format_date(current_date)
    date_name = date_string.replace('-', '_')

    if event_type == 'heat':
        name = f'heat_wave_{date_name}_v{VERSION}'
        asset_id = f'{HEAT_ASSET_ROOT}/{name}'
        description = f'HW_{date_name}_v{VERSION}'

    elif event_type == 'cold':
        name = f'cold_wave_{date_name}_v{VERSION}'
        asset_id = f'{COLD_ASSET_ROOT}/{name}'
        description = f'CW_{date_name}_v{VERSION}'

    else:
        raise ValueError("event_type must be 'heat' or 'cold'")

    return {
        'date': current_date,
        'date_string': date_string,
        'event_type': event_type,
        'name': name,
        'asset_id': asset_id,
        'description': description,
        'attempts': 0,
        'last_error': '',
    }


def build_all_job_specs():
    """Build expected heat/cold job specifications for the complete period."""

    start_date = parse_date(START_DATE)
    end_date = parse_date(END_DATE)
    jobs = []

    for current_date in date_range(start_date, end_date):
        if EXPORT_HEAT:
            jobs.append(build_job_spec(current_date, 'heat'))

        if EXPORT_COLD:
            jobs.append(build_job_spec(current_date, 'cold'))

    return jobs


# ============================================================================
# 13. EARTH ENGINE OPERATION HELPERS
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
    """List Earth Engine operations visible to this initialized project/user."""

    return ee.data.listOperations()


def get_preexisting_active_operations(job_by_description):
    """
    Find already-submitted active operations matching this workflow.

    This prevents duplicate exports when the notebook/script is restarted while
    an earlier batch is still queued or running.
    """

    active = {}

    for operation in list_operations():
        state = operation_state(operation)
        description = operation_description(operation)

        if (
            state in ACTIVE_OPERATION_STATES
            and description in job_by_description
        ):
            active[operation['name']] = {
                'job': job_by_description[description],
                'state': state,
            }

    return active


def current_pending_operation_count(operations=None):
    """Count all currently PENDING operations returned by Earth Engine."""

    if operations is None:
        operations = list_operations()

    return sum(
        1
        for operation in operations
        if operation_state(operation) == 'PENDING'
    )


def wait_for_queue_capacity(
    requested_slots,
    monitor_context,
    start_monotonic,
):
    """
    Wait until submitting requested_slots would stay below the READY/PENDING
    project queue limit with a safety margin.
    """

    while True:
        operations = list_operations()
        pending = current_pending_operation_count(operations)
        allowed = PROJECT_READY_LIMIT - QUEUE_SAFETY_MARGIN

        if pending + requested_slots <= allowed:
            return

        note = (
            'WAITING FOR EARTH ENGINE QUEUE CAPACITY\n'
            f'Current PENDING operations: {pending:,}\n'
            f'Requested new slots:        {requested_slots:,}\n'
            f'Safety ceiling:             {allowed:,}'
        )

        render_panel(
            phase='WAITING_FOR_QUEUE_CAPACITY',
            start_monotonic=start_monotonic,
            note=note,
            **monitor_context,
        )

        time.sleep(MONITOR_REFRESH_SECONDS)


# ============================================================================
# 14. EXPORT CREATION
# ============================================================================


def create_export_task(job):
    """Create, but do not start, one Earth Engine export task."""

    image = create_event_image(
        job['date'],
        job['event_type']
    )

    return ee.batch.Export.image.toAsset(
        image=image,
        description=job['description'],
        assetId=job['asset_id'],
        region=brazil,
        scale=EXPORT_SCALE,
        maxPixels=MAX_PIXELS,
        pyramidingPolicy={
            '.default': 'mode'
        }
    )


def submit_job(job):
    """Create and start one job; return its operation record descriptor."""

    task = create_export_task(job)
    task.start()

    job['attempts'] += 1

    return {
        'operation_name': task.operation_name,
        'task_id': task.id,
        'job': job,
        'state': 'PENDING',
        'error': '',
    }


# ============================================================================
# 15. LIVE OPERATION MONITOR
# ============================================================================


def refresh_watched_operations(watched):
    """
    Refresh states for operation names in watched.

    listOperations() is used as the normal fast path. If an active operation is
    unexpectedly absent from that listing, getOperation() is used as a targeted
    fallback so a vanished/recent operation cannot leave the monitor hanging.
    """

    operations = list_operations()
    listed = {
        operation.get('name'): operation
        for operation in operations
        if operation.get('name')
    }

    for operation_name, record in watched.items():
        if record['state'] in TERMINAL_OPERATION_STATES:
            continue

        operation = listed.get(operation_name)

        if operation is None:
            try:
                operation = ee.data.getOperation(operation_name)
            except Exception as exc:
                record['state'] = record.get('state', 'UNKNOWN')
                record['error'] = str(exc)
                continue

        state = operation_state(operation)
        record['state'] = state

        if state == 'FAILED':
            error = operation.get('error', {})
            record['error'] = error.get('message', str(error))

        elif state == 'CANCELLED':
            record['error'] = 'Earth Engine operation was cancelled.'

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
    monitor_context,
    phase,
    start_monotonic,
):
    """Refresh and display a watched operation set until all are terminal."""

    while True:
        refresh_watched_operations(watched)
        states = watched_state_counts(watched)

        render_panel(
            phase=phase,
            states=states,
            start_monotonic=start_monotonic,
            **monitor_context,
        )

        if all_watched_terminal(watched):
            return watched

        time.sleep(MONITOR_REFRESH_SECONDS)


# ============================================================================
# 16. ONE STRICT BATCH
# ============================================================================


def run_strict_batch(
    batch_jobs,
    batch_number,
    batch_total,
    counters,
    base_context,
    start_monotonic,
    phase_prefix='RUNNING',
):
    """
    Submit one batch, then wait for EVERY operation in that batch to reach a
    terminal state before returning.
    """

    wait_for_queue_capacity(
        requested_slots=len(batch_jobs),
        monitor_context={
            **base_context,
            'batch_number': batch_number,
            'batch_total': batch_total,
            'batch_size': len(batch_jobs),
            'submitted_this_run': counters['submitted'],
            'completed_this_run': counters['completed'],
            'failed_this_run': counters['failed'],
            'skipped_since_start': counters['skipped_later'],
        },
        start_monotonic=start_monotonic,
    )

    watched = {}
    submit_failures = []
    last_panel = 0

    for index, job in enumerate(batch_jobs, start=1):
        try:
            record = submit_job(job)
            watched[record['operation_name']] = record
            counters['submitted'] += 1

        except Exception as exc:
            job['attempts'] += 1
            job['last_error'] = str(exc)
            submit_failures.append(job)
            counters['failed'] += 1

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
                total_expected=base_context['total_expected'],
                initial_existing=base_context['initial_existing'],
                pending_total=base_context['pending_total'],
                active_preexisting=base_context['active_preexisting'],
                batch_number=batch_number,
                batch_total=batch_total,
                batch_size=len(batch_jobs),
                states=submit_states,
                submitted_this_run=counters['submitted'],
                completed_this_run=counters['completed'],
                failed_this_run=counters['failed'],
                skipped_since_start=counters['skipped_later'],
                start_monotonic=start_monotonic,
                note=f'Submission progress: {index:,}/{len(batch_jobs):,}',
            )

            last_panel = now

    if watched:
        monitor_context = {
            **base_context,
            'batch_number': batch_number,
            'batch_total': batch_total,
            'batch_size': len(batch_jobs),
            'submitted_this_run': counters['submitted'],
            'completed_this_run': counters['completed'],
            'failed_this_run': counters['failed'],
            'skipped_since_start': counters['skipped_later'],
        }

        monitor_watched_operations(
            watched=watched,
            monitor_context=monitor_context,
            phase=f'{phase_prefix}_BATCH',
            start_monotonic=start_monotonic,
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
# 17. RETRY FAILED JOBS BEFORE ADVANCING TO NEXT MAIN BATCH
# ============================================================================


def retry_failed_jobs(
    failed_jobs,
    batch_number,
    batch_total,
    counters,
    base_context,
    start_monotonic,
):
    """Retry terminal failures before the controller advances to the next batch."""

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

        # Never exceed the requested 1500-task submission size on retries.
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
                base_context=base_context,
                start_monotonic=start_monotonic,
                phase_prefix=f'RETRY_{retry_round}',
            )

            next_retry_queue.extend(failed_again)

        retry_queue = next_retry_queue

    return permanently_failed


# ============================================================================
# 18. REPORT HELPERS
# ============================================================================


def write_jobs_csv(path, jobs):
    """Write a compact report of jobs for diagnostics/resume review."""

    with open(path, 'w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                'date',
                'event_type',
                'description',
                'asset_id',
                'attempts',
                'last_error',
            ]
        )

        writer.writeheader()

        for job in jobs:
            writer.writerow({
                'date': job['date_string'],
                'event_type': job['event_type'],
                'description': job['description'],
                'asset_id': job['asset_id'],
                'attempts': job['attempts'],
                'last_error': job.get('last_error', ''),
            })


# ============================================================================
# 19. CONTROLLER
# ============================================================================


def main():
    controller_start = time.monotonic()

    # ------------------------------------------------------------------------
    # 19.1 Ensure output collections exist.
    # ------------------------------------------------------------------------

    if EXPORT_HEAT:
        ensure_image_collection(HEAT_ASSET_ROOT)

    if EXPORT_COLD:
        ensure_image_collection(COLD_ASSET_ROOT)

    # ------------------------------------------------------------------------
    # 19.2 Build all expected names first.
    # ------------------------------------------------------------------------

    all_jobs = build_all_job_specs()
    total_expected = len(all_jobs)
    job_by_description = {
        job['description']: job
        for job in all_jobs
    }

    # ------------------------------------------------------------------------
    # 19.3 CHECKER FIRST: inventory completed outputs already in collections.
    # ------------------------------------------------------------------------

    render_panel(
        phase='CHECKING_EXISTING_ASSETS',
        total_expected=total_expected,
        initial_existing=0,
        pending_total=total_expected,
        start_monotonic=controller_start,
        note='Scanning heatWaves and coldWaves before any new submission...',
    )

    existing_assets = inventory_existing_outputs()

    initially_existing_jobs = [
        job
        for job in all_jobs
        if job['asset_id'] in existing_assets
    ]

    initial_existing = len(initially_existing_jobs)

    pending_jobs = [
        job
        for job in all_jobs
        if job['asset_id'] not in existing_assets
    ]

    # ------------------------------------------------------------------------
    # 19.4 Detect a previously submitted batch from an interrupted/restarted run.
    # Do not submit anything new until those matching active jobs finish.
    # ------------------------------------------------------------------------

    preexisting_active = get_preexisting_active_operations(
        job_by_description
    )

    pending_descriptions = {
        job['description']
        for job in pending_jobs
    }

    preexisting_active = {
        name: record
        for name, record in preexisting_active.items()
        if record['job']['description'] in pending_descriptions
    }

    counters = Counter({
        'submitted': 0,
        'completed': 0,
        'failed': 0,
        'skipped_later': 0,
    })

    base_context = {
        'total_expected': total_expected,
        'initial_existing': initial_existing,
        'pending_total': len(pending_jobs),
        'active_preexisting': len(preexisting_active),
    }

    if preexisting_active:
        monitor_watched_operations(
            watched=preexisting_active,
            monitor_context={
                **base_context,
                'batch_size': len(preexisting_active),
                'submitted_this_run': counters['submitted'],
                'completed_this_run': counters['completed'],
                'failed_this_run': counters['failed'],
                'skipped_since_start': counters['skipped_later'],
            },
            phase='WAITING_FOR_PREEXISTING_BATCH',
            start_monotonic=controller_start,
        )

        # Re-run the asset checker after those operations finish. Any failed
        # pre-existing operations will remain missing and become eligible below.
        existing_assets = inventory_existing_outputs()

        pending_jobs = [
            job
            for job in all_jobs
            if job['asset_id'] not in existing_assets
        ]

        base_context['pending_total'] = len(pending_jobs)
        base_context['active_preexisting'] = 0

    # ------------------------------------------------------------------------
    # 19.5 Nothing missing: stop cleanly.
    # ------------------------------------------------------------------------

    if not pending_jobs:
        render_panel(
            phase='COMPLETE_NOTHING_TO_EXPORT',
            total_expected=total_expected,
            initial_existing=initial_existing,
            pending_total=0,
            submitted_this_run=0,
            completed_this_run=0,
            failed_this_run=0,
            skipped_since_start=0,
            start_monotonic=controller_start,
            note='All expected heat/cold assets already exist.',
        )
        return

    # ------------------------------------------------------------------------
    # 19.6 Main strict 1500-task batching.
    # Before every batch, refresh the collection inventory and drop anything
    # that appeared since the initial checker.
    # ------------------------------------------------------------------------

    main_batch_number = 0
    permanently_failed = []

    while pending_jobs:
        # Refresh output inventory. This handles assets created by another run
        # or collaborator after this controller started.
        existing_assets = inventory_existing_outputs()

        refreshed_pending = [
            job
            for job in pending_jobs
            if job['asset_id'] not in existing_assets
        ]

        skipped_now = len(pending_jobs) - len(refreshed_pending)
        counters['skipped_later'] += skipped_now
        pending_jobs = refreshed_pending

        if not pending_jobs:
            break

        main_batch_number += 1

        # Dynamic total estimate based on what remains at this point.
        batches_remaining = math.ceil(len(pending_jobs) / BATCH_SIZE)
        batch_total_display = main_batch_number + batches_remaining - 1

        batch_jobs = pending_jobs[:BATCH_SIZE]
        pending_jobs = pending_jobs[BATCH_SIZE:]

        base_context['pending_total'] = (
            len(batch_jobs)
            + len(pending_jobs)
        )

        _, failed_jobs = run_strict_batch(
            batch_jobs=batch_jobs,
            batch_number=main_batch_number,
            batch_total=batch_total_display,
            counters=counters,
            base_context=base_context,
            start_monotonic=controller_start,
            phase_prefix='RUNNING',
        )

        if failed_jobs:
            exhausted = retry_failed_jobs(
                failed_jobs=failed_jobs,
                batch_number=main_batch_number,
                batch_total=batch_total_display,
                counters=counters,
                base_context=base_context,
                start_monotonic=controller_start,
            )

            permanently_failed.extend(exhausted)

    # ------------------------------------------------------------------------
    # 19.7 FINAL CHECKER: verify expected assets, regardless of task status.
    # ------------------------------------------------------------------------

    render_panel(
        phase='FINAL_ASSET_VERIFICATION',
        total_expected=total_expected,
        initial_existing=initial_existing,
        pending_total=0,
        submitted_this_run=counters['submitted'],
        completed_this_run=counters['completed'],
        failed_this_run=counters['failed'],
        skipped_since_start=counters['skipped_later'],
        start_monotonic=controller_start,
        note='Re-scanning both output collections for exact expected names...',
    )

    final_existing_assets = inventory_existing_outputs()

    final_missing_jobs = [
        job
        for job in all_jobs
        if job['asset_id'] not in final_existing_assets
    ]

    # Keep the best available error diagnostics for anything still absent.
    failure_by_asset = {
        job['asset_id']: job
        for job in permanently_failed
    }

    for job in final_missing_jobs:
        failed_record = failure_by_asset.get(job['asset_id'])
        if failed_record:
            job['last_error'] = failed_record.get('last_error', '')
            job['attempts'] = max(
                job.get('attempts', 0),
                failed_record.get('attempts', 0),
            )

    if final_missing_jobs:
        write_jobs_csv(MISSING_REPORT_CSV, final_missing_jobs)

    if permanently_failed:
        write_jobs_csv(FAILED_REPORT_CSV, permanently_failed)

    final_existing_expected = total_expected - len(final_missing_jobs)

    note_lines = [
        f'Expected assets present: {final_existing_expected:,}/{total_expected:,}',
        f'Assets still missing:    {len(final_missing_jobs):,}',
    ]

    if final_missing_jobs:
        note_lines.append(
            f'Missing report:          {MISSING_REPORT_CSV}'
        )

    if permanently_failed:
        note_lines.append(
            f'Failure report:          {FAILED_REPORT_CSV}'
        )

    render_panel(
        phase=(
            'COMPLETE'
            if not final_missing_jobs
            else 'COMPLETE_WITH_MISSING_OUTPUTS'
        ),
        total_expected=total_expected,
        initial_existing=initial_existing,
        pending_total=len(final_missing_jobs),
        submitted_this_run=counters['submitted'],
        completed_this_run=counters['completed'],
        failed_this_run=counters['failed'],
        skipped_since_start=counters['skipped_later'],
        start_monotonic=controller_start,
        note='\n'.join(note_lines),
    )


# ============================================================================
# 20. RUN
# ============================================================================

if __name__ == '__main__':
    main()
