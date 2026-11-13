"""MAPBIOMAS BRAZIL — HEATWAVE AGGREGATION (NATIONAL, THROUGH AUG 2026)

ONLY input: .../CLIMATIC_WAVES/heatWaves_v2
ONLY output: .../CLIMATIC_WAVES/heatWaves_v2_agg
FULL mode, all Brazil:
  - 1985–2025: January–December MONTHLY + ANNUAL rasters.
  - 2026: January–August MONTHLY rasters only (through 2026-08-31 UTC).
  - NO 2026 annual raster; NO September–December 2026 rasters.

Indicators: heatwave_days, event_count, max_event_duration,
mean_event_duration, plus qc_censored_event (1 = event metrics are masked).

Calendar convention:
  * Heatwave days belong to the UTC dates on which they occurred.
  * A WHOLE heatwave event belongs to the UTC date on which it ENDED.
  * Never split an event at a month or year boundary.
  * No completed events: event_count=0; max/mean masked.
  * Unknown incoming event start: mask all event metrics in its end period.
  * A day AFTER the final period is necessary to confirm events ending on
    its final day. For August 2026, Sep 1 is OPTIONAL: when absent, hot
    pixels on Aug 31 receive qc_censored_event=1 for August event metrics;
    the August heatwave-day total is still complete.
  * Full-year annual raw metrics derive from exactly 12 monthly raw metrics.

Execution:
  1. Check the v2 daily date inventory and schema; use the native v2 grid.
  2. One national staging image per year: 60 bands for full years,
     40 bands for Jan–Aug 2026. No geographic tiling.
  3. Export monthly finals for every year and annual finals ONLY through 2025.
  4. Reuse existing valid asset IDs, resume active tasks, retry failures.

FULL is default. PILOT processes one entire-Brazil year in dedicated pilot
assets; set PILOT_YEAR=2026 to test the eight-month partial-year pathway.
The original 1985 empty-lookback safeguard remains in place.
"""

from __future__ import annotations

import csv
import datetime as dt
import math
import time
from collections import Counter
from pathlib import Path

import ee

# ============================ 1. USER SETTINGS ===========================
EE_PROJECT = 'mapbiomas-brazil'
ROOT = 'projects/mapbiomas-brazil/assets/DEGRADATION/COLLECTION-11/CLIMATIC_WAVES'

SOURCE = {'label': 'v2', 'input': ROOT + '/heatWaves_v2',
          'output': ROOT + '/heatWaves_v2_agg',
          'band': 'heat_wave', 'positive_value': 1}

RUN_MODE = 'FULL'       # 'FULL' or 'PILOT'; both use the WHOLE-BRAZIL geometry.
PILOT_YEAR = 2026       # Set RUN_MODE='PILOT' to test the partial 2026 path.
START_YEAR = 1985       # Set START_YEAR=2026 to run only 2026 in FULL mode.
END_YEAR = 2026
PARTIAL_YEAR = 2026
PARTIAL_LAST_MONTH = 8  # 2026 ends at AUGUST 31 (inclusive).

SPEC_VERSION = 1        # Retain historical asset IDs / existing m1 exports.
TASK_PREFIX = 'Dhemerson_Atmosfera_'  # Task description only, NOT output asset names.
FIRST_SOURCE_DATE = dt.date(1985, 1, 1)
LOOKBACK_DAYS = 366     # Incoming event onset: long runs may be QC flagged.
REQUIRE_NEXT_JAN1 = True      # Full years: require their following Jan 1.
REQUIRE_PARTIAL_NEXT_DAY = False  # Sep 1, 2026 optional; mask ambiguous Aug events.

# Output on the v2 input grid (projection and exact pixel origin).
REFERENCE_SOURCE = SOURCE['input']
REFERENCE_BAND = 'heat_wave'

# Time controls / restart safety. Avoid flooding the EE project queue.
MAX_STAGE_CONCURRENT = 1   # One national staging image at a time: high computation cost.
MAX_FINAL_CONCURRENT = 4   # Derived from already materialized staging assets.
MAX_ATTEMPTS = 3          # Includes initial submission; per Colab invocation.
MONITOR_SECONDS = 35
PENDING_SAFETY_CEILING = 2800
WAIT_TIMEOUT_HOURS = 12

# Full-Brazil geometry is used by BOTH FULL and PILOT. No tile coordinates.
# Caution: a single national staging task is more demanding than four tiles.

# This file is an optional record from the active Colab session. Earth Engine
# assets, not this local CSV, are authoritative for restarting a run.
REPORT_PATH = Path('/content/mapbiomas_heatwaves_aggregation_run.csv')

# ============================ 2. AUTHENTICATION ===========================
try:
    ee.Initialize(project=EE_PROJECT)
except Exception:
    ee.Authenticate()
    ee.Initialize(project=EE_PROJECT)

assert RUN_MODE in ('PILOT', 'FULL')
assert START_YEAR <= END_YEAR
assert PARTIAL_YEAR == 2026 and PARTIAL_LAST_MONTH == 8
assert LOOKBACK_DAYS >= 1

BRAZIL = (ee.FeatureCollection('USDOS/LSIB_SIMPLE/2017')
          .filter(ee.Filter.eq('country_na', 'Brazil'))
          .first().geometry())
RUN_YEARS = ([PILOT_YEAR] if RUN_MODE == 'PILOT'
             else list(range(START_YEAR, END_YEAR + 1)))


def is_partial_year(year):
    return year == PARTIAL_YEAR


def months_for_year(year):
    """2026: Jan-Aug; all other years: Jan-Dec."""
    last_month = PARTIAL_LAST_MONTH if is_partial_year(year) else 12
    return list(range(1, last_month + 1))


def last_day_for_year(year):
    """Inclusive last processed UTC date (2026-08-31 for 2026)."""
    last_month = months_for_year(year)[-1]
    return month_boundaries(year, last_month)[1] - dt.timedelta(days=1)


def iso(d):
    return d.isoformat()


def month_boundaries(year, month):
    start = dt.date(year, month, 1)
    end = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    return start, end


def date_range(first, last_inclusive):
    day = first
    while day <= last_inclusive:
        yield day
        day += dt.timedelta(days=1)


def configure_source(source):
    """Prepare the heatWaves_v2 source and staging collection."""
    src = dict(source)
    if RUN_MODE == 'PILOT':
        # Different from the older small-region pilot; never reuse its assets.
        src['output'] += '_NATIONAL_PILOT'
    # Distinct from the former FOUR-TILE staging collections.
    suffix = ('_stage_national_PILOT' if RUN_MODE == 'PILOT'
              else '_stage_national_v' + str(SPEC_VERSION))
    src['staging'] = source['output'] + suffix

    collection = ee.ImageCollection(src['input'])
    year0 = min(RUN_YEARS)
    lookback0 = max(FIRST_SOURCE_DATE,
                    dt.date(year0, 1, 1) - dt.timedelta(days=LOOKBACK_DAYS))
    first_image = ee.Image(collection.filterDate(
        iso(lookback0), iso(lookback0 + dt.timedelta(days=1))).first())
    bands = first_image.bandNames().getInfo()
    if not bands:
        raise RuntimeError(f"No first daily source image: {src['input']} on {lookback0}")
    if src['band'] not in bands:
        raise RuntimeError(f"[{src['label']}] Expected band {src['band']!r}, found {bands}")
    src['raw'] = collection

    # Keep input and output grids aligned to heatWaves_v2.
    return src


def common_projection():
    ref = ee.Image(ee.ImageCollection(REFERENCE_SOURCE).filterDate(
        '1985-01-01', '1985-01-02').first()).select(REFERENCE_BAND)
    info = ref.projection().getInfo()
    crs, transform = info.get('crs'), info.get('transform')
    if not crs or not transform or len(transform) != 6:
        raise RuntimeError(f'Could not determine the v2 input grid: {info}')
    print('COMMON OUTPUT PIXEL GRID: CRS=', crs, ' transform=', transform)
    return {'crs': crs, 'crsTransform': transform}


def check_dates(source):
    """Fail on missing/duplicate required daily images; detect closing sentinel.

    FULL: require 1985-01-01 through 2026-08-31 inclusive (unless START_YEAR
    was moved forward); allow 2026-09-01 to be missing and QC-mask uncertain
    August events. Full years always need a next-day observation to close
    their Dec 31 events when REQUIRE_NEXT_JAN1 is True.

    PILOT: check its lookback, all requested months, and optional next day
    (mandatory when testing a full year and REQUIRE_NEXT_JAN1 is True).
    """
    first = max(FIRST_SOURCE_DATE,
                dt.date(min(RUN_YEARS), 1, 1) - dt.timedelta(days=LOOKBACK_DAYS))
    last_day = last_day_for_year(max(RUN_YEARS))
    sentinel = last_day + dt.timedelta(days=1)
    end_exclusive = sentinel + dt.timedelta(days=1)  # Include sentinel if present.
    milliseconds = (source['raw'].filterDate(iso(first), iso(end_exclusive))
                    .aggregate_array('system:time_start').getInfo())
    if not isinstance(milliseconds, list):
        raise RuntimeError(f"[{source['label']}] Could not read date metadata")

    observed = []
    off_midnight = []
    for value in milliseconds:
        if not isinstance(value, (int, float)):
            raise RuntimeError(f"[{source['label']}] Invalid date metadata {value!r}")
        stamp = dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc)
        if stamp.time() != dt.time(0, 0):
            off_midnight.append(stamp.isoformat())
        observed.append(stamp.date())

    duplicates = [(d, n) for d, n in Counter(observed).items() if n > 1]
    obs_set = set(observed)
    missing = sorted(set(date_range(first, last_day)) - obs_set)
    has_sentinel = sentinel in obs_set
    mandatory_sentinel = (REQUIRE_PARTIAL_NEXT_DAY if is_partial_year(max(RUN_YEARS))
                          else REQUIRE_NEXT_JAN1)
    if off_midnight or duplicates or missing or (mandatory_sentinel and not has_sentinel):
        raise RuntimeError(
            f"[{source['label']}] DATE PREFLIGHT FAILED. "
            f"Required dates {first}..{last_day} UTC. "
            f"Missing ({len(missing)}): {missing[:20]}; "
            f"duplicates ({len(duplicates)}): {duplicates[:10]}; "
            f"off-midnight: {off_midnight[:5]}; "
            f"next-day sentinel {sentinel}: {has_sentinel} "
            f"(mandatory={mandatory_sentinel}). "
            "Missing required days must not be interpreted as heatwave-free days.")

    # For FULL, all dates before the final partial year are contiguous, so
    # January 1 is present for each earlier full year's December 31 closure.
    source['observed_dates'] = obs_set
    print(f"[{source['label']}] PASS: {len(observed)} daily images; "
          f"required range {first}..{last_day}; "
          f"closing-day observation {sentinel}={has_sentinel}")
    if is_partial_year(max(RUN_YEARS)) and not has_sentinel:
        print(f"[{source['label']}] NOTE: 2026-08-31 hot pixels will have "
              "qc_censored_event=1 in August; August day counts remain valid.")
    return has_sentinel


# =========================== 3. RASTER ALGORITHM ===========================
MONTHS = list(range(1, 13))  # Only used for annual (1985–2025) reductions.
RAW_KEYS = ('days', 'events', 'duration_sum', 'max', 'qc')


def binary_collection(source, first, end_exclusive):
    band = source['band']
    positive_value = source['positive_value']

    def select_bin(image):
        image = ee.Image(image)
        return (image.select(band).unmask(0).eq(positive_value).toInt32().rename('hw')
                .copyProperties(image, ['system:time_start']))

    return source['raw'].filterDate(iso(first), iso(end_exclusive)).map(select_bin)


def make_stage_image(source, year, include_next_day):
    """One 60-band full-year OR 40-band Jan–Aug 2026 national staging image.

    The image's scan includes the lookback and requested months. An optional
    observation on the NEXT day identifies completed events ending on the
    last date of the requested interval (e.g., Sep 1 closes Aug 31).
    Event-end records are timestamped on the LAST hot day, not on the first
    non-hot day; annual finals, when applicable, sum these monthly metrics.
    """
    year_start = dt.date(year, 1, 1)
    last_day = last_day_for_year(year)
    period_end_exclusive = last_day + dt.timedelta(days=1)
    context_start = max(FIRST_SOURCE_DATE,
                        year_start - dt.timedelta(days=LOOKBACK_DAYS))
    read_until = (period_end_exclusive + dt.timedelta(days=1)
                  if include_next_day else period_end_exclusive)
    col = binary_collection(source, context_start, read_until)

    # Reference zero inherits the *source* grid inside the temporal scan;
    # national stage/final exports are explicitly aligned to COMMON v2 grid.
    example = ee.Image(col.filterDate(iso(context_start),
                                     iso(context_start + dt.timedelta(days=1))).first())
    zero = example.select('hw').unmask(0).multiply(0).toInt32()
    initial = ee.Dictionary({'prev': zero, 'run': zero, 'seen_zero': zero})

    def update_run(image, state):
        image = ee.Image(image)
        a = ee.Dictionary(state)
        current = image.select('hw')
        run = ee.Image(a.get('run'))
        seen = ee.Image(a.get('seen_zero'))
        return ee.Dictionary({
            'prev': current,
            'run': run.add(1).multiply(current).toInt32(),
            'seen_zero': seen.Or(current.Not()).toInt32(),
        })

    # Establish the incoming run using the available lookback dates.
    # The first source year (1985) has no earlier daily assets:
    # context_start == year_start. Do NOT call Collection.iterate() on
    # this empty date range (Earth Engine Error code 3). Start from an
    # empty state instead. If a run begins on the first available day,
    # seen_zero remains 0 and its end period receives qc_censored_event=1.
    if context_start < year_start:
        warm = col.filterDate(iso(context_start), iso(year_start)).sort('system:time_start')
        warm_state = ee.Dictionary(warm.iterate(update_run, initial))
    else:
        warm_state = initial

    state0 = ee.Dictionary({
        'prev': warm_state.get('prev'),
        'run': warm_state.get('run'),
        'seen_zero': warm_state.get('seen_zero'),
        'records': ee.List([]),
    })

    # A next-day non-hot observation confirms an event ended on the last
    # processed day (Jan 1 after Dec 31, or Sep 1 after Aug 31, 2026).
    # A run still hot on that next day does not count as a completed event.
    target = col.filterDate(iso(year_start), iso(read_until)).sort('system:time_start')

    def record_event_end(image, state):
        image = ee.Image(image)
        a = ee.Dictionary(state)
        today = image.select('hw')
        yesterday = ee.Image(a.get('prev'))
        run = ee.Image(a.get('run'))
        seen_zero = ee.Image(a.get('seen_zero'))
        ended = yesterday.And(today.Not()).toInt32()
        confirmed = ended.And(seen_zero).toInt32()
        left_censored = ended.And(seen_zero.Not()).toInt32()
        event_end_date = ee.Date(image.get('system:time_start')).advance(-1, 'day')

        # Four pixelwise contributions and one QA. A record may contain no
        # event anywhere; those zero records allow regular period reducers.
        record = ee.Image.cat([
            confirmed.rename('events'),
            run.multiply(confirmed).rename('duration_sum'),
            run.multiply(confirmed).rename('max'),
            left_censored.rename('qc'),
        ]).set('system:time_start', event_end_date.millis())

        return ee.Dictionary({
            'prev': today,
            'run': run.add(1).multiply(today).toInt32(),
            'seen_zero': seen_zero.Or(today.Not()).toInt32(),
            'records': ee.List(a.get('records')).add(record),
        })

    final = ee.Dictionary(target.iterate(record_event_end, state0))
    ends = ee.ImageCollection.fromImages(ee.List(final.get('records')))
    # Without the post-period observation, hot pixels on the final day
    # could represent incomplete events: flag ONLY the last processed month.
    right_uncertain = (zero if include_next_day else ee.Image(final.get('prev')))

    processed_months = months_for_year(year)
    raw_bands = []
    for month in processed_months:
        start, stop = month_boundaries(year, month)
        m = f'm{month:02d}'
        source_days = col.filterDate(iso(start), iso(stop)).select('hw')
        days = source_days.sum().unmask(0).toInt32().rename(f'{m}_days')
        monthly_end_records = ends.filterDate(iso(start), iso(stop))
        events = monthly_end_records.select('events').sum().unmask(0).toInt32()
        duration_sum = (monthly_end_records.select('duration_sum').sum()
                        .unmask(0).toInt32())
        maximum = monthly_end_records.select('max').max().unmask(0).toInt32()
        qc = monthly_end_records.select('qc').max().unmask(0).toInt32()
        if month == processed_months[-1]:
            qc = qc.Or(right_uncertain).toInt32()
        raw_bands.extend([
            days,
            events.rename(f'{m}_events'),
            duration_sum.rename(f'{m}_duration_sum'),
            maximum.rename(f'{m}_max'),
            qc.rename(f'{m}_qc'),
        ])

    return ee.Image.cat(raw_bands).clip(BRAZIL).set({
        'system:time_start': ee.Date(iso(year_start)).millis(),
        'year': year,
        'source_collection': source['input'],
        'source_band': source['band'],
        'source_heatwave_value': source['positive_value'],
        'aggregation_spec_version': SPEC_VERSION,
        'aggregation_stage': True,
        'processing_layout': 'single_nationwide_stage',
        'left_lookback_days': LOOKBACK_DAYS,
        'processed_through_utc': iso(last_day),
        'months_in_stage': len(processed_months),
        'partial_year': is_partial_year(year),
        'includes_day_after_processed_period': include_next_day,
        'method': 'day_by_calendar_date_complete_event_by_utc_end_date',
        'run_mode': RUN_MODE,
    })


def stage_id(source, year):
    # Distinct partial-stage ID avoids accidentally reusing an old 60-band
    # 2026 staging raster from the previous script. Historic IDs are unchanged.
    partial = f'_JAN_AUG' if is_partial_year(year) else ''
    return f"{source['staging']}/staging_{year}{partial}_BRAZIL_m{SPEC_VERSION}"


def final_id(source, year, month=None):
    if month is None:
        if is_partial_year(year):
            raise ValueError('No annual 2026 raster: only Jan-Aug monthly exports')
        name = f'annual_{year}_m{SPEC_VERSION}'
    else:
        if month not in months_for_year(year):
            raise ValueError(f'Month {month} is not included in year {year}')
        name = f'monthly_{year}_{month:02d}_m{SPEC_VERSION}'
    return source['output'] + '/' + name


def staged_year_image(source, year):
    """Load the full-Brazil staged image (60 bands, or 40 for 2026)."""
    return ee.Image(stage_id(source, year))


def finalize_raw(raw, year, month, source):
    """Calculate public monthly or (only through 2025) annual indicators."""
    if month is None:
        if is_partial_year(year):
            raise ValueError('Annual output is not defined for partial year 2026')
        days = ee.ImageCollection.fromImages([
            raw.select(f'm{m:02d}_days').rename('value') for m in MONTHS]).sum()
        events = ee.ImageCollection.fromImages([
            raw.select(f'm{m:02d}_events').rename('value') for m in MONTHS]).sum()
        durations = ee.ImageCollection.fromImages([
            raw.select(f'm{m:02d}_duration_sum').rename('value') for m in MONTHS]).sum()
        maximum = ee.ImageCollection.fromImages([
            raw.select(f'm{m:02d}_max').rename('value') for m in MONTHS]).max()
        qc = ee.ImageCollection.fromImages([
            raw.select(f'm{m:02d}_qc').rename('value') for m in MONTHS]).max()
        date0 = dt.date(year, 1, 1)
        date1 = dt.date(year + 1, 1, 1)
        period = 'annual'
    else:
        stem = f'm{month:02d}'
        days = raw.select(stem + '_days')
        events = raw.select(stem + '_events')
        durations = raw.select(stem + '_duration_sum')
        maximum = raw.select(stem + '_max')
        qc = raw.select(stem + '_qc')
        date0, date1 = month_boundaries(year, month)
        period = 'monthly'

    qc = qc.gt(0).unmask(0).toUint8().rename('qc_censored_event')
    good = qc.eq(0)
    has_events = events.gt(0).And(good)
    return ee.Image.cat([
        days.toInt16().rename('heatwave_days'),
        events.toInt16().rename('event_count').updateMask(good),
        maximum.toInt16().rename('max_event_duration').updateMask(has_events),
        durations.divide(events.where(events.eq(0), 1)).toFloat()
                 .rename('mean_event_duration').updateMask(has_events),
        qc,
    ]).clip(BRAZIL).set({
        'system:time_start': ee.Date(iso(date0)).millis(),
        'year': year,
        'month': month if month is not None else 0,
        'period_type': period,
        'period_start_utc': iso(date0),
        'period_end_exclusive_utc': iso(date1),
        'source_data_through_utc': iso(last_day_for_year(year)),
        'partial_year': is_partial_year(year),
        'source_collection': source['input'],
        'source_band': source['band'],
        'source_heatwave_value': source['positive_value'],
        'aggregation_spec_version': SPEC_VERSION,
        'heatwave_days_rule': 'actual_UTC_dates',
        'event_metrics_rule': 'complete_events_assigned_to_UTC_end_date',
        'max_mean_when_no_completed_events': 'masked',
        'event_metrics_when_qc_censored_event_1': 'masked',
        'reference_grid': REFERENCE_SOURCE,
        'processing_layout': 'single_nationwide_stage',
        'run_mode': RUN_MODE,
    })


# ========================== 4. ASSET / TASK HELPERS ========================
def asset_exists(asset_id):
    try:
        return ee.data.getAsset(asset_id) is not None
    except ee.EEException as exc:
        # Do not mistake permissions/network outages for an absent asset.
        message = str(exc).lower()
        if any(x in message for x in ('not found', 'does not exist', '404')):
            return False
        raise


def ensure_collection(asset_id):
    if not asset_exists(asset_id):
        ee.data.createAsset({'type': 'IMAGE_COLLECTION'}, asset_id)
        print('Created ImageCollection:', asset_id)
    info = ee.data.getAsset(asset_id)
    if info.get('type') != 'IMAGE_COLLECTION':
        raise RuntimeError(f'Expected IMAGE_COLLECTION but found {info.get("type")}: {asset_id}')


def list_operations():
    try:
        items = ee.data.listOperations(project=f'projects/{EE_PROJECT}')
    except TypeError:
        items = ee.data.listOperations()
    return items.get('operations', []) if isinstance(items, dict) else items


def operation_state(op):
    return op.get('metadata', {}).get('state', 'UNKNOWN')


def operation_description(op):
    return op.get('metadata', {}).get('description', '')


def active_operations():
    active = {}
    for op in list_operations():
        if operation_state(op) in ('PENDING', 'RUNNING', 'CANCELLING'):
            desc = operation_description(op)
            if desc:
                active.setdefault(desc, op.get('name'))
    return active


def await_operation(op_name, desc, output):
    deadline = time.monotonic() + WAIT_TIMEOUT_HOURS * 3600
    while time.monotonic() < deadline:
        if asset_exists(output):
            return
        op = ee.data.getOperation(op_name)
        status = operation_state(op)
        if status == 'SUCCEEDED':
            for _ in range(8):
                if asset_exists(output):
                    return
                time.sleep(5)
            raise RuntimeError(f'{desc} succeeded but asset not visible: {output}')
        if status in ('FAILED', 'CANCELLED'):
            raise RuntimeError(f'{desc} pre-existing task {status}: {op.get("error")}')
        time.sleep(MONITOR_SECONDS)
    raise TimeoutError(f'{desc}: timeout waiting for pre-existing task; rerun to resume')


def wait_for_pending_queue(slots=1):
    # We don't create thousands of simultaneous tasks or approach quotas.
    while True:
        pending = sum(operation_state(op) == 'PENDING' for op in list_operations())
        if pending + slots <= PENDING_SAFETY_CEILING:
            return
        print(f'Project has {pending} pending tasks; waiting for queue capacity ...')
        time.sleep(MONITOR_SECONDS * 2)


def record(job, status, detail=''):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not REPORT_PATH.exists()
    with REPORT_PATH.open('a', encoding='utf-8', newline='') as fp:
        writer = csv.writer(fp)
        if is_new:
            writer.writerow(['utc_time', 'run_mode', 'source', 'year',
                             'period', 'asset', 'status', 'detail'])
        writer.writerow([dt.datetime.now(dt.timezone.utc).isoformat(), RUN_MODE,
                         job['source'], job['year'], job['period'],
                         job['asset_id'], status, detail])


def run_tasks_in_batches(jobs, limit):
    """Use small strictly monitored batches. Resume any known active tasks."""
    failures = []
    for pos in range(0, len(jobs), limit):
        group = jobs[pos:pos + limit]
        print(f'  Task group {pos // limit + 1}/{math.ceil(len(jobs) / limit)}')
        to_start = []
        existing = active_operations()
        for job in group:
            if asset_exists(job['asset_id']):
                record(job, 'EXISTS')
                continue
            # Also recognize a pre-prefix task from an earlier run, avoiding
            # duplicate exports if a job was still running when names changed.
            original_description = (job['description'][len(TASK_PREFIX):]
                                    if job['description'].startswith(TASK_PREFIX)
                                    else job['description'])
            old_op = (existing.get(job['description'])
                      or existing.get(original_description))
            if old_op:
                print('    Resume:', job['description'])
                try:
                    await_operation(old_op, job['description'], job['asset_id'])
                    record(job, 'RESUMED_SUCCESS')
                except Exception as exc:
                    print('    Previously active task did not finish:', exc)
                    # A truly FAILED prior task may be retried below. A timeout
                    # is NOT a failed task; avoid concurrent duplicate exports.
                    if isinstance(exc, TimeoutError):
                        raise
                    if asset_exists(job['asset_id']):
                        record(job, 'RESUMED_SUCCESS')
                    else:
                        to_start.append(job)
            else:
                to_start.append(job)

        retry = list(to_start)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if not retry:
                break
            wait_for_pending_queue(slots=len(retry))
            watches = []
            submit_failed = []
            for job in retry:
                if asset_exists(job['asset_id']):
                    record(job, 'EXISTS_AFTER_RESCAN')
                    continue
                try:
                    task = job['create']()
                    task.start()
                    print(f'    Submitted: {job["description"]} (attempt {attempt})')
                    record(job, 'SUBMITTED', str(attempt))
                    watches.append((job, task))
                except Exception as exc:
                    print('    Submission error:', job['description'], exc)
                    submit_failed.append((job, str(exc)))

            deadline = time.monotonic() + WAIT_TIMEOUT_HOURS * 3600
            remaining = list(watches)
            while remaining:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Export batch timeout. Tasks continue '
                                       'server-side; rerun to resume safely.')
                next_remaining = []
                for job, task in remaining:
                    status = task.status()
                    state = status.get('state', 'UNKNOWN')
                    if state == 'COMPLETED':
                        if asset_exists(job['asset_id']):
                            record(job, 'COMPLETE', str(attempt))
                        else:
                            # Completion may precede asset inventory visibility.
                            found = False
                            for _ in range(8):
                                time.sleep(5)
                                if asset_exists(job['asset_id']):
                                    found = True
                                    break
                            if found:
                                record(job, 'COMPLETE', str(attempt))
                            else:
                                submit_failed.append((job, 'Completed but output absent'))
                    elif state in ('FAILED', 'CANCELLED'):
                        message = status.get('error_message', state)
                        submit_failed.append((job, str(message)))
                        record(job, state, str(message))
                    else:
                        next_remaining.append((job, task))
                if next_remaining:
                    print('    Waiting:', len(next_remaining), 'active task(s)')
                    time.sleep(MONITOR_SECONDS)
                remaining = next_remaining

            if submit_failed:
                print('    Will retry:', len(submit_failed), 'task(s)')
                if attempt < MAX_ATTEMPTS:
                    time.sleep(20 * attempt)
            retry = [job for job, _ in submit_failed]
            if attempt == MAX_ATTEMPTS:
                for job, error in submit_failed:
                    failures.append((job, error))
                    record(job, 'PERMANENT_FAILURE', error)
        if failures:
            # Never derive public images from a missing/failed national stage.
            raise RuntimeError(f'{len(failures)} export(s) failed. First error: '
                               f'{failures[0][0]["asset_id"]}: {failures[0][1]}')


def export_options():
    return dict(region=BRAZIL,
                crs=GRID['crs'], crsTransform=GRID['crsTransform'],
                maxPixels=1e13)


def stage_jobs(source, year):
    """Exactly one national stage (2026=Jan-Aug only), no mosaics/tiles."""
    next_day = last_day_for_year(year) + dt.timedelta(days=1)
    sentinel = next_day in source['observed_dates']
    if not sentinel and (REQUIRE_PARTIAL_NEXT_DAY if is_partial_year(year)
                         else REQUIRE_NEXT_JAN1):
        raise RuntimeError(
            f"[{source['label']}] Required {next_day} image to close {year} events")
    aid = stage_id(source, year)
    period_tag = 'JAN_AUG_' if is_partial_year(year) else ''
    desc = (f'{TASK_PREFIX}MBHW_S{SPEC_VERSION}_'
            f'{source["label"]}_{year}_{period_tag}BRAZIL_{RUN_MODE}')

    def make_task(aid=aid, desc=desc, year=year,
                  source=source, sentinel=sentinel):
        image = make_stage_image(source, year, sentinel)
        return ee.batch.Export.image.toAsset(
            image=image, description=desc, assetId=aid,
            region=BRAZIL, crs=GRID['crs'], crsTransform=GRID['crsTransform'],
            maxPixels=1e13,
            pyramidingPolicy={'.default': 'mean',
                              **{f'm{m:02d}_qc': 'max'
                                 for m in months_for_year(year)}},
        )

    return [{'source': source['label'], 'year': year,
             'period': 'stage_BRAZIL', 'asset_id': aid,
             'description': desc, 'create': make_task}]


def final_jobs(source, year):
    jobs = []
    # Absolutely no 2026 annual (None) or Sep-Dec monthly tasks.
    periods = months_for_year(year)
    if not is_partial_year(year):
        periods = [*periods, None]
    for month in periods:
        aid = final_id(source, year, month)
        period = f'month_{month:02d}' if month else 'annual'
        desc = f'{TASK_PREFIX}MBHW_F{SPEC_VERSION}_{source["label"]}_{year}_{period}_{RUN_MODE}'

        def make_task(aid=aid, desc=desc, month=month, year=year, source=source):
            national_stage = staged_year_image(source, year)
            image = finalize_raw(national_stage, year, month, source)
            return ee.batch.Export.image.toAsset(
                image=image, description=desc, assetId=aid,
                pyramidingPolicy={
                    'heatwave_days': 'mean', 'event_count': 'mean',
                    'max_event_duration': 'mean', 'mean_event_duration': 'mean',
                    'qc_censored_event': 'max',
                }, **export_options())

        jobs.append({'source': source['label'], 'year': year, 'period': period,
                     'asset_id': aid, 'description': desc, 'create': make_task})
    return jobs


# =========================== 5. CONTROLLED RUN ============================
def main():
    print('=' * 74)
    print('MapBiomas heatwave aggregates', RUN_MODE, 'spec v' + str(SPEC_VERSION))
    print('Years:', RUN_YEARS[0], '..', RUN_YEARS[-1],
          '| 1985-2025: monthly + annual; 2026: Jan-Aug monthly ONLY')
    print('Partial 2026 data end: 2026-08-31 inclusive (UTC)')
    print('Only source: heatWaves_v2 (original heatWaves is NOT accessed)')
    print('Input data are never overwritten; all outputs have versioned names.')
    print('=' * 74)

    # Check ONLY the v2 input collection before exporting anything.
    src = configure_source(SOURCE)
    check_dates(src)

    global GRID
    GRID = common_projection()
    count_expected = sum(len(months_for_year(y)) +
                         (0 if is_partial_year(y) else 1) for y in RUN_YEARS)
    print('Expected FINAL rasters:', count_expected,
          '| reusable NATIONAL stages:', len(RUN_YEARS))
    print('PILOT (if chosen) ALSO covers entire Brazil; it uses separate assets.')

    ensure_collection(src['staging'])
    ensure_collection(src['output'])
    for iy, year in enumerate(RUN_YEARS, 1):
        print(f'[{src["label"]}] YEAR {year} ({iy}/{len(RUN_YEARS)})')
        finals = final_jobs(src, year)
        if all(asset_exists(j['asset_id']) for j in finals):
            print(f'  All {len(finals)} final images already exist; skipping year.')
            continue
        stages = stage_jobs(src, year)
        print('  A. Materializing ONE national staging image ...')
        run_tasks_in_batches(stages, MAX_STAGE_CONCURRENT)
        if not asset_exists(stages[0]['asset_id']):
            raise RuntimeError('Cannot export final images: national stage is absent')
        print(f'  B. Exporting {len(finals)} final images (ALL BRAZIL) ...')
        run_tasks_in_batches(finals, MAX_FINAL_CONCURRENT)
        print(f'  DONE {src["label"]} {year}')
    print('SOURCE FINISHED:', src['label'], src['output'])

    print('\nFINISHED: all requested final assets verified as existing.')
    print('Run log:', REPORT_PATH)
    print('Do not delete staging collections until you have checked final maps.')


if __name__ == '__main__':
    main()
