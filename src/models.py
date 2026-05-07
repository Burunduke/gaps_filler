"""
Passive data containers for PIKA-L flight line files.

This module provides dataclasses that bundle paths to the files describing
a PIKA-L flight line. These are passive containers — they do NOT perform
any I/O operations. File reading logic lives in separate modules like envi_io.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


@dataclass(frozen=True)
class FlightLineMeta:
    """Paths to the four files describing one PIKA-L flight line.

    Passive container — does NOT read files. I/O lives elsewhere
    (see envi_io.read_envi_header, airborne_georef in P3).
    """
    name: str                 # short identifier, e.g. file stem of the .bil
    bil: Path                 # raw cube (.bil / .bip / .bsq accepted; key is the binary)
    hdr: Path                 # ENVI header
    times: Optional[Path]     # per-line timestamps (1 column, len == hdr['lines'])
    lcf: Optional[Path]       # per-line nav (lat/lon/alt + roll/pitch/yaw + time)


# PIKA-L typical band count
DEFAULT_PIKA_L_BANDS = 280


def discover_flight_line(bil_path: Union[str, Path]) -> FlightLineMeta:
    """Given path to a .bil/.bip/.bsq, find sibling .hdr/.times/.lcf files.
    
    Supports both naming conventions:
    - HDR: <bil_path>.hdr (e.g. foo.bil.hdr), with fallback to <basename>.hdr
    - TIMES: <bil_path>.times (e.g. foo.bil.times), with fallback to <basename>.times
    - LCF: <basename>.lcf (e.g. foo.lcf), with fallback to <bil_path>.lcf
    """
    bil = Path(bil_path).resolve()
    
    # Check if the .bil file exists
    if not bil.exists():
        raise FileNotFoundError(f"File not found: {bil}")
    
    # Check for .hdr file - try <bil_path>.hdr first, then <basename>.hdr
    hdr_primary = bil.with_name(bil.name + '.hdr')  # foo.bil.hdr
    hdr_fallback = bil.with_suffix('.hdr')          # foo.hdr
    hdr = hdr_primary if hdr_primary.exists() else hdr_fallback
    if not hdr.exists():
        raise FileNotFoundError(f"Required header file not found: {hdr_primary} or {hdr_fallback}")
    
    # Check for .times file - try <bil_path>.times first, then <basename>.times
    times_primary = bil.with_name(bil.name + '.times')  # foo.bil.times
    times_fallback = bil.with_suffix('.times')          # foo.times
    times = times_primary if times_primary.exists() else times_fallback
    if not times.exists():
        times = None
    
    # Check for .lcf file - try <basename>.lcf first, then <bil_path>.lcf
    lcf_fallback = bil.with_name(bil.name + '.lcf')  # foo.bil.lcf
    lcf_primary = bil.with_suffix('.lcf')            # foo.lcf
    lcf = lcf_primary if lcf_primary.exists() else lcf_fallback
    if not lcf.exists():
        lcf = None
    
    # Extract name from the stem of the .bil file
    name = bil.stem
    
    return FlightLineMeta(name=name, bil=bil, hdr=hdr, times=times, lcf=lcf)