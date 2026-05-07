#!/usr/bin/env python3
"""
ENHANCED DIAGNOSTIC FOR WINDOWS ISSUE

This diagnostic will help identify exactly where the flat_ground_grid function fails on Windows.
"""

import sys
import os
from pathlib import Path
import numpy as np

def simple_print(*args):
    """Simple print that works everywhere."""
    print(*args)
    sys.stdout.flush()

def main():
    simple_print("=== ENHANCED DIAGNOSTIC FOR WINDOWS ISSUE ===")
    
    try:
        sys.path.insert(0, "src")
        from src.models import discover_flight_line
        from src.envi_io import read_envi_header
        from src.airborne_georef import read_lcf, read_times, interpolate_poses, flat_ground_grid, PushbroomSensor, sample_view_angles, _build_rotation_matrix
        from pymap3d import enu2geodetic
        
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
        
        # Test with detailed diagnostics
        simple_print("\n=== DETAILED DIAGNOSTICS ===")
        
        # Get view angles
        angles = sample_view_angles(sensor)
        simple_print(f"Angles shape: {angles.shape}")
        simple_print(f"Angles min/max: {np.min(angles):.6f} to {np.max(angles):.6f}")
        simple_print(f"Angles first 5: {angles[:5]}")
        simple_print(f"Angles last 5: {angles[-5:]}")
        
        # Test with a single line first
        line = 0
        simple_print(f"\n=== TESTING LINE {line} ===")
        
        # Get pose for this line
        lat0 = poses.lat[line]
        lon0 = poses.lon[line]
        h0 = poses.alt[line]
        roll = poses.roll[line]
        pitch = poses.pitch[line]
        yaw = poses.yaw[line]
        
        simple_print(f"Pose: lat={lat0:.6f}, lon={lon0:.6f}, alt={h0:.3f}")
        simple_print(f"Attitude: roll={roll:.6f}, pitch={pitch:.6f}, yaw={yaw:.6f}")
        
        # Create rotation matrix
        R = _build_rotation_matrix(roll, pitch, yaw)
        simple_print(f"Rotation matrix shape: {R.shape}")
        simple_print(f"Rotation matrix:\n{R}")
        
        # Check if matrix is valid
        det = np.linalg.det(R)
        simple_print(f"Rotation matrix determinant: {det:.6f} (should be ~1.0)")
        
        # Create rays in body coordinates
        ray_body_x = np.zeros(sensor.samples)
        ray_body_y = np.sin(angles)
        ray_body_z = -np.cos(angles)
        
        simple_print(f"Ray body shapes: x={ray_body_x.shape}, y={ray_body_y.shape}, z={ray_body_z.shape}")
        simple_print(f"Ray body z min/max: {np.min(ray_body_z):.6f} to {np.max(ray_body_z):.6f}")
        simple_print(f"Ray body z first 5: {ray_body_z[:5]}")
        simple_print(f"Ray body z last 5: {ray_body_z[-5:]}")
        
        # Rotate rays to ENU
        ray_body = np.vstack([ray_body_x, ray_body_y, ray_body_z])
        ray_enu = R @ ray_body
        
        simple_print(f"Ray ENU shape: {ray_enu.shape}")
        simple_print(f"Ray ENU z min/max: {np.min(ray_enu[2, :]):.6f} to {np.max(ray_enu[2, :]):.6f}")
        simple_print(f"Ray ENU z first 5: {ray_enu[2, :5]}")
        simple_print(f"Ray ENU z last 5: {ray_enu[2, -5:]}")
        
        # Check downward condition
        dz = ray_enu[2, :]
        downward = dz < -1e-5
        simple_print(f"Downward check: {np.sum(downward)}/{len(downward)} rays point downward")
        
        if np.sum(downward) == 0:
            simple_print("ERROR: NO RAYS POINT DOWNWARD!")
            # Check if all rays point upward
            upward = dz > 1e-5
            simple_print(f"Upward check: {np.sum(upward)}/{len(upward)} rays point upward")
            
            # Check for exact zeros or very small values
            zero = np.abs(dz) < 1e-10
            simple_print(f"Near zero check: {np.sum(zero)}/{len(zero)} rays have near-zero z-component")
            
            # Show histogram of dz values
            simple_print(f"DZ histogram:")
            hist, bins = np.histogram(dz, bins=10)
            for i in range(len(hist)):
                simple_print(f"  {bins[i]:.6f} to {bins[i+1]:.6f}: {hist[i]}")
        
        # Test the full function
        simple_print("\n=== TESTING FULL FUNCTION ===")
        try:
            full_grid = flat_ground_grid(poses, sensor, ground_alt=0.0)
            full_valid = np.sum(full_grid.valid)
            full_total = full_grid.valid.size
            simple_print(f"Full grid result: {full_valid}/{full_total} valid ({100*full_valid/full_total:.2f}%)")
            
            if full_valid > 0:
                valid_lon = full_grid.lon[full_grid.valid]
                valid_lat = full_grid.lat[full_grid.valid]
                simple_print(f"Valid lon range: {np.min(valid_lon):.6f} to {np.max(valid_lon):.6f}")
                simple_print(f"Valid lat range: {np.min(valid_lat):.6f} to {np.max(valid_lat):.6f}")
            else:
                simple_print("ERROR: NO VALID POINTS IN FULL GRID!")
                
                # Check grid arrays
                simple_print(f"Grid lon shape: {full_grid.lon.shape}")
                simple_print(f"Grid lat shape: {full_grid.lat.shape}")
                simple_print(f"Grid valid shape: {full_grid.valid.shape}")
                
                # Check for NaN or inf values
                simple_print(f"Lon has NaN: {np.isnan(full_grid.lon).any()}")
                simple_print(f"Lon has Inf: {np.isinf(full_grid.lon).any()}")
                simple_print(f"Lat has NaN: {np.isnan(full_grid.lat).any()}")
                simple_print(f"Lat has Inf: {np.isinf(full_grid.lat).any()}")
                
        except Exception as e:
            simple_print(f"ERROR in full function: {e}")
            import traceback
            simple_print(traceback.format_exc())
        
        simple_print("\n🎉 ENHANCED DIAGNOSTIC COMPLETED!")
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