"""
Module for parsing PIKA-L airborne navigation data and interpolating per-frame poses.

This module handles parsing of Resonon .lcf (navigation log) and .times (per-frame timestamps) files,
and interpolates pose data (position and orientation) for each frame using relative time alignment.
The .lcf and .times files may have different absolute time epochs but should cover the same
flight segment duration, allowing alignment by relative time within each file.

The implementation assumes:
- .lcf file has 11 whitespace-separated columns with navigation data
- .times file has a single column of timestamps, length matching ENVI header 'lines'
- Both files cover the same flight segment (standard Resonon convention)
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from models import FlightLineMeta, discover_flight_line
from envi_io import read_envi_header

# Import for ENU transformations
try:
    from pymap3d import enu2geodetic
except ImportError:
    # Fallback if pymap3d is not available
    def enu2geodetic(e, n, u, lat0, lon0, h0, ell=None, deg=True):
        raise ImportError("pymap3d is required for georeferencing. Install with: pip install pymap3d")


@dataclass(frozen=True)
class LcfTable:
    """Raw rows from a .lcf file (Resonon LCF nav log)."""
    time: np.ndarray   # shape (N,), seconds, raw absolute
    roll: np.ndarray   # radians
    pitch: np.ndarray
    yaw: np.ndarray
    lon: np.ndarray    # degrees
    lat: np.ndarray
    alt: np.ndarray    # meters


@dataclass(frozen=True)
class FrameTimes:
    """Per-frame timestamps from a .times file."""
    time: np.ndarray   # shape (M,) where M == hdr['lines']


@dataclass(frozen=True)
class FramePoses:
    """Pose interpolated per along-track frame (M frames)."""
    lon: np.ndarray    # degrees
    lat: np.ndarray
    alt: np.ndarray    # meters
    roll: np.ndarray   # radians
    pitch: np.ndarray
    yaw: np.ndarray


@dataclass(frozen=True)
class PushbroomSensor:
    """Simple cross-track-only sensor model for PIKA-L-like line scanners."""
    samples: int              # cross-track pixels, from ENVI header samples
    fov_deg: float            # lens FOV, user parameter (different lenses exist)
    principal_sample: float | None = None  # default center: (samples - 1) / 2
    flip_samples: bool = False             # flips left/right if orientation is reversed


@dataclass(frozen=True)
class GroundGrid:
    """Per raw pixel ground coordinates from flat-earth ray intersection."""
    lon: np.ndarray       # shape (lines, samples), degrees
    lat: np.ndarray       # shape (lines, samples), degrees
    alt: np.ndarray       # shape (lines, samples), meters; constant ground_alt
    valid: np.ndarray     # shape (lines, samples), bool; false where ray doesn't hit ground


def read_lcf(lcf_path: Path | str) -> LcfTable:
    """Parse a Resonon .lcf nav file (whitespace-separated, 11 columns).
    Skips blank/comment lines. Validates column count >= 7.
    """
    # Load data, skipping blank/comment lines
    data = np.loadtxt(lcf_path, dtype=float)
    
    # Validate column count
    if data.shape[1] < 7:
        raise ValueError(f"LCF file must have at least 7 columns, got {data.shape[1]}")
    
    # Extract the first 7 columns (0-6)
    time = data[:, 0]
    roll = data[:, 1]
    pitch = data[:, 2]
    yaw = data[:, 3]
    lon = data[:, 4]
    lat = data[:, 5]
    alt = data[:, 6]
    
    return LcfTable(time=time, roll=roll, pitch=pitch, yaw=yaw, lon=lon, lat=lat, alt=alt)


def read_times(times_path: Path | str, expected_lines: int | None = None) -> FrameTimes:
    """Parse a single-column .times file. If expected_lines given, raise ValueError on mismatch."""
    # Load data
    time_data = np.loadtxt(times_path, dtype=float)
    
    # Validate shape - should be 1D array
    if time_data.ndim != 1:
        raise ValueError("TIMES file must be a single column")
    
    # Check expected lines if provided
    if expected_lines is not None and len(time_data) != expected_lines:
        raise ValueError(f"TIMES file length {len(time_data)} does not match expected lines {expected_lines}")
    
    return FrameTimes(time=time_data)


def interpolate_poses(lcf: LcfTable, times: FrameTimes) -> FramePoses:
    """Align time bases by subtracting each file's first timestamp (RELATIVE alignment),
    then np.interp each lcf field at the per-frame relative time.
    For yaw/heading use np.unwrap on lcf.yaw before interp (so wrap-around is handled).
    """
    # Check monotonicity of time arrays
    if not np.all(np.diff(lcf.time) >= 0):
        raise ValueError("LCF time values must be monotonic increasing")
    if not np.all(np.diff(times.time) >= 0):
        raise ValueError("TIMES time values must be monotonic increasing")
    
    # Convert to relative time
    t_lcf_rel = lcf.time - lcf.time[0]
    t_frame_rel = times.time - times.time[0]
    
    # Check for time overlap
    max_lcf_rel = t_lcf_rel.max()
    max_frame_rel = t_frame_rel.max()
    time_diff = abs(max_lcf_rel - max_frame_rel)
    
    if time_diff > 0.5:
        raise ValueError(f"Time bases don't overlap; .lcf and .times durations differ by {time_diff:.1f} s")
    
    # Unwrap yaw for interpolation to handle wrap-around correctly
    yaw_unwrapped = np.unwrap(lcf.yaw)
    
    # Interpolate each field
    lon_interp = np.interp(t_frame_rel, t_lcf_rel, lcf.lon)
    lat_interp = np.interp(t_frame_rel, t_lcf_rel, lcf.lat)
    alt_interp = np.interp(t_frame_rel, t_lcf_rel, lcf.alt)
    roll_interp = np.interp(t_frame_rel, t_lcf_rel, lcf.roll)
    pitch_interp = np.interp(t_frame_rel, t_lcf_rel, lcf.pitch)
    yaw_interp = np.interp(t_frame_rel, t_lcf_rel, yaw_unwrapped)
    
    return FramePoses(lon=lon_interp, lat=lat_interp, alt=alt_interp,
                      roll=roll_interp, pitch=pitch_interp, yaw=yaw_interp)


def load_flight_line_poses(meta: FlightLineMeta) -> FramePoses:
    """Convenience: parses .lcf, .times, validates length against ENVI header, returns FramePoses.
    Raises ValueError if meta.times or meta.lcf is None."""
    # Check that required files exist
    if meta.times is None:
        raise ValueError("TIMES file is required but not found")
    if meta.lcf is None:
        raise ValueError("LCF file is required but not found")
    
    # Read ENVI header to get expected lines count
    hdr = read_envi_header(meta.hdr)
    
    # Parse files
    lcf = read_lcf(meta.lcf)
    times = read_times(meta.times, expected_lines=hdr.lines)
    
    # Interpolate poses
    return interpolate_poses(lcf, times)


def sample_view_angles(sensor: PushbroomSensor) -> np.ndarray:
    """Return cross-track view angle per sample in radians.
    Center sample ≈ 0; left/right cover ±fov/2. If flip_samples=True, reverse sign.
    
    FOV is a required parameter because PIKA-L can have different lenses.
    """
    # Determine principal sample (center of the sensor)
    principal = sensor.principal_sample
    if principal is None:
        principal = (sensor.samples - 1) / 2.0
    
    # Create array of sample indices
    samples = np.arange(sensor.samples)
    
    # Calculate angles in radians
    # Map samples to angles covering ±fov/2 range
    half_fov_rad = np.radians(sensor.fov_deg / 2.0)
    angles = (samples - principal) / (sensor.samples - 1) * 2.0 * half_fov_rad
    
    # Flip if needed
    if sensor.flip_samples:
        angles = -angles
    
    return angles


def flat_ground_grid(
    poses: FramePoses,
    sensor: PushbroomSensor,
    ground_alt: float,
) -> GroundGrid:
    """Intersect each raw pixel ray with a flat plane at ground_alt.
    Uses per-frame lon/lat/alt/roll/pitch/yaw and sensor cross-track angles.
    
    Coordinate convention for MVP:
    - Local ENU frame per line.
    - Aircraft body axes before rotation:
      - x = forward
      - y = right
      - z = down
    - Camera looks downward. For each sample, make a ray in body coordinates:
      - `angle = sample_view_angles(sensor)`
      - `ray_body = [0, sin(angle), -cos(angle)]` **IMPORTANT**: In ENU, z is UP;
        this vector is expressed with z-up sign, so downward is negative z.
        Keep a comment explaining this.
    - Apply yaw/pitch/roll to rotate body ray into local ENU:
      - Use explicit small rotation matrices, junior-readable.
      - Recommended order: `R = Rz(yaw) @ Ry(pitch) @ Rx(roll)`; document this convention
        and caveat that exact IMU convention may need calibration.
      - Output `ray_enu = R @ ray_body`.
    - Intersect with flat plane `z = ground_alt` relative to aircraft origin:
      - `dz = ray_enu_z`
      - `scale = (ground_alt - aircraft_alt) / dz`
      - valid if `dz < -1e-6` and `scale > 0`.
      - `east = scale * ray_enu_x`, `north = scale * ray_enu_y`, `up = ground_alt - aircraft_alt`.
    - Convert ENU offset to geodetic for each line/sample with `pymap3d.enu2geodetic(east, north, up, lat0, lon0, h0)`.
      - Use aircraft line pose as origin (`lat0=poses.lat[line]`, `lon0=poses.lon[line]`, `h0=poses.alt[line]`).
      - Vectorize per line over all samples; loop over lines is fine for readability.
    
    Important caveats to document in docstrings/comments:
    - FOV is a required parameter because PIKA-L can have different lenses.
    - `ground_alt` is a flat plane; this is an MVP before DEM.
    - Roll/pitch/yaw convention may need calibration/sign flips depending on IMU export.
    - This computes geolocation arrays only; it does not resample imagery.
    """
    # Get view angles for all samples
    angles = sample_view_angles(sensor)  # shape: (samples,)
    
    # Number of lines/frames
    lines = len(poses.lat)
    samples = sensor.samples
    
    # Preallocate output arrays
    lon_grid = np.empty((lines, samples), dtype=np.float64)
    lat_grid = np.empty((lines, samples), dtype=np.float64)
    alt_grid = np.full((lines, samples), ground_alt, dtype=np.float64)
    valid_grid = np.zeros((lines, samples), dtype=bool)
    
    # Create rotation matrices helper functions
    def rotation_x(angle):
        """Rotation matrix around x-axis"""
        cos_a = np.cos(angle)
        sin_a = np.sin(angle)
        return np.array([
            [1, 0, 0],
            [0, cos_a, -sin_a],
            [0, sin_a, cos_a]
        ])
    
    def rotation_y(angle):
        """Rotation matrix around y-axis"""
        cos_a = np.cos(angle)
        sin_a = np.sin(angle)
        return np.array([
            [cos_a, 0, sin_a],
            [0, 1, 0],
            [-sin_a, 0, cos_a]
        ])
    
    def rotation_z(angle):
        """Rotation matrix around z-axis"""
        cos_a = np.cos(angle)
        sin_a = np.sin(angle)
        return np.array([
            [cos_a, -sin_a, 0],
            [sin_a, cos_a, 0],
            [0, 0, 1]
        ])
    
    # Process each line/frame
    for line in range(lines):
        # Get pose for this line
        lat0 = poses.lat[line]
        lon0 = poses.lon[line]
        h0 = poses.alt[line]
        roll = poses.roll[line]
        pitch = poses.pitch[line]
        yaw = poses.yaw[line]
        
        # Create rotation matrix: R = Rz(yaw) @ Ry(pitch) @ Rx(roll)
        # This is the standard aerospace convention for Euler angles
        Rz = rotation_z(yaw)
        Ry = rotation_y(pitch)
        Rx = rotation_x(roll)
        R = Rz @ Ry @ Rx
        
        # Vectorize computation for all samples in this line
        # Create rays in body coordinates for all samples
        # ray_body = [0, sin(angle), -cos(angle)] for each angle
        ray_body_x = np.zeros(samples)
        ray_body_y = np.sin(angles)
        ray_body_z = -np.cos(angles)
        
        # Rotate all rays to ENU coordinates at once
        # R is 3x3, ray_body is 3xN where N=samples
        ray_body = np.vstack([ray_body_x, ray_body_y, ray_body_z])  # shape: (3, samples)
        ray_enu = R @ ray_body  # shape: (3, samples)
        
        # Extract components
        ray_e = ray_enu[0, :]  # east components for all samples
        ray_n = ray_enu[1, :]  # north components for all samples
        ray_u = ray_enu[2, :]  # up components for all samples
        
        # Intersect with flat ground plane at ground_alt
        # dz = ray_enu_z (z-component of ray in ENU)
        dz = ray_u
        
        # Check if rays point downward (toward ground)
        downward = dz < -1e-6  # Boolean array for all samples
        
        # Scale factor to reach ground plane for valid rays
        # scale = (ground_alt - aircraft_alt) / dz
        aircraft_alt = h0
        scale = np.full_like(dz, np.nan)
        scale[downward] = (ground_alt - aircraft_alt) / dz[downward]
        
        # Check if intersection is in front of camera (positive scale)
        in_front = scale > 0
        
        # Valid rays are those that are downward and in front
        valid = downward & in_front
        valid_grid[line, :] = valid
        
        # Calculate ENU offsets for valid rays
        east = np.full_like(scale, np.nan)
        north = np.full_like(scale, np.nan)
        up = ground_alt - aircraft_alt  # Constant for all samples
        
        east[valid] = scale[valid] * ray_e[valid]
        north[valid] = scale[valid] * ray_n[valid]
        
        # Convert to geodetic coordinates for valid rays
        if np.any(valid):
            try:
                # Vectorized conversion using pymap3d
                # enu2geodetic returns (lat, lon, alt), so we need to assign correctly
                lat, lon, _ = enu2geodetic(
                    east[valid], north[valid], up,
                    lat0, lon0, aircraft_alt,
                    deg=True
                )
                lon_grid[line, valid] = lon
                lat_grid[line, valid] = lat
            except Exception:
                # If conversion fails, mark as invalid
                valid_grid[line, :] = False
    
    return GroundGrid(lon=lon_grid, lat=lat_grid, alt=alt_grid, valid=valid_grid)


@dataclass(frozen=True)
class GeorefResult:
    """Summary of a written flat-earth georeferenced GeoTIFF."""
    output_path: Path
    crs: str
    width: int
    height: int
    bands: int
    transform: object  # rasterio Affine; keep loose to avoid importing Affine in type hints


def write_flat_geotiff(
    meta: FlightLineMeta,
    output_path: str | Path,
    fov_deg: float,
    ground_alt: float,
    dem_path: str | Path | None = None,
    dst_crs: str = "EPSG:4326",
    resolution: float | None = None,
    nodata: float | int | None = None,
) -> GeorefResult:
    """Write a flat-earth georeferenced GeoTIFF from a PIKA-L raw cube.

    MVP assumptions:
    - FOV is user-provided because PIKA-L lenses differ.
    - ground_alt is a flat plane; when dem_path is provided, it's used for DEM-aware georeferencing.
    - uses geolocation arrays from P3.7a; orientation may need calibration.
    
    When dem_path is None, uses flat_ground_grid with constant ground_alt.
    When dem_path is provided, uses dem_ground_grid with ground_alt as fallback.
    """
    # Validate raw cube exists FIRST before doing expensive operations
    if not meta.bil.exists():
        raise FileNotFoundError(f"Raw cube file not found: {meta.bil}")
    
    # Lazy import rasterio inside write_flat_geotiff() so import-time stays light
    try:
        import rasterio
        from rasterio.transform import from_bounds
        from rasterio.warp import reproject, Resampling
        from rasterio.crs import CRS
        import inspect
    except ImportError:
        raise ImportError("rasterio is required to write georeferenced GeoTIFFs")
    
    # Read header
    header = read_envi_header(meta.hdr)
    
    # Load poses
    poses = load_flight_line_poses(meta)
    
    # Build sensor and grid
    sensor = PushbroomSensor(samples=header.samples, fov_deg=fov_deg)
    
    # Use either flat or DEM-aware grid generation
    if dem_path is None:
        grid = flat_ground_grid(poses, sensor, ground_alt=ground_alt)
    else:
        grid = dem_ground_grid(poses, sensor, dem_path, fallback_ground_alt=ground_alt)
    
    # Open cube with rasterio
    with rasterio.open(meta.bil) as src:
        # Validate dimensions
        if src.width != header.samples:
            raise ValueError(f"Width mismatch: header samples {header.samples} != raster width {src.width}")
        if src.height != header.lines:
            raise ValueError(f"Height mismatch: header lines {header.lines} != raster height {src.height}")
        if src.count != header.bands:
            raise ValueError(f"Band count mismatch: header bands {header.bands} != raster bands {src.count}")
        
        # Define destination bounds and size
        # Use valid grid cells only
        lon = grid.lon[grid.valid]
        lat = grid.lat[grid.valid]
        
        # Calculate bounds
        if len(lon) == 0 or len(lat) == 0:
            raise ValueError("No valid geolocation points found in grid")
        
        min_lon, max_lon = lon.min(), lon.max()
        min_lat, max_lat = lat.min(), lat.max()
        
        # Handle coordinate transformation if needed
        if dst_crs != "EPSG:4326":
            try:
                import pyproj
                transformer = pyproj.Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)
                # Transform the bounds
                min_x, min_y = transformer.transform(min_lon, min_lat)
                max_x, max_y = transformer.transform(max_lon, max_lat)
                # Ensure proper ordering
                minx, maxx = min(min_x, max_x), max(min_x, max_x)
                miny, maxy = min(min_y, max_y), max(min_y, max_y)
            except ImportError:
                raise ImportError("pyproj is required for coordinate transformation")
        else:
            minx, miny = min_lon, min_lat
            maxx, maxy = max_lon, max_lat
        
        # Calculate resolution if not provided
        if resolution is None:
            if dst_crs == "EPSG:4326":
                # For geographic CRS, use degrees
                x_extent = max_lon - min_lon
                y_extent = max_lat - min_lat
                resolution = max(x_extent / header.samples, y_extent / header.lines)
                if resolution <= 0:
                    resolution = 1e-6  # fallback
            else:
                # For projected CRS, use meters
                x_extent = maxx - minx
                y_extent = maxy - miny
                resolution = max(x_extent / header.samples, y_extent / header.lines)
                if resolution <= 0:
                    resolution = 1.0  # fallback
        
        # Calculate width and height
        from math import ceil
        width = max(1, int(ceil((maxx - minx) / resolution)))
        height = max(1, int(ceil((maxy - miny) / resolution)))
        
        # Create transform
        transform = from_bounds(minx, miny, maxx, maxy, width, height)
        
        # Check if rasterio supports geolocation arrays by inspecting the signature
        reproject_signature = inspect.signature(reproject)
        geolocation_supported = 'src_geoloc_array' in reproject_signature.parameters
        
        if not geolocation_supported:
            # If geolocation array support is not available, raise clear error
            raise RuntimeError("This rasterio version lacks geolocation-array support. "
                             "Suggest using GDAL Warp/GCP fallback for next iteration.")
        
        # Write output profile
        profile = {
            'driver': 'GTiff',
            'count': src.count,
            'dtype': src.dtypes[0],
            'crs': dst_crs,
            'transform': transform,
            'width': width,
            'height': height,
            'compress': 'deflate',
            'BIGTIFF': 'IF_SAFER'
        }
        
        if nodata is not None:
            profile['nodata'] = nodata
        
        # Write the georeferenced GeoTIFF
        with rasterio.open(output_path, 'w', **profile) as dst:
            # Reproject one band at a time to keep memory simple
            for b in range(1, src.count + 1):
                # Use nearest neighbor resampling for spectral safety
                # Use actual geolocation arrays for accurate georeferencing
                reproject(
                    source=rasterio.band(src, b),
                    destination=rasterio.band(dst, b),
                    src_geoloc_array=(grid.lon, grid.lat),  # Use actual geolocation arrays
                    src_crs=CRS.from_string("EPSG:4326"),
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.nearest,
                )
    
    # Return GeorefResult
    return GeorefResult(
        output_path=Path(output_path),
        crs=dst_crs,
        width=width,
        height=height,
        bands=src.count,
        transform=transform
    )


def dem_ground_grid(
    poses: FramePoses,
    sensor: PushbroomSensor,
    dem_path: str | Path,
    fallback_ground_alt: float,
    max_iterations: int = 8,
    tolerance_m: float = 0.25,
) -> GroundGrid:
    """Intersect each raw pixel ray with terrain from a DEM.
    
    This function mirrors flat_ground_grid() but replaces the constant ground altitude
    with sampled DEM altitude. It uses iterative ray/DEM intersection to refine
    the ground intersection point.
    
    Parameters:
    - poses: FramePoses with per-frame lon/lat/alt/roll/pitch/yaw
    - sensor: PushbroomSensor with sensor parameters
    - dem_path: Path to the DEM file
    - fallback_ground_alt: Initial altitude estimate when DEM sample is invalid
    - max_iterations: Maximum number of iterations for ray/DEM intersection (default 8)
    - tolerance_m: Convergence tolerance in meters (default 0.25)
    
    Returns:
    - GroundGrid with lon/lat/alt/valid arrays for each pixel
    """
    # Check if DEM path exists
    if dem_path is None:
        raise ValueError("DEM path is required but not provided")
    
    dem_path = Path(dem_path)
    if not dem_path.exists():
        raise FileNotFoundError(f"DEM file not found: {dem_path}")
    
    # Lazy import rasterio only when needed
    try:
        import rasterio
    except ImportError as e:
        raise ImportError(f"rasterio is required for DEM-aware georeferencing: {e}")
    
    # Lazy import pyproj.Transformer only when needed
    Transformer = None
    
    # Open DEM with rasterio
    with rasterio.open(dem_path) as dem:
        # Check if DEM CRS is missing
        if dem.crs is None:
            raise ValueError(f"DEM file {dem_path} has no CRS")
        
        # Prepare transformer if needed
        if str(dem.crs) != "EPSG:4326":
            try:
                from pyproj import Transformer
            except ImportError as e:
                raise ImportError(f"pyproj is required for coordinate transformation: {e}")
            transformer = Transformer.from_crs("EPSG:4326", dem.crs, always_xy=True)
        else:
            transformer = None
        
        # Get view angles for all samples
        angles = sample_view_angles(sensor)  # shape: (samples,)
        
        # Number of lines/frames
        lines = len(poses.lat)
        samples = sensor.samples
        
        # Preallocate output arrays
        lon_grid = np.empty((lines, samples), dtype=np.float64)
        lat_grid = np.empty((lines, samples), dtype=np.float64)
        alt_grid = np.empty((lines, samples), dtype=np.float64)
        valid_grid = np.zeros((lines, samples), dtype=bool)
        
        # Create rotation matrices helper functions
        def rotation_x(angle):
            """Rotation matrix around x-axis"""
            cos_a = np.cos(angle)
            sin_a = np.sin(angle)
            return np.array([
                [1, 0, 0],
                [0, cos_a, -sin_a],
                [0, sin_a, cos_a]
            ])
        
        def rotation_y(angle):
            """Rotation matrix around y-axis"""
            cos_a = np.cos(angle)
            sin_a = np.sin(angle)
            return np.array([
                [cos_a, 0, sin_a],
                [0, 1, 0],
                [-sin_a, 0, cos_a]
            ])
        
        def rotation_z(angle):
            """Rotation matrix around z-axis"""
            cos_a = np.cos(angle)
            sin_a = np.sin(angle)
            return np.array([
                [cos_a, -sin_a, 0],
                [sin_a, cos_a, 0],
                [0, 0, 1]
            ])
        
        # Process each line/frame
        for line in range(lines):
            # Get pose for this line
            lat0 = poses.lat[line]
            lon0 = poses.lon[line]
            h0 = poses.alt[line]
            roll = poses.roll[line]
            pitch = poses.pitch[line]
            yaw = poses.yaw[line]
            
            # Create rotation matrix: R = Rz(yaw) @ Ry(pitch) @ Rx(roll)
            Rz = rotation_z(yaw)
            Ry = rotation_y(pitch)
            Rx = rotation_x(roll)
            R = Rz @ Ry @ Rx
            
            # Vectorize computation for all samples in this line
            # Create rays in body coordinates for all samples
            # ray_body = [0, sin(angle), -cos(angle)] for each angle
            ray_body_x = np.zeros(samples)
            ray_body_y = np.sin(angles)
            ray_body_z = -np.cos(angles)
            
            # Rotate all rays to ENU coordinates at once
            ray_body = np.vstack([ray_body_x, ray_body_y, ray_body_z])  # shape: (3, samples)
            ray_enu = R @ ray_body  # shape: (3, samples)
            
            # Extract components
            ray_e = ray_enu[0, :]  # east components for all samples
            ray_n = ray_enu[1, :]  # north components for all samples
            ray_u = ray_enu[2, :]  # up components for all samples
            
            # Intersect with DEM using iterative approach
            # Start with fallback altitude for all samples
            ground_alt = np.full(samples, fallback_ground_alt, dtype=np.float64)
            valid = np.zeros(samples, dtype=bool)
            
            # Iterative ray/DEM intersection
            for iteration in range(max_iterations):
                # Calculate ENU offsets for current ground_alt estimate
                dz = ray_u
                downward = dz < -1e-6
                
                # Scale factor to reach ground plane for valid rays
                scale = np.full_like(dz, np.nan)
                scale[downward] = (ground_alt - h0) / dz[downward]
                
                # Check if intersection is in front of camera (positive scale)
                in_front = scale > 0
                
                # Valid rays are those that are downward and in front
                current_valid = downward & in_front
                
                # If no valid rays, skip to next line
                if not np.any(current_valid):
                    break
                
                # Calculate ENU offsets for valid rays
                east = np.full_like(scale, np.nan)
                north = np.full_like(scale, np.nan)
                up = ground_alt - h0  # Variable for each sample
                
                east[current_valid] = scale[current_valid] * ray_e[current_valid]
                north[current_valid] = scale[current_valid] * ray_n[current_valid]
                
                # Convert to geodetic coordinates for valid rays
                if np.any(current_valid):
                    try:
                        # Vectorized conversion using pymap3d
                        # enu2geodetic returns (lat, lon, alt)
                        lat, lon, _ = enu2geodetic(
                            east[current_valid], north[current_valid], up[current_valid],
                            lat0, lon0, h0,
                            deg=True
                        )
                        
                        # Sample DEM at those lon/lat points
                        if transformer is not None:
                            # Transform to DEM CRS
                            x, y = transformer.transform(lon, lat)
                            coords = zip(x, y)
                        else:
                            # Already in EPSG:4326
                            coords = zip(lon, lat)
                        
                        # Sample DEM using nearest neighbor
                        dem_samples = list(dem.sample(coords, indexes=1))
                        dem_alt = np.array(dem_samples, dtype=np.float64)
                        
                        # Handle nodata and non-finite values
                        valid_dem = np.isfinite(dem_alt) & (dem_alt != dem.nodata if dem.nodata is not None else np.ones_like(dem_alt, dtype=bool))
                        
                        # Update ground_alt with valid DEM samples
                        if np.any(valid_dem):
                            # Get indices of valid DEM samples in the current_valid array
                            valid_indices = np.where(current_valid)[0]
                            dem_valid_indices = valid_indices[valid_dem]
                            
                            # Calculate altitude changes
                            alt_changes = np.abs(ground_alt[dem_valid_indices] - dem_alt[valid_dem])
                            
                            # Update ground_alt for valid DEM samples
                            ground_alt[dem_valid_indices] = dem_alt[valid_dem]
                            
                            # Update valid mask
                            valid[dem_valid_indices] = True
                            
                            # Check convergence
                            if np.all(alt_changes < tolerance_m):
                                # Converged for all valid samples
                                break
                        else:
                            # No valid DEM samples in this iteration, mark as invalid if this is the last iteration
                            if iteration == max_iterations - 1:
                                valid[current_valid] = False
                    except Exception:
                        # If conversion or sampling fails, mark as invalid
                        current_valid[:] = False
                        break
                else:
                    # No valid rays, break
                    break
            
            # After iterations, calculate final positions for valid rays
            if np.any(valid):
                try:
                    # Recalculate ENU offsets for final ground_alt estimate
                    dz = ray_u
                    downward = dz < -1e-6
                    scale = np.full_like(dz, np.nan)
                    scale[downward] = (ground_alt - h0) / dz[downward]
                    in_front = scale > 0
                    final_valid = downward & in_front & valid
                    
                    if np.any(final_valid):
                        east = np.full_like(scale, np.nan)
                        north = np.full_like(scale, np.nan)
                        up = ground_alt - h0
                        
                        east[final_valid] = scale[final_valid] * ray_e[final_valid]
                        north[final_valid] = scale[final_valid] * ray_n[final_valid]
                        
                        # Convert to geodetic coordinates for final valid rays
                        lat, lon, _ = enu2geodetic(
                            east[final_valid], north[final_valid], up[final_valid],
                            lat0, lon0, h0,
                            deg=True
                        )
                        
                        lon_grid[line, final_valid] = lon
                        lat_grid[line, final_valid] = lat
                        alt_grid[line, final_valid] = ground_alt[final_valid]
                        valid_grid[line, :] = final_valid
                except Exception:
                    # If final conversion fails, mark as invalid
                    valid_grid[line, :] = False
            else:
                # No valid rays for this line
                valid_grid[line, :] = False
    
    return GroundGrid(lon=lon_grid, lat=lat_grid, alt=alt_grid, valid=valid_grid)
