#!/usr/bin/env python3
"""Generate an AstroBin long-exposure acquisition CSV from a Seestar lights folder.

AstroBin "Import acquisitions from CSV" expects one row per acquisition session
(per night). This tool scans the raw lights, groups them into observing nights,
and emits the documented AstroBin long-exposure columns.

Session grouping
----------------
Seestar encodes the **local** capture time in the filename (`_YYYYMMDD-HHMMSS.fit`),
while the FITS `DATE-OBS` header is **UTC**. A night that crosses local midnight must
stay in one row, so we take the local timestamp, subtract a night-shift (default 12 h),
and group by the resulting date (= the date the night began). The filename is the
primary source; if it does not match the Seestar pattern we fall back to `DATE-OBS`
plus `--utc-offset-hours`.

Columns (https://welcome.astrobin.com/importing-acquisitions-from-csv):
    date,filter,number,duration,iso,binning,gain,sensorCooling,fNumber,
    darks,flats,flatDarks,bias,bortle,meanSqm,meanFwhm,temperature
Only `number` and `duration` are mandatory. `filter` is an AstroBin numeric filter ID,
auto-detected per sub from the filename token (`_LP_` / `_IRCUT_`; header FILTER as
fallback) and mapped to the S30 Pro integrated filters' AstroBin IDs. Sessions are
grouped by (night, filter, exposure), so a night mixing filters or sub lengths
yields one honest row per combination instead of a modal-duration mash.
Override with --filter-id N (forces one ID on every row) or --filter-id 0 (blank).
Seestar calibrates on-device, so darks/flats/bias are left blank rather than
asserting 0.

Usage:
    astrobin_session_csv.py <lights-dir> [--out FILE] [--night-shift-hours 12]
        [--utc-offset-hours 0] [--filter-id N] [--bortle N] [--sqm X]
        [--fwhm X] [--sensor-temp]
"""
import argparse
import csv
import glob
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from astropy.io import fits

COLUMNS = [
    "date", "filter", "number", "duration", "iso", "binning", "gain",
    "sensorCooling", "fNumber", "darks", "flats", "flatDarks", "bias",
    "bortle", "meanSqm", "meanFwhm", "temperature",
]

# AstroBin equipment-DB IDs for the S30 Pro's integrated (switchable) filters:
# LP    https://app.astrobin.com/equipment/explorer/filter/40954/zwo-seestar-s30-pro-integrated-lp-filter
# IRCUT https://app.astrobin.com/equipment/explorer/filter/42307 (Integrated UV/IR Cut Filter)
# Both are built into the scope, so these are constants for every Seestar session.
SEESTAR_FILTER_IDS = {"LP": 40954, "IRCUT": 42307}

# Seestar local timestamp in the filename, e.g. Light_C 103_30.0s_LP_20260617-205453.fit
_FNAME_TS = re.compile(r"_(\d{8})-(\d{6})\.fit$", re.IGNORECASE)

# Filter token between exposure and timestamp, e.g. _30.0s_IRCUT_20260627-060233.fit
_FNAME_FILTER = re.compile(r"\ds_([A-Za-z0-9]+)_\d{8}-\d{6}\.fit$", re.IGNORECASE)


def sub_filter(path, header):
    """Filter name for a sub: filename token first, header FILTER as fallback."""
    m = _FNAME_FILTER.search(os.path.basename(path))
    if m:
        return m.group(1).upper()
    filt = header.get("FILTER")
    return str(filt).strip().upper() if filt else ""


# Exposure token before the filter, e.g. _10.0s_IRCUT_20260710-220525.fit
_FNAME_EXP = re.compile(r"_(\d+(?:\.\d+)?)s_[A-Za-z0-9]+_\d{8}-\d{6}\.fit$", re.IGNORECASE)


def sub_exptime(path, header):
    """Exposure seconds for a sub: header EXPTIME/EXPOSURE first, filename as fallback."""
    exp = header.get("EXPTIME") or header.get("EXPOSURE")
    if exp is not None:
        return float(exp)
    m = _FNAME_EXP.search(os.path.basename(path))
    return float(m.group(1)) if m else None


def resolve_lights_dir(path):
    """Accept the lights/ folder itself or a workdir containing lights/."""
    if os.path.isdir(os.path.join(path, "lights")):
        return os.path.join(path, "lights")
    return path


def local_time(path, header, utc_offset_hours):
    """Local capture time: from the Seestar filename, else DATE-OBS + offset."""
    m = _FNAME_TS.search(os.path.basename(path))
    if m:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    dobs = header.get("DATE-OBS")
    if not dobs:
        return None
    try:
        dt = datetime.fromisoformat(str(dobs).replace("Z", ""))
    except ValueError:
        return None
    return dt + timedelta(hours=utc_offset_hours)


def _mode(values):
    """Most common non-None value, or None."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return Counter(vals).most_common(1)[0][0]


def _num(x):
    """Format a number without trailing .0 noise; pass through None as ''."""
    if x is None:
        return ""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def collect_sessions(lights_dir, night_shift_hours, utc_offset_hours):
    files = sorted(glob.glob(os.path.join(lights_dir, "*.fit")))
    if not files:
        files = sorted(glob.glob(os.path.join(lights_dir, "*.fits")))
    sessions = defaultdict(lambda: {
        "n": 0, "exptime": [], "gain": [], "binning": [], "ccdtemp": [],
    })
    skipped = 0
    for f in files:
        try:
            h = fits.getheader(f)
        except Exception:
            skipped += 1
            continue
        lt = local_time(f, h, utc_offset_hours)
        if lt is None:
            skipped += 1
            continue
        night = (lt - timedelta(hours=night_shift_hours)).date().isoformat()
        s = sessions[(night, sub_filter(f, h), sub_exptime(f, h))]
        s["n"] += 1
        s["exptime"].append(h.get("EXPTIME") or h.get("EXPOSURE"))
        s["gain"].append(h.get("GAIN"))
        s["binning"].append(h.get("XBINNING"))
        s["ccdtemp"].append(h.get("CCD-TEMP"))
    return sessions, len(files), skipped


def filter_cell(filt, args):
    """AstroBin filter ID for a row: --filter-id override, else auto by name."""
    if args.filter_id is not None:
        return str(args.filter_id) if args.filter_id else ""
    fid = SEESTAR_FILTER_IDS.get(filt)
    return str(fid) if fid else ""


def build_rows(sessions, args):
    rows = []
    for night, filt, exp in sorted(sessions,
                                   key=lambda k: (k[0], k[1], k[2] is None, k[2] or 0)):
        s = sessions[(night, filt, exp)]
        ccd = [t for t in s["ccdtemp"] if t is not None]
        sensor_cooling = ""
        if args.sensor_temp and ccd:
            sensor_cooling = str(round(sum(ccd) / len(ccd)))
        rows.append({
            "date": night,
            "filter": filter_cell(filt, args),
            "number": str(s["n"]),
            "duration": _num(exp if exp is not None else _mode(s["exptime"])),
            "iso": "",
            "binning": _num(_mode(s["binning"]) or 1),
            "gain": _num(_mode(s["gain"])),
            "sensorCooling": sensor_cooling,
            "fNumber": "",
            "darks": "", "flats": "", "flatDarks": "", "bias": "",
            "bortle": _num(args.bortle),
            "meanSqm": _num(args.sqm),
            "meanFwhm": _num(args.fwhm),
            "temperature": "",
        })
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lights_dir", help="lights/ folder (or a dir containing it)")
    ap.add_argument("--out", help="write CSV here (also printed to stdout)")
    ap.add_argument("--night-shift-hours", type=float, default=12.0,
                    help="hours to subtract from local time before taking the night date (default 12)")
    ap.add_argument("--utc-offset-hours", type=float, default=0.0,
                    help="offset added to DATE-OBS when filename has no local timestamp")
    ap.add_argument("--filter-id", type=int, default=None,
                    help="force one AstroBin filter ID on every row (default: auto by "
                         f"the sub's filter — LP {SEESTAR_FILTER_IDS['LP']}, "
                         f"IRCUT {SEESTAR_FILTER_IDS['IRCUT']}; pass 0 to leave blank)")
    ap.add_argument("--bortle", type=int, default=None)
    ap.add_argument("--sqm", type=float, default=None)
    ap.add_argument("--fwhm", type=float, default=None)
    ap.add_argument("--sensor-temp", action="store_true",
                    help="fill sensorCooling from mean CCD-TEMP (Seestar is uncooled; off by default)")
    args = ap.parse_args(argv)

    lights_dir = resolve_lights_dir(args.lights_dir)
    if not os.path.isdir(lights_dir):
        ap.error(f"not a directory: {lights_dir}")

    sessions, total, skipped = collect_sessions(
        lights_dir, args.night_shift_hours, args.utc_offset_hours)
    if not sessions:
        ap.error(f"no usable .fit lights found in {lights_dir}")

    rows = build_rows(sessions, args)

    def write(fh):
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    write(sys.stdout)
    if args.out:
        with open(args.out, "w", newline="") as fh:
            write(fh)

    total_subs = sum(int(r["number"]) for r in rows)
    total_h = sum(int(r["number"]) * float(r["duration"] or 0) for r in rows) / 3600.0
    print(f"\n[astrobin-csv] {len(rows)} session(s), {total_subs} subs, "
          f"{total_h:.2f} h total" + (f"  (skipped {skipped})" if skipped else ""),
          file=sys.stderr)
    if args.filter_id is not None:
        note = (f"filter ID forced to {args.filter_id}" if args.filter_id
                else "'filter' left blank (--filter-id 0)")
        print(f"[astrobin-csv] {note}.", file=sys.stderr)
    else:
        filters = sorted({filt for _, filt, _ in sessions})
        print(f"[astrobin-csv] filter auto-detect: "
              + ", ".join(f"{f or '(none)'} -> {SEESTAR_FILTER_IDS.get(f, 'blank')}"
                          for f in filters), file=sys.stderr)
        unknown = [f for f in filters if f and f not in SEESTAR_FILTER_IDS]
        if unknown:
            print(f"[astrobin-csv] NOTE: unknown filter(s) {unknown} left blank — "
                  "set them with --filter-id or on AstroBin after import.",
                  file=sys.stderr)
    if args.out:
        print(f"[astrobin-csv] written: {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
