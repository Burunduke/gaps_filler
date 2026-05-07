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
    """Given path to a .bil/.bip/.bsq, find sibling .hdr/.times/.lcf by stem."""
    bil = Path(bil_path).resolve()
    
    # Check if the .bil file exists
    if not bil.exists():
        raise FileNotFoundError(f"File not found: {bil}")
    
    # Check if the .hdr file exists
    hdr = bil.with_suffix('.hdr')
    if not hdr.exists():
        raise FileNotFoundError(f"Required header file not found: {hdr}")
    
    # Check for optional .times file
    times_path = bil.with_suffix('.times')
    times = times_path if times_path.exists() else None
    
    # Check for optional .lcf file
    lcf_path = bil.with_suffix('.lcf')
    lcf = lcf_path if lcf_path.exists() else None
    
    # Extract name from the stem of the .bil file
    name = bil.stem
    
    return FlightLineMeta(name=name, bil=bil, hdr=hdr, times=times, lcf=lcf)