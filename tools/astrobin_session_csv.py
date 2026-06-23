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
Only `number` and `duration` are mandatory. `filter` is an AstroBin numeric filter ID;
it defaults to the Seestar's fixed integrated LP filter (override with --filter-id, or
--filter-id 0 to leave it blank). Seestar calibrates on-device, so darks/flats/bias are
left blank rather than asserting 0.

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

# AstroBin equipment-DB ID for the Seestar's fixed integrated LP filter
# (https://app.astrobin.com/equipment/explorer/filter/40954/zwo-seestar-s30-pro-integrated-lp-filter).
# The filter is built into the scope, so this is a constant for every Seestar session.
SEESTAR_LP_FILTER_ID = 40954

# Seestar local timestamp in the filename, e.g. Light_C 103_30.0s_LP_20260617-205453.fit
_FNAME_TS = re.compile(r"_(\d{8})-(\d{6})\.fit$", re.IGNORECASE)


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
        s = sessions[night]
        s["n"] += 1
        s["exptime"].append(h.get("EXPTIME") or h.get("EXPOSURE"))
        s["gain"].append(h.get("GAIN"))
        s["binning"].append(h.get("XBINNING"))
        s["ccdtemp"].append(h.get("CCD-TEMP"))
    return sessions, len(files), skipped


def build_rows(sessions, args):
    rows = []
    for night in sorted(sessions):
        s = sessions[night]
        ccd = [t for t in s["ccdtemp"] if t is not None]
        sensor_cooling = ""
        if args.sensor_temp and ccd:
            sensor_cooling = str(round(sum(ccd) / len(ccd)))
        rows.append({
            "date": night,
            "filter": str(args.filter_id) if args.filter_id else "",
            "number": str(s["n"]),
            "duration": _num(_mode(s["exptime"])),
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
    ap.add_argument("--filter-id", type=int, default=SEESTAR_LP_FILTER_ID,
                    help=f"AstroBin numeric filter ID from the filter's equipment URL "
                         f"(default {SEESTAR_LP_FILTER_ID}, the Seestar integrated LP filter; "
                         f"pass 0 to leave blank)")
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
    if args.filter_id:
        print(f"[astrobin-csv] filter ID {args.filter_id} "
              f"(Seestar integrated LP filter; override with --filter-id).",
              file=sys.stderr)
    else:
        print("[astrobin-csv] NOTE: 'filter' left blank (--filter-id 0).",
              file=sys.stderr)
    if args.out:
        print(f"[astrobin-csv] written: {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
