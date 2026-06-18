#!/usr/bin/env python3
"""Restore a FITS header that a processing tool stripped (notably GraXpert, which
keeps only NAXIS). Copies all metadata from a header-bearing SOURCE onto TARGET while
keeping TARGET's own structural keywords (so the data layout stays correct).

This preserves the keywords astro software needs downstream: OBJECT, DATE-OBS, EXPTIME,
INSTRUME, TELESCOP, FOCALLEN, XPIXSZ/YPIXSZ, RA/DEC, FILTER, GAIN, plus any WCS — so
plate solving and colour calibration (SPCC/PCC) have their hints.

Usage:
  restore_fits_header.py SOURCE.fits TARGET.fits [OUT.fits]
    SOURCE  = a FITS with the full header (e.g. the Siril stack fed into GraXpert).
    TARGET  = the header-stripped output to fix.
    OUT     = optional; default overwrites TARGET in place.

Needs: astropy.
"""
import sys
from astropy.io import fits

# Structural / data-layout keywords: keep TARGET's, never copy from SOURCE
# (BSCALE/BZERO especially — SOURCE may be uint16-scaled, TARGET is float).
STRUCTURAL = {
    "SIMPLE", "XTENSION", "BITPIX", "EXTEND", "PCOUNT", "GCOUNT",
    "NAXIS", "NAXIS1", "NAXIS2", "NAXIS3", "NAXIS4",
    "BSCALE", "BZERO", "BLANK", "END", "",
}


def restore(source, target, out=None):
    src = fits.getheader(source, ext=0, ignore_missing_simple=True)
    with fits.open(target, ignore_missing_simple=True) as hdul:
        data = hdul[0].data
        hdr = hdul[0].header.copy()           # correct structure for the actual data

    copied = 0
    for card in src.cards:
        k = card.keyword
        if k in STRUCTURAL:
            continue
        if k == "COMMENT":
            hdr.add_comment(card.value); copied += 1; continue
        if k == "HISTORY":
            hdr.add_history(card.value); copied += 1; continue
        try:
            hdr[k] = (card.value, card.comment)
            copied += 1
        except Exception:
            pass
    hdr.add_history(f"Header restored from {source.rsplit('/', 1)[-1]}")
    fits.writeto(out or target, data, hdr, overwrite=True)
    return copied


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    src, tgt = sys.argv[1], sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else None
    n = restore(src, tgt, out)
    print(f"restored {n} header cards from {src.rsplit('/',1)[-1]} -> {(out or tgt).rsplit('/',1)[-1]}")
