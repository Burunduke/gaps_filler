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
from .models import FlightLineMeta, discover_flight_line
from .envi_io import read_envi_header

# Import for ENU transformations.
# We deliberately re-raise as a clear error at *call time* if pymap3d is
# missing, rather than silently degrading: previously the fallback stub
# raised ImportError inside flat_ground_grid, which was swallowed by a
# broad `except Exception:` and surfaced only as "No valid geolocation
# points found in grid" with no hint about the missing dependency.
try:
    from pymap3d import enu2geodetic
    _PYMAP3D_IMPORT_ERROR: ImportError | None = None
except ImportError as _exc:
    enu2geodetic = None  # type: ignore[assignment]
    _PYMAP3D_IMPORT_ERROR = _exc

# Import for vector writing
try:
    from osgeo import ogr, osr
except ImportError:
    # Fallback if osgeo is not available
    ogr = None
    osr = None

# Import for shapely simplification (optional)
try:
    from shapely.geometry import Polygon
    from shapely import simplify
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False


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


def interpolate_poses(lcf: LcfTable, times: FrameTimes, time_offset_s: float = 0.0) -> FramePoses:
    """Align time bases by subtracting each file's first timestamp (RELATIVE alignment),
    then np.interp each lcf field at the per-frame relative time.
    For yaw/heading use np.unwrap on lcf.yaw before interp (so wrap-around is handled).
    
    time_offset_s: Time offset in seconds to add to image timestamps before pose interpolation.
    """
    # Check monotonicity of time arrays
    if not np.all(np.diff(lcf.time) >= 0):
        raise ValueError("LCF time values must be monotonic increasing")
    if not np.all(np.diff(times.time) >= 0):
        raise ValueError("TIMES time values must be monotonic increasing")
    
    # Convert to relative time
    t_lcf_rel = lcf.time - lcf.time[0]
    # Apply time offset to frame times before converting to relative time
    t_frame_abs = times.time + time_offset_s
    t_frame_rel = t_frame_abs - times.time[0]
    
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


def load_flight_line_poses(meta: FlightLineMeta, time_offset_s: float = 0.0) -> FramePoses:
    """Convenience: parses .lcf, .times, validates length against ENVI header, returns FramePoses.
    Raises ValueError if meta.times or meta.lcf is None.
    
    time_offset_s: Time offset in seconds to add to image timestamps before pose interpolation.
    """
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
    
    # Interpolate poses with time offset
    return interpolate_poses(lcf, times, time_offset_s=time_offset_s)


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


def _build_rotation_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """Build 3x3 rotation matrix from roll/pitch/yaw (radians).

    Rotation order matches the original implementation in flat_ground_grid /
    dem_ground_grid (verify before changing).
    """
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
    
    # Create rotation matrix: R = Rz(yaw) @ Ry(pitch) @ Rx(roll)
    Rz = rotation_z(yaw)
    Ry = rotation_y(pitch)
    Rx = rotation_x(roll)
    R = Rz @ Ry @ Rx
    
    return R


def flat_ground_grid(
    poses: FramePoses,
    sensor: PushbroomSensor,
    ground_alt: float,
    boresight_roll_deg: float = 0.0,
    boresight_pitch_deg: float = 0.0,
    boresight_yaw_deg: float = 0.0,
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
    
    Boresight convention: Boresight is a small fixed rotation of the camera frame relative
    to the IMU body frame, applied as Z-Y-X intrinsic Euler (yaw → pitch → roll),
    right-handed, degrees, with the convention: positive roll = right wing down,
    positive pitch = nose up, positive yaw = nose right (clockwise from above).
    At each frame, the effective camera attitude in the local ENU frame is
    R_enu_from_camera = R_enu_from_imu(t) · R_imu_from_camera(boresight).
    
    Important caveats to document in docstrings/comments:
    - FOV is a required parameter because PIKA-L can have different lenses.
    - `ground_alt` is a flat plane; this is an MVP before DEM.
    - Roll/pitch/yaw convention may need calibration/sign flips depending on IMU export.
    - This computes geolocation arrays only; it does not resample imagery.
    """
    # Fail fast with a clear message if pymap3d is missing, instead of
    # surfacing the failure as "No valid geolocation points found in grid".
    if enu2geodetic is None:
        raise ImportError(
            "pymap3d is required for georeferencing but is not installed in "
            "the QGIS Python environment. Install it (e.g. "
            "`pip install pymap3d`) and restart QGIS. "
            "Original import error: %s" % _PYMAP3D_IMPORT_ERROR
        )

    # Validate geometry: ground plane must be below aircraft for any ray to hit.
    if ground_alt >= float(np.min(poses.alt)):
        raise ValueError(
            "ground_alt (%r m) must be below the minimum aircraft altitude "
            "(%.3f m) for rays to intersect the ground plane." % (
                ground_alt, float(np.min(poses.alt))
            )
        )

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

    # Track the first exception encountered inside the per-line loop so we can
    # surface it in the final error message instead of silently masking pixels.
    _first_exc: BaseException | None = None
    _exc_lines = 0
    
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
        R = _build_rotation_matrix(roll, pitch, yaw)
        
        # Apply boresight rotation if specified
        # Convert boresight angles from degrees to radians
        if boresight_roll_deg != 0.0 or boresight_pitch_deg != 0.0 or boresight_yaw_deg != 0.0:
            boresight_roll_rad = np.radians(boresight_roll_deg)
            boresight_pitch_rad = np.radians(boresight_pitch_deg)
            boresight_yaw_rad = np.radians(boresight_yaw_deg)
            
            # Create boresight rotation matrix: R_b = Rz(yaw) @ Ry(pitch) @ Rx(roll)
            R_b = _build_rotation_matrix(boresight_roll_rad, boresight_pitch_rad, boresight_yaw_rad)
            
            # Apply boresight rotation: R_enu_from_camera = R_enu_from_imu * R_imu_from_camera
            R = R @ R_b
        
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
        up_scalar = float(ground_alt - aircraft_alt)  # Constant for all samples

        east[valid] = scale[valid] * ray_e[valid]
        north[valid] = scale[valid] * ray_n[valid]
        # Build `up` as an array matching east/north length: some pymap3d
        # versions don't broadcast a scalar `up` against array east/north
        # (suspected Windows symptom: enu2geodetic raises -> all pixels
        # marked invalid silently).
        n_valid = int(np.count_nonzero(valid))
        up = np.full(n_valid, up_scalar, dtype=np.float64)


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
                lon_grid[line, valid] = np.asarray(lon)
                lat_grid[line, valid] = np.asarray(lat)
            except Exception as _exc:
                # Don't mask the failure indefinitely; remember the first
                # error so it surfaces in the final RuntimeError below.
                valid_grid[line, :] = False
                _exc_lines += 1
                if _first_exc is None:
                    _first_exc = _exc

    # If no valid pixels, raise here with the underlying cause embedded in
    # the message so QGIS log surfaces the real reason (stderr is not
    # captured reliably by QGIS, especially on Windows).
    if not np.any(valid_grid):
        detail = (
            "ground_alt=%r aircraft_alt(min/mean/max)=%.3f/%.3f/%.3f "
            "exc_lines=%d/%d pymap3d_module=%r" % (
                ground_alt,
                float(np.min(poses.alt)), float(np.mean(poses.alt)),
                float(np.max(poses.alt)),
                _exc_lines, lines,
                getattr(enu2geodetic, "__module__", None),
            )
        )
        if _first_exc is not None:
            raise RuntimeError(
                "No valid geolocation points produced; first underlying "
                "error: %s: %s. Context: %s" % (
                    type(_first_exc).__name__, _first_exc, detail
                )
            ) from _first_exc
        raise RuntimeError(
            "No valid geolocation points produced. Context: " + detail
        )

    return GroundGrid(lon=lon_grid, lat=lat_grid, alt=alt_grid, valid=valid_grid)


def extract_footprint_polygon(grid: GroundGrid, simplify_tolerance: float = 0.0) -> list[tuple[float, float]]:
    """Extract the footprint polygon from a GroundGrid.
    
    Takes the outer boundary of the projected grid:
    - Top edge (frame 0, all across-track samples)
    - Right edge (all frames, last across-track sample)
    - Bottom edge (last frame, all across-track samples reversed)
    - Left edge (all frames reversed, first across-track sample)
    
    Args:
        grid: GroundGrid with lon/lat/valid arrays
        simplify_tolerance: Simplification tolerance in degrees (0 = no simplification)
        
    Returns:
        List of (lon, lat) tuples forming a closed ring
    """
    lines, samples = grid.lon.shape
    
    if lines < 2 or samples < 2:
        raise ValueError("Grid must be at least 2x2")
    
    # Extract valid coordinates
    valid_lon = grid.lon[grid.valid]
    valid_lat = grid.lat[grid.valid]
    
    if len(valid_lon) == 0:
        raise ValueError("No valid points in grid")
    
    # Build the outer ring
    ring_points = []
    
    # Top edge: frame 0, all across-track samples
    for s in range(samples):
        if grid.valid[0, s]:
            ring_points.append((grid.lon[0, s], grid.lat[0, s]))
    
    # Right edge: all frames, last across-track sample
    for l in range(lines):
        if grid.valid[l, samples-1]:
            ring_points.append((grid.lon[l, samples-1], grid.lat[l, samples-1]))
    
    # Bottom edge: last frame, all across-track samples (reversed)
    for s in range(samples-1, -1, -1):
        if grid.valid[lines-1, s]:
            ring_points.append((grid.lon[lines-1, s], grid.lat[lines-1, s]))
    
    # Left edge: all frames (reversed), first across-track sample
    for l in range(lines-1, -1, -1):
        if grid.valid[l, 0]:
            ring_points.append((grid.lon[l, 0], grid.lat[l, 0]))
    
    # Close the ring by adding the first point again
    if ring_points:
        ring_points.append(ring_points[0])
    
    # Simplify if requested and shapely is available
    if simplify_tolerance > 0 and SHAPELY_AVAILABLE and len(ring_points) > 3:
        try:
            polygon = Polygon(ring_points)
            simplified = simplify(polygon, tolerance=simplify_tolerance)
            if simplified.exterior:
                # Convert back to list of tuples
                simplified_points = list(simplified.exterior.coords)
                return simplified_points
        except Exception:
            # If simplification fails, return original points
            pass
    
    return ring_points


def write_footprint_vector(
    grid: GroundGrid,
    footprint_path: str | Path,
    dst_crs: str = "EPSG:4326",
    flight_line_name: str = "",
    simplify_tolerance: float = 0.0
) -> None:
    """Write the footprint polygon as a vector file.
    
    Args:
        grid: GroundGrid with lon/lat/valid arrays
        footprint_path: Path to write the vector file to
        dst_crs: Destination CRS (default EPSG:4326)
        flight_line_name: Name of the flight line for attributes
        simplify_tolerance: Simplification tolerance (0 = no simplification)
    """
    if ogr is None:
        raise ImportError("osgeo.ogr is required to write vector files")
    
    # Extract the footprint polygon ring
    ring_points = extract_footprint_polygon(grid, simplify_tolerance)
    
    if len(ring_points) < 3:
        raise ValueError("Footprint polygon must have at least 3 points")
    
    # Determine driver from file extension
    path_str = str(footprint_path).lower()
    if path_str.endswith('.gpkg'):
        driver_name = 'GPKG'
    elif path_str.endswith('.shp'):
        driver_name = 'ESRI Shapefile'
    else:  # Default to GeoJSON
        driver_name = 'GeoJSON'
    
    # Create the vector file
    driver = ogr.GetDriverByName(driver_name)
    if driver is None:
        raise RuntimeError(f"OGR driver '{driver_name}' not available")
    
    # Remove existing file if it exists
    import os
    if os.path.exists(footprint_path):
        driver.DeleteDataSource(footprint_path)
    
    # Create data source
    datasource = driver.CreateDataSource(str(footprint_path))
    if datasource is None:
        raise RuntimeError(f"Failed to create datasource at {footprint_path}")
    
    # Initialize variables for cleanup
    layer = None
    feature = None
    ring = None
    polygon = None
    spatial_ref = None
    output_ref = None
    transform = None
    
    try:
        # Create layer
        layer_name = Path(footprint_path).stem
        layer = datasource.CreateLayer(layer_name, geom_type=ogr.wkbPolygon)
        if layer is None:
            raise RuntimeError("Failed to create layer")
        
        # Add fields
        field_defn = ogr.FieldDefn("flight_line", ogr.OFTString)
        field_defn.SetWidth(255)
        layer.CreateField(field_defn)
        
        field_defn = ogr.FieldDefn("n_frames", ogr.OFTInteger)
        layer.CreateField(field_defn)
        
        field_defn = ogr.FieldDefn("n_xtrack", ogr.OFTInteger)
        layer.CreateField(field_defn)
        
        # Add mean_alt_m field if altitude data is available
        field_defn = ogr.FieldDefn("mean_alt_m", ogr.OFTReal)
        layer.CreateField(field_defn)
        
        # Create feature
        feature = ogr.Feature(layer.GetLayerDefn())
        
        # Set attributes
        feature.SetField("flight_line", flight_line_name)
        feature.SetField("n_frames", grid.lon.shape[0])
        feature.SetField("n_xtrack", grid.lon.shape[1])
        
        # Calculate mean altitude if all alt values are the same (flat grid)
        if np.all(grid.alt == grid.alt[0, 0]):
            feature.SetField("mean_alt_m", float(grid.alt[0, 0]))
        # For DEM grids, we could calculate mean, but for now we'll leave it unset
        
        # Create polygon geometry
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for lon, lat in ring_points:
            ring.AddPoint(lon, lat)
        
        polygon = ogr.Geometry(ogr.wkbPolygon)
        polygon.AddGeometry(ring)
        
        # Set CRS
        spatial_ref = osr.SpatialReference()
        spatial_ref.SetFromUserInput("EPSG:4326")
        polygon.AssignSpatialReference(spatial_ref)
        
        # Transform to output CRS if needed
        if dst_crs != "EPSG:4326":
            output_ref = osr.SpatialReference()
            output_ref.SetFromUserInput(dst_crs)
            transform = osr.CoordinateTransformation(spatial_ref, output_ref)
            polygon.Transform(transform)
        
        feature.SetGeometry(polygon)
        
        # Create feature in layer
        if layer.CreateFeature(feature) != 0:
            raise RuntimeError("Failed to create feature")
    
    finally:
        # Cleanup OGR objects
        if feature is not None:
            feature.Destroy()
        if layer is not None:
            layer = None
        if datasource is not None:
            datasource.Destroy()
        if polygon is not None:
            polygon.Destroy()
        if ring is not None:
            ring.Destroy()
        # spatial_ref, output_ref, transform don't need explicit cleanup in GDAL/OGR Python


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
    boresight_roll_deg: float = 0.0,
    boresight_pitch_deg: float = 0.0,
    boresight_yaw_deg: float = 0.0,
    time_offset_s: float = 0.0,
    footprint_path: str | Path | None = None,
) -> GeorefResult:
    """Write a flat-earth georeferenced GeoTIFF from a PIKA-L raw cube.

    MVP assumptions:
    - FOV is user-provided because PIKA-L lenses differ.
    - ground_alt is a flat plane; when dem_path is provided, it's used for DEM-aware georeferencing.
    - uses geolocation arrays from P3.7a; orientation may need calibration.
    
    When dem_path is None, uses flat_ground_grid with constant ground_alt.
    When dem_path is provided, uses dem_ground_grid with ground_alt as fallback.
    
    Boresight convention: Boresight is a small fixed rotation of the camera frame relative
    to the IMU body frame, applied as Z-Y-X intrinsic Euler (yaw → pitch → roll),
    right-handed, degrees, with the convention: positive roll = right wing down,
    positive pitch = nose up, positive yaw = nose right (clockwise from above).
    At each frame, the effective camera attitude in the local ENU frame is
    R_enu_from_camera = R_enu_from_imu(t) · R_imu_from_camera(boresight).
    
    Time offset convention: time_offset_s is added to the image-frame timestamp
    before looking up the IMU/GPS pose: t_lookup = t_image + time_offset_s.
    Positive value means the images were stamped earlier than GPS, so we shift
    forward to align.
    
    LCF angles are in radians per Resonon spec; we convert boresight from degrees
    to radians before composing.
    
    footprint_path: Optional path to write the footprint polygon as a vector file.
    Format is inferred from extension (.geojson, .gpkg, .shp). When None, no footprint is written.
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
    
    # Load poses with time offset
    poses = load_flight_line_poses(meta, time_offset_s=time_offset_s)
    
    # Build sensor and grid
    sensor = PushbroomSensor(samples=header.samples, fov_deg=fov_deg)
    
    # Use either flat or DEM-aware grid generation
    if dem_path is None:
        grid = flat_ground_grid(poses, sensor, ground_alt=ground_alt,
                                boresight_roll_deg=boresight_roll_deg,
                                boresight_pitch_deg=boresight_pitch_deg,
                                boresight_yaw_deg=boresight_yaw_deg)
    else:
        grid = dem_ground_grid(poses, sensor, dem_path, fallback_ground_alt=ground_alt,
                                boresight_roll_deg=boresight_roll_deg,
                                boresight_pitch_deg=boresight_pitch_deg,
                                boresight_yaw_deg=boresight_yaw_deg)
    
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
        if nodata is None:
            if np.issubdtype(np.dtype(src.dtypes[0]), np.floating):
                nodata = float("nan")
            else:
                nodata = 0
        
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
                    src_nodata=src.nodata,
                    dst_nodata=nodata,
                    init_dest_nodata=True,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.nearest,
                )
     
    # Write footprint vector file if requested
    if footprint_path is not None:
        # Reuse the already computed grid for footprint
        # The grid has the same shape and resolution as would be computed for footprint
        # Write the footprint with a small simplification tolerance (half a pixel in degrees)
        # For projected CRS, we'd need to convert to meters, but for now we'll use a small value
        write_footprint_vector(
            grid=grid,
            footprint_path=footprint_path,
            dst_crs=dst_crs,
            flight_line_name=meta.name,
            simplify_tolerance=0.00001  # Approximately half a pixel in degrees for typical resolution
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
    boresight_roll_deg: float = 0.0,
    boresight_pitch_deg: float = 0.0,
    boresight_yaw_deg: float = 0.0,
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
    - boresight_roll_deg: Boresight roll angle in degrees (default 0.0)
    - boresight_pitch_deg: Boresight pitch angle in degrees (default 0.0)
    - boresight_yaw_deg: Boresight yaw angle in degrees (default 0.0)
    
    Returns:
    - GroundGrid with lon/lat/alt/valid arrays for each pixel
    """
    # Fail fast with a clear message if pymap3d is missing.
    if enu2geodetic is None:
        raise ImportError(
            "pymap3d is required for georeferencing but is not installed in "
            "the QGIS Python environment. Install it (e.g. "
            "`pip install pymap3d`) and restart QGIS. "
            "Original import error: %s" % _PYMAP3D_IMPORT_ERROR
        )

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
            R = _build_rotation_matrix(roll, pitch, yaw)
            
            # Apply boresight rotation if specified
            # Convert boresight angles from degrees to radians
            if boresight_roll_deg != 0.0 or boresight_pitch_deg != 0.0 or boresight_yaw_deg != 0.0:
                boresight_roll_rad = np.radians(boresight_roll_deg)
                boresight_pitch_rad = np.radians(boresight_pitch_deg)
                boresight_yaw_rad = np.radians(boresight_yaw_deg)
                
                # Create boresight rotation matrix: R_b = Rz(yaw) @ Ry(pitch) @ Rx(roll)
                R_b = _build_rotation_matrix(boresight_roll_rad, boresight_pitch_rad, boresight_yaw_rad)
                
                # Apply boresight rotation: R_enu_from_camera = R_enu_from_imu * R_imu_from_camera
                R = R @ R_b
            
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
