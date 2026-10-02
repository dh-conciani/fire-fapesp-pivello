"""MAPBIOMAS BRAZIL — HEATWAVE MONTHLY + ANNUAL EXPORTS (NATIONAL V1.2)

Input 1: .../CLIMATIC_WAVES/heatWaves_v2  -> .../heatWaves_v2_agg
Input 2: .../CLIMATIC_WAVES/heatWaves     -> .../heatWaves_agg
Years: 1985–2025, all 12 months plus annual, all Brazil in FULL mode.

Four indicators: heatwave_days, event_count, max_event_duration,
mean_event_duration. Plus qc_censored_event (1 = do NOT interpret event metrics).

MapBiomas calendar convention:
  * Allocate heatwave days to the UTC dates on which they occurred.
  * Allocate each WHOLE event to the UTC date on which it ENDED.
  * Never split an event at a month or year boundary.
  * No completed events: event_count=0, max/mean masked.
  * Unknown incoming event start: mask all event metrics in its end period.
  * The day AFTER period end is needed to close an event on its final day.
  * Yearly results reconcile exactly with the 12 monthly source metrics.

Computational design:
  1. Check source date inventories, source band, and common target grid.
  2. For each source and year, export ONE 60-band whole-Brazil staging image
     (12 months x five raw metrics) using ONE event scan; there are no tiles.
  3. Read the national staging image to export 12 monthly + one annual
     five-band final products, avoiding 13 independent event-history scans.
  4. Skip existing assets; resume/wait for active exports; retry failures.

FULL is selected by default to match the requested 1985-2025 exports.
Strongly consider changing to PILOT first: PILOT processes the entire Brazil
geometry for 2023 and writes to dedicated *_agg_NATIONAL_PILOT collections.

This script includes a fix for empty 1985 lookback windows. The 2023 national
pilot was run by the user; full historical exports still require validation.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import time
from collections import Counter
from pathlib import Path

import ee

# ============================ 1. USER SETTINGS ===========================
EE_PROJECT = 'mapbiomas-brazil'
ROOT = 'projects/mapbiomas-brazil/assets/DEGRADATION/COLLECTION-11/CLIMATIC_WAVES'

SOURCES = [  # Process in exactly this order, not simultaneously.
    {'label': 'v2', 'input': ROOT + '/heatWaves_v2',
     'output': ROOT + '/heatWaves_v2_agg', 'band': 'heat_wave', 'positive_value': 1},
    {'label': 'v1', 'input': ROOT + '/heatWaves',
     'output': ROOT + '/heatWaves_agg', 'band': None, 'positive_value': 1},
]

RUN_MODE = 'FULL'         # 'FULL' (1985-2025) or 'PILOT' (one year); BOTH cover ALL BRAZIL.
PILOT_YEAR = 2023        # Use RUN_MODE='PILOT' to try one NATIONAL year first.
START_YEAR = 1985
END_YEAR = 2025

SPEC_VERSION = 1         # Increment if calculations/definitions change.
TASK_PREFIX = 'Dhemerson_Atmosfera_'  # Earth Engine task description ONLY; never asset/file names.
FIRST_SOURCE_DATE = dt.date(1985, 1, 1)
LOOKBACK_DAYS = 366      # Earlier run onset must be observable or QC flags it.
REQUIRE_NEXT_JAN1 = True    # In FULL, requires Jan 1, 2026 to close Dec 2025.
                              # In PILOT, requires Jan 1 after PILOT_YEAR.

# COMMON GRID: export both versions on the projection and exact pixel origin
# of v2's first daily binary image. No invented fine-resolution pixels.
REFERENCE_SOURCE = SOURCES[0]['input']
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
assert LOOKBACK_DAYS >= 1

BRAZIL = (ee.FeatureCollection('USDOS/LSIB_SIMPLE/2017')
          .filter(ee.Filter.eq('country_na', 'Brazil'))
          .first().geometry())
RUN_YEARS = ([PILOT_YEAR] if RUN_MODE == 'PILOT'
             else list(range(START_YEAR, END_YEAR + 1)))


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
    """Source schema may differ; never silently select an arbitrary V1 band."""
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
    if src['band'] is None:
        if 'heat_wave' in bands:
            src['band'] = 'heat_wave'
        elif len(bands) == 1:
            src['band'] = bands[0]
        else:
            raise RuntimeError(
                f"V1 has multiple bands {bands}. Set SOURCES[1]['band'] explicitly.")
        print(f"[{src['label']}] Automatically selected band: {src['band']!r}")
    if src['band'] not in bands:
        raise RuntimeError(f"[{src['label']}] Expected band {src['band']!r}, found {bands}")
    src['raw'] = collection

    # The output grid is identical for both sources, even if their native
    # input pixel grids differ. Old binary source uses nearest-neighbor sampling.
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
    """Fail closed if required daily images are absent or duplicated.

    For FULL, check all 1985-2025 dates, plus 2026-01-01 for closing 2025.
    For PILOT, check exactly the requested year, 366-day lookback and one
    post-year day. No missing day may be interpreted as a zero.
    """
    first = max(FIRST_SOURCE_DATE,
                dt.date(min(RUN_YEARS), 1, 1) - dt.timedelta(days=LOOKBACK_DAYS))
    last_day = dt.date(max(RUN_YEARS), 12, 31)
    sentinel = last_day + dt.timedelta(days=1)
    end_exclusive = sentinel + dt.timedelta(days=1)
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
    if off_midnight or duplicates or missing or (REQUIRE_NEXT_JAN1 and not has_sentinel):
        raise RuntimeError(
            f"[{source['label']}] DATE PREFLIGHT FAILED. "
            f"Missing ({len(missing)}): {missing[:20]}; "
            f"duplicates ({len(duplicates)}): {duplicates[:10]}; "
            f"off-midnight: {off_midnight[:5]}; "
            f"last-day sentinel {sentinel}: {has_sentinel}. "
            "Repair the source or set REQUIRE_NEXT_JAN1=False only if "
            "you accept QC-masked final-period event metrics.")
    print(f"[{source['label']}] PASS: {len(observed)} daily assets; "
          f"{first}..{last_day}; sentinel {sentinel}={has_sentinel}")
    source['final_sentinel'] = has_sentinel
    return has_sentinel


# =========================== 3. RASTER ALGORITHM ===========================
MONTHS = list(range(1, 13))
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
    """Return 60 *raw* monthly bands for one year over all Brazil.

    A single stateful scan tracks runs over the lookback and year. Event-end
    contribution images are timestamped on the event's last 1-day (yesterday,
    when the current day's 0 proves the preceding run has finished). Annual
    final products are calculated by combining these exact monthly bands.
    """
    year_start = dt.date(year, 1, 1)
    next_year = dt.date(year + 1, 1, 1)
    context_start = max(FIRST_SOURCE_DATE,
                        year_start - dt.timedelta(days=LOOKBACK_DAYS))
    read_until = next_year + dt.timedelta(days=1) if include_next_day else next_year
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

    # Jan 1 NEXT year closes an event ending Dec 31 of THIS year. An event
    # still active Jan 1 does NOT end in December and is NOT counted then.
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
    # With no 2026 Jan 1 asset, a 2025 Dec 31 hot day could be an unconfirmed
    # December event end. Mask ONLY Dec and annual event metrics at those pixels.
    right_uncertain = (zero if include_next_day else ee.Image(final.get('prev')))

    raw_bands = []
    for month in MONTHS:
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
        if month == 12:
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
        'includes_next_year_jan_01': include_next_day,
        'method': 'day_by_calendar_date_complete_event_by_utc_end_date',
        'run_mode': RUN_MODE,
    })


def stage_id(source, year):
    return f"{source['staging']}/staging_{year}_BRAZIL_m{SPEC_VERSION}"


def final_id(source, year, month=None):
    if month is None:
        name = f'annual_{year}_m{SPEC_VERSION}'
    else:
        name = f'monthly_{year}_{month:02d}_m{SPEC_VERSION}'
    return source['output'] + '/' + name


def staged_year_image(source, year):
    """Load the one materialized, full-Brazil 60-band annual staging image."""
    return ee.Image(stage_id(source, year))


def finalize_raw(raw, year, month, source):
    """Calculate public monthly or annual 4 indicators plus 1 QA band."""
    if month is None:
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
    """Exactly ONE national staging task per source/year; no mosaics/tiles."""
    sentinel = True if year < max(RUN_YEARS) or source['final_sentinel'] else False
    aid = stage_id(source, year)
    desc = (f'{TASK_PREFIX}MBHW_S{SPEC_VERSION}_'
            f'{source["label"]}_{year}_BRAZIL_{RUN_MODE}')

    def make_task(aid=aid, desc=desc, year=year,
                  source=source, sentinel=sentinel):
        image = make_stage_image(source, year, sentinel)
        return ee.batch.Export.image.toAsset(
            image=image, description=desc, assetId=aid,
            region=BRAZIL, crs=GRID['crs'], crsTransform=GRID['crsTransform'],
            maxPixels=1e13,
            pyramidingPolicy={'.default': 'mean',
                              **{f'm{m:02d}_qc': 'max' for m in MONTHS}},
        )

    return [{'source': source['label'], 'year': year,
             'period': 'stage_BRAZIL', 'asset_id': aid,
             'description': desc, 'create': make_task}]


def final_jobs(source, year):
    jobs = []
    for month in [*MONTHS, None]:
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
          '| monthly + annual | ONE NATIONAL stage per year (NO PARTITIONS)')
    print('2 sources: v2 FIRST, then original heatWaves')
    print('Input data are never overwritten; all outputs have versioned names.')
    print('=' * 74)

    # Check BOTH input sources before exporting ANYTHING.
    sources = [configure_source(s) for s in SOURCES]
    for s in sources:
        check_dates(s)

    global GRID
    GRID = common_projection()
    count_expected = len(RUN_YEARS) * (12 + 1) * len(sources)
    print('Expected FINAL rasters:', count_expected,
          '| reusable NATIONAL stages:', len(RUN_YEARS) * len(sources))
    print('PILOT (if chosen) ALSO covers entire Brazil; it uses separate assets.')

    for src in sources:  # sequential: V2 fully first, then V1
        ensure_collection(src['staging'])
        ensure_collection(src['output'])
        print('\n' + '=' * 30, src['label'], '=' * 30)
        for iy, year in enumerate(RUN_YEARS, 1):
            print(f'[{src["label"]}] YEAR {year} ({iy}/{len(RUN_YEARS)})')
            finals = final_jobs(src, year)
            if all(asset_exists(j['asset_id']) for j in finals):
                print('  All 13 final images already exist; skipping this year.')
                continue
            stages = stage_jobs(src, year)
            print('  A. Materializing ONE national staging image ...')
            run_tasks_in_batches(stages, MAX_STAGE_CONCURRENT)
            if not asset_exists(stages[0]['asset_id']):
                raise RuntimeError('Cannot export final images: national stage is absent')
            print('  B. Exporting 12 monthly + 1 annual image (ALL BRAZIL) ...')
            run_tasks_in_batches(finals, MAX_FINAL_CONCURRENT)
            print(f'  DONE {src["label"]} {year}')
        print('SOURCE FINISHED:', src['label'], src['output'])

    print('\nFINISHED: all requested final assets verified as existing.')
    print('Run log:', REPORT_PATH)
    print('Do not delete staging collections until you have checked final maps.')


if __name__ == '__main__':
    main()
