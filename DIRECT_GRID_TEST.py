#!/usr/bin/env python3
"""
DIRECT GRID FUNCTION TEST

This diagnostic directly tests the flat_ground_grid function to see what's happening.
"""

import sys
import os
from pathlib import Path

def simple_print(*args):
    """Simple print that works everywhere."""
    print(*args)
    sys.stdout.flush()

def main():
    simple_print("=== DIRECT GRID FUNCTION TEST ===")
    
    try:
        sys.path.insert(0, "src")
        from src.models import discover_flight_line
        from src.envi_io import read_envi_header
        from src.airborne_georef import read_lcf, read_times, interpolate_poses, flat_ground_grid, PushbroomSensor
        import numpy as np
        
        # Discover flight line
        simple_print("Discovering flight line...")
        bil_path = "example/manual_Pika_L_28.bil"
        meta = discover_flight_line(bil_path)
        simple_print(f"✓ Flight line: {meta.name}")
        
        # Read data
        simple_print("\nReading data...")
        hdr = read_envi_header(meta.hdr)
        lcf = read_lcf(meta.lcf)
        times = read_times(meta.times, expected_lines=hdr.lines)
        poses = interpolate_poses(lcf, times)
        sensor = PushbroomSensor(samples=hdr.samples, fov_deg=40.0)
        
        simple_print(f"Header: {hdr.samples}x{hdr.lines}x{hdr.bands}")
        simple_print(f"Poses: {len(poses.lat)}")
        simple_print(f"Sensor: {sensor.samples} samples")
        
        # Test the flat_ground_grid function directly
        simple_print("\n=== TESTING flat_ground_grid FUNCTION ===")
        
        # First, test with a small subset to make sure it works
        simple_print("Testing with first 10 lines...")
        
        class SubsetPoses:
            def __init__(self, poses, end_line):
                self.lat = poses.lat[:end_line]
                self.lon = poses.lon[:end_line]
                self.alt = poses.alt[:end_line]
                self.roll = poses.roll[:end_line]
                self.pitch = poses.pitch[:end_line]
                self.yaw = poses.yaw[:end_line]
        
        small_poses = SubsetPoses(poses, 10)
        small_sensor = PushbroomSensor(samples=sensor.samples, fov_deg=sensor.fov_deg)
        
        try:
            small_grid = flat_ground_grid(small_poses, small_sensor, ground_alt=0.0)
            small_valid = np.sum(small_grid.valid)
            small_total = small_grid.valid.size
            simple_print(f"  Small grid (10 lines): {small_valid}/{small_total} valid ({100*small_valid/small_total:.2f}%)")
            
            if small_valid > 0:
                valid_lon = small_grid.lon[small_grid.valid]
                valid_lat = small_grid.lat[small_grid.valid]
                simple_print(f"    Lon range: {valid_lon.min():.6f} to {valid_lon.max():.6f}")
                simple_print(f"    Lat range: {valid_lat.min():.6f} to {valid_lat.max():.6f}")
        except Exception as e:
            simple_print(f"  ✗ Small grid failed: {e}")
            import traceback
            simple_print(traceback.format_exc())
        
        # Now test the full grid
        simple_print("\nTesting with full grid...")
        try:
            full_grid = flat_ground_grid(poses, sensor, ground_alt=0.0)
            full_valid = np.sum(full_grid.valid)
            full_total = full_grid.valid.size
            simple_print(f"  Full grid ({len(poses.lat)} lines): {full_valid}/{full_total} valid ({100*full_valid/full_total:.2f}%)")
            
            if full_valid > 0:
                valid_lon = full_grid.lon[full_grid.valid]
                valid_lat = full_grid.lat[full_grid.valid]
                simple_print(f"    Lon range: {valid_lon.min():.6f} to {valid_lon.max():.6f}")
                simple_print(f"    Lat range: {valid_lat.min():.6f} to {valid_lat.max():.6f}")
            else:
                simple_print("    ✗ NO VALID POINTS!")
                
                # Let's check the grid arrays themselves
                simple_print(f"    Grid lon shape: {full_grid.lon.shape}")
                simple_print(f"    Grid lat shape: {full_grid.lat.shape}")
                simple_print(f"    Grid alt shape: {full_grid.alt.shape}")
                simple_print(f"    Grid valid shape: {full_grid.valid.shape}")
                
                # Check for any NaN or inf values
                simple_print(f"    Lon has NaN: {np.isnan(full_grid.lon).any()}")
                simple_print(f"    Lon has Inf: {np.isinf(full_grid.lon).any()}")
                simple_print(f"    Lat has NaN: {np.isnan(full_grid.lat).any()}")
                simple_print(f"    Lat has Inf: {np.isinf(full_grid.lat).any()}")
                
                # Check min/max values
                simple_print(f"    Lon range: {np.nanmin(full_grid.lon):.6f} to {np.nanmax(full_grid.lon):.6f}")
                simple_print(f"    Lat range: {np.nanmin(full_grid.lat):.6f} to {np.nanmax(full_grid.lat):.6f}")
                
        except Exception as e:
            simple_print(f"  ✗ Full grid failed: {e}")
            import traceback
            simple_print(traceback.format_exc())
            
        # Let's also test with some debugging enabled
        simple_print("\n=== TESTING WITH MANUAL LOOP ===")
        simple_print("Manually processing first 5 lines to compare...")
        
        ground_alt = 0.0
        lines = len(poses.lat)
        samples = sensor.samples
        
        # Preallocate output arrays like flat_ground_grid does
        lon_grid = np.empty((5, samples), dtype=np.float64)
        lat_grid = np.empty((5, samples), dtype=np.float64)
        alt_grid = np.full((5, samples), ground_alt, dtype=np.float64)
        valid_grid = np.zeros((5, samples), dtype=bool)
        
        from src.airborne_georef import sample_view_angles, _build_rotation_matrix
        
        # Get view angles
        angles = sample_view_angles(sensor)
        ray_body_x = np.zeros(samples)
        ray_body_y = np.sin(angles)
        ray_body_z = -np.cos(angles)
        
        # Process first 5 lines manually
        for line in range(5):
            lat0 = poses.lat[line]
            lon0 = poses.lon[line]
            h0 = poses.alt[line]
            roll = poses.roll[line]
            pitch = poses.pitch[line]
            yaw = poses.yaw[line]
            
            # Create rotation matrix
            R = _build_rotation_matrix(roll, pitch, yaw)
            
            # Create rays
            ray_body = np.vstack([ray_body_x, ray_body_y, ray_body_z])
            ray_enu = R @ ray_body
            ray_u = ray_enu[2, :]
            
            # Intersect with ground
            downward = ray_u < -1e-5
            scale = np.full_like(ray_u, np.nan)
            scale[downward] = (ground_alt - h0) / ray_u[downward]
            in_front = scale > 0
            valid = downward & in_front
            valid_grid[line, :] = valid
            
            simple_print(f"  Line {line}: {np.sum(valid)}/{samples} valid")
            
            if np.sum(valid) > 0:
                east = np.full_like(scale, np.nan)
                north = np.full_like(scale, np.nan)
                up = ground_alt - h0
                east[valid] = scale[valid] * ray_enu[0, valid]
                north[valid] = scale[valid] * ray_enu[1, valid]
                
                # Convert to geodetic (import here to avoid issues)
                try:
                    from pymap3d import enu2geodetic
                    lat, lon, _ = enu2geodetic(
                        east[valid], north[valid], up,
                        lat0, lon0, h0,
                        deg=True
                    )
                    lon_grid[line, valid] = lon
                    lat_grid[line, valid] = lat
                except Exception as e:
                    simple_print(f"    Coordinate conversion failed: {e}")
                    valid_grid[line, :] = False
        
        manual_valid = np.sum(valid_grid)
        manual_total = valid_grid.size
        simple_print(f"  Manual loop result: {manual_valid}/{manual_total} valid ({100*manual_valid/manual_total:.2f}%)")
        
        simple_print("\n🎉 DIRECT GRID TEST COMPLETED!")
        return 0
        
    except Exception as e:
        simple_print(f"\n💥 ERROR: {e}")
        import traceback
        simple_print(traceback.format_exc())
        return 1

if __name__ == "__main__":
    result = main()
    simple_print(f"\nExit code: {result}")
    sys.exit(result)