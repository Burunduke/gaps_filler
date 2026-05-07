#!/usr/bin/env python3
"""
WINDOWS DEBUG DIAGNOSTIC

This diagnostic is specifically designed to identify why rays don't point downward on Windows.
"""

import sys
import os
from pathlib import Path
import numpy as np

def simple_print(*args):
    """Simple print that works everywhere."""
    print(*args)
    sys.stdout.flush()

def debug_flat_ground_grid_step_by_step():
    """Debug the flat_ground_grid function step by step."""
    simple_print("=== STEP-BY-STEP DEBUG OF flat_ground_grid ===")
    
    try:
        sys.path.insert(0, "src")
        from src.models import discover_flight_line
        from src.envi_io import read_envi_header
        from src.airborne_georef import read_lcf, read_times, interpolate_poses, PushbroomSensor, sample_view_angles, _build_rotation_matrix
        
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
        
        # Process just the first line to debug
        line = 0
        simple_print(f"\n=== DEBUGGING LINE {line} ===")
        
        # Get pose for this line
        lat0 = poses.lat[line]
        lon0 = poses.lon[line]
        h0 = poses.alt[line]
        roll = poses.roll[line]
        pitch = poses.pitch[line]
        yaw = poses.yaw[line]
        
        simple_print(f"Pose: lat={lat0:.10f}, lon={lon0:.10f}, alt={h0:.6f}")
        simple_print(f"Attitude: roll={roll:.10f}, pitch={pitch:.10f}, yaw={yaw:.10f}")
        
        # Step 1: Get view angles
        simple_print(f"\n--- STEP 1: Get view angles ---")
        angles = sample_view_angles(sensor)
        simple_print(f"Angles shape: {angles.shape}")
        simple_print(f"Angles min: {np.min(angles):.10f}")
        simple_print(f"Angles max: {np.max(angles):.10f}")
        simple_print(f"Angles[0]: {angles[0]:.10f}")
        simple_print(f"Angles[-1]: {angles[-1]:.10f}")
        
        # Step 2: Create rotation matrix
        simple_print(f"\n--- STEP 2: Create rotation matrix ---")
        R = _build_rotation_matrix(roll, pitch, yaw)
        simple_print(f"Rotation matrix:\n{R}")
        det = np.linalg.det(R)
        simple_print(f"Determinant: {det:.10f}")
        
        # Step 3: Create rays in body coordinates
        simple_print(f"\n--- STEP 3: Create rays in body coordinates ---")
        ray_body_x = np.zeros(sensor.samples)
        ray_body_y = np.sin(angles)
        ray_body_z = -np.cos(angles)
        
        simple_print(f"ray_body_x shape: {ray_body_x.shape}")
        simple_print(f"ray_body_y shape: {ray_body_y.shape}")
        simple_print(f"ray_body_z shape: {ray_body_z.shape}")
        
        simple_print(f"ray_body_x[0]: {ray_body_x[0]:.10f}")
        simple_print(f"ray_body_y[0]: {ray_body_y[0]:.10f}")
        simple_print(f"ray_body_z[0]: {ray_body_z[0]:.10f}")
        
        simple_print(f"ray_body_x[-1]: {ray_body_x[-1]:.10f}")
        simple_print(f"ray_body_y[-1]: {ray_body_y[-1]:.10f}")
        simple_print(f"ray_body_z[-1]: {ray_body_z[-1]:.10f}")
        
        # Check for NaN or inf values
        simple_print(f"ray_body_x has NaN: {np.isnan(ray_body_x).any()}")
        simple_print(f"ray_body_y has NaN: {np.isnan(ray_body_y).any()}")
        simple_print(f"ray_body_z has NaN: {np.isnan(ray_body_z).any()}")
        
        simple_print(f"ray_body_x has Inf: {np.isinf(ray_body_x).any()}")
        simple_print(f"ray_body_y has Inf: {np.isinf(ray_body_y).any()}")
        simple_print(f"ray_body_z has Inf: {np.isinf(ray_body_z).any()}")
        
        # Step 4: Stack rays
        simple_print(f"\n--- STEP 4: Stack rays ---")
        ray_body = np.vstack([ray_body_x, ray_body_y, ray_body_z])
        simple_print(f"ray_body shape: {ray_body.shape}")
        simple_print(f"ray_body[:, 0]: {ray_body[:, 0]}")
        simple_print(f"ray_body[:, -1]: {ray_body[:, -1]}")
        
        # Step 5: Rotate rays to ENU
        simple_print(f"\n--- STEP 5: Rotate rays to ENU ---")
        ray_enu = R @ ray_body
        simple_print(f"ray_enu shape: {ray_enu.shape}")
        simple_print(f"ray_enu[:, 0]: {ray_enu[:, 0]}")
        simple_print(f"ray_enu[:, -1]: {ray_enu[:, -1]}")
        
        # Step 6: Extract z components
        simple_print(f"\n--- STEP 6: Extract z components ---")
        ray_u = ray_enu[2, :]
        simple_print(f"ray_u shape: {ray_u.shape}")
        simple_print(f"ray_u min: {np.min(ray_u):.10f}")
        simple_print(f"ray_u max: {np.max(ray_u):.10f}")
        simple_print(f"ray_u[0]: {ray_u[0]:.10f}")
        simple_print(f"ray_u[-1]: {ray_u[-1]:.10f}")
        
        # Check for NaN or inf values
        simple_print(f"ray_u has NaN: {np.isnan(ray_u).any()}")
        simple_print(f"ray_u has Inf: {np.isinf(ray_u).any()}")
        
        # Step 7: Check downward condition
        simple_print(f"\n--- STEP 7: Check downward condition ---")
        dz = ray_u
        downward = dz < -1e-5
        simple_print(f"dz min: {np.min(dz):.10f}")
        simple_print(f"dz max: {np.max(dz):.10f}")
        simple_print(f"Number of downward rays: {np.sum(downward)}/{len(downward)}")
        
        if np.sum(downward) == 0:
            simple_print("ERROR: NO RAYS POINT DOWNWARD!")
            
            # Detailed analysis
            simple_print(f"\n--- DETAILED ANALYSIS ---")
            
            # Check if all rays point upward
            upward = dz > 1e-5
            simple_print(f"Number of upward rays: {np.sum(upward)}/{len(upward)}")
            
            # Check for near-zero rays
            near_zero = np.abs(dz) < 1e-5
            simple_print(f"Number of near-zero rays: {np.sum(near_zero)}/{len(upward)}")
            
            # Show histogram
            simple_print(f"\nDZ value distribution:")
            hist, bins = np.histogram(dz, bins=20)
            for i in range(len(hist)):
                simple_print(f"  {bins[i]:.6f} to {bins[i+1]:.6f}: {hist[i]}")
            
            # Show some specific values
            simple_print(f"\nFirst 10 dz values: {dz[:10]}")
            simple_print(f"Last 10 dz values: {dz[-10:]}")
            
            # Check if we need to invert
            if np.sum(upward) == len(dz):
                simple_print("ALL RAYS POINT UPWARD - NEED TO INVERT!")
                ray_enu_inverted = -ray_enu
                ray_u_inverted = ray_enu_inverted[2, :]
                downward_inverted = ray_u_inverted < -1e-5
                simple_print(f"After inversion, downward rays: {np.sum(downward_inverted)}/{len(downward_inverted)}")
        
        # Step 8: Test with a few specific samples
        simple_print(f"\n--- STEP 8: Test specific samples ---")
        test_samples = [0, sensor.samples//2, sensor.samples-1]
        for i in test_samples:
            simple_print(f"Sample {i}:")
            simple_print(f"  Angle: {angles[i]:.10f}")
            simple_print(f"  Sin: {np.sin(angles[i]):.10f}")
            simple_print(f"  Cos: {np.cos(angles[i]):.10f}")
            simple_print(f"  Ray body: [{ray_body[0, i]:.10f}, {ray_body[1, i]:.10f}, {ray_body[2, i]:.10f}]")
            simple_print(f"  Ray ENU: [{ray_enu[0, i]:.10f}, {ray_enu[1, i]:.10f}, {ray_enu[2, i]:.10f}]")
            simple_print(f"  Downward: {downward[i]}")
        
        return True
        
    except Exception as e:
        simple_print(f"ERROR: {e}")
        import traceback
        simple_print(traceback.format_exc())
        return False

def main():
    simple_print("=== WINDOWS DEBUG DIAGNOSTIC ===")
    
    try:
        success = debug_flat_ground_grid_step_by_step()
        
        if success:
            simple_print("\n🎉 WINDOWS DEBUG DIAGNOSTIC COMPLETED SUCCESSFULLY!")
            return 0
        else:
            simple_print("\n💥 WINDOWS DEBUG DIAGNOSTIC FAILED!")
            return 1
        
    except Exception as e:
        simple_print(f"\n💥 ERROR: {e}")
        import traceback
        simple_print(traceback.format_exc())
        return 1

if __name__ == "__main__":
    result = main()
    simple_print(f"\nExit code: {result}")
    sys.exit(result)