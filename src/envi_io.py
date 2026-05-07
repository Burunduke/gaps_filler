"""
Thin wrapper around spectral.io.envi.read_envi_header to extract ENVI .hdr metadata
for hyperspectral cubes (PIKA-L .bil + .hdr).

This module provides a simplified interface to read ENVI header files with proper
type conversion and error handling. The spectral package is an optional dependency
and is only imported when needed.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union


def _convert_list(value, converter):
    """Convert a value to a list with the given converter function."""
    if value is None:
        return None
    if isinstance(value, str):
        # Handle comma-separated strings
        value = [v.strip() for v in value.split(',')]
    try:
        return [converter(v) for v in value]
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class EnviHeader:
    path: str                 # absolute path to .hdr
    samples: int              # ENVI 'samples' (= image width / cross-track pixels)
    lines: int                # ENVI 'lines' (= image height / along-track frames)
    bands: int
    interleave: str           # 'bil' | 'bip' | 'bsq', lower-case
    data_type: int            # ENVI numeric code (1..15)
    byte_order: int           # 0 little / 1 big
    wavelengths: Optional[List[float]]     # None if absent
    fwhm: Optional[List[float]]
    bbl: Optional[List[int]]               # bad band list as ints (0/1)
    band_names: Optional[List[str]]
    wavelength_units: Optional[str]        # e.g. 'nm', 'micrometers'
    ground_elevation: Optional[float] = None  # ground elevation in meters, None if absent


def read_envi_header(hdr_path: Union[str, Path]) -> EnviHeader:
    """
    Read ENVI header file and extract metadata.
    
    Args:
        hdr_path: Path to .hdr file or to .bil/.bip/.bsq file (will replace extension with .hdr)
        
    Returns:
        EnviHeader: Parsed header information
        
    Raises:
        FileNotFoundError: If the resolved .hdr file doesn't exist
        ValueError: If required keys are missing from the header
        ImportError: If the spectral package is not installed
    """
    # Convert to Path object
    hdr_path = Path(hdr_path).resolve()
    
    # If not a .hdr file, replace extension with .hdr
    if hdr_path.suffix.lower() not in ['.hdr']:
        hdr_path = hdr_path.with_suffix('.hdr')
    
    # Check if file exists
    if not hdr_path.exists():
        raise FileNotFoundError(f"ENVI header file not found: {hdr_path}")
    
    # Lazy import of spectral to keep it as an optional dependency
    try:
        from spectral.io.envi import read_envi_header as _read_envi_header
    except ImportError:
        raise ImportError(
            "The 'spectral' package is required to read ENVI .hdr metadata. "
            "Install with: pip install spectral"
        )
    
    # Read the raw header dictionary
    raw_header = _read_envi_header(str(hdr_path))
    
    # Extract required keys with safe .get() and type coercion
    required_keys = ['samples', 'lines', 'bands', 'interleave', 'data type']
    for key in required_keys:
        if key not in raw_header:
            raise ValueError(f"Required key '{key}' missing from ENVI header")
    
    # Extract and convert required fields
    samples = int(raw_header['samples'])
    lines = int(raw_header['lines'])
    bands = int(raw_header['bands'])
    interleave = str(raw_header['interleave']).lower()
    data_type = int(raw_header['data type'])
    byte_order = int(raw_header.get('byte order', 0))  # Default to 0 if absent
    
    # Extract optional fields with proper type conversion
    wavelengths = _convert_list(raw_header.get('wavelength'), float)
    fwhm = _convert_list(raw_header.get('fwhm'), float)
    bbl = _convert_list(raw_header.get('bbl'), int)
    band_names = _convert_list(raw_header.get('band names'), str)
    wavelength_units = raw_header.get('wavelength units')
    if wavelength_units is not None:
        wavelength_units = str(wavelength_units)
    
    # Extract ground elevation
    ground_elevation = None
    if 'ground elevation' in raw_header:
        try:
            ground_elevation = float(raw_header['ground elevation'])
        except (ValueError, TypeError):
            pass  # Keep as None if conversion fails
    
    # Create and return the EnviHeader object
    return EnviHeader(
        path=str(hdr_path),
        samples=samples,
        lines=lines,
        bands=bands,
        interleave=interleave,
        data_type=data_type,
        byte_order=byte_order,
        wavelengths=wavelengths,
        fwhm=fwhm,
        bbl=bbl,
        band_names=band_names,
        wavelength_units=wavelength_units,
        ground_elevation=ground_elevation
    )