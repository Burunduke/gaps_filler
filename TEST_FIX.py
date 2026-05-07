#!/usr/bin/env python3
"""
TEST FIX FOR WINDOWS ISSUE

This script tests if our fix for the Windows issue works.
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
    simple_print("=== TEST FIX FOR WINDOWS ISSUE ===")
    
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
        
        # Test the flat_ground_grid function
        simple_print("\n=== TESTING flat_ground_grid FUNCTION ===")
        
        try:
            grid = flat_ground_grid(poses, sensor, ground_alt=0.0)
            valid_count = np.sum(grid.valid)
            total_count = grid.valid.size
            simple_print(f"Grid result: {valid_count}/{total_count} valid ({100*valid_count/total_count:.2f}%)")
            
            if valid_count > 0:
                valid_lon = grid.lon[grid.valid]
                valid_lat = grid.lat[grid.valid]
                simple_print(f"Valid lon range: {np.min(valid_lon):.6f} to {np.max(valid_lon):.6f}")
                simple_print(f"Valid lat range: {np.min(valid_lat):.6f} to {np.max(valid_lat):.6f}")
                simple_print("🎉 SUCCESS: Fix works!")
                return 0
            else:
                simple_print("💥 FAILURE: Still no valid points!")
                return 1
                
        except Exception as e:
            simple_print(f"ERROR in flat_ground_grid: {e}")
            import traceback
            simple_print(traceback.format_exc())
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