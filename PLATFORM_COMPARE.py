#!/usr/bin/env python3
"""
PLATFORM COMPARE DIAGNOSTIC

This diagnostic compares specific operations that might behave differently on Windows vs macOS.
"""

import sys
import os
from pathlib import Path
import numpy as np

def simple_print(*args):
    """Simple print that works everywhere."""
    print(*args)
    sys.stdout.flush()

def test_math_functions():
    """Test mathematical functions that might differ between platforms."""
    simple_print("=== TESTING MATH FUNCTIONS ===")
    
    # Test trigonometric functions
    angles = np.array([-0.349066, 0.0, 0.349066])  # ~±20 degrees
    sin_vals = np.sin(angles)
    cos_vals = np.cos(angles)
    
    simple_print(f"Angles: {angles}")
    simple_print(f"Sin: {sin_vals}")
    simple_print(f"Cos: {cos_vals}")
    
    # Test specific values that are used in the algorithm
    angle = np.radians(40.0 / 2.0)  # Half of 40 degrees FOV
    simple_print(f"Half FOV (20°) in radians: {angle}")
    simple_print(f"Sin of half FOV: {np.sin(angle)}")
    simple_print(f"Cos of half FOV: {np.cos(angle)}")
    
    # Test matrix operations
    simple_print("\n=== TESTING MATRIX OPERATIONS ===")
    
    # Create test rotation matrices
    def rotation_x(angle):
        cos_a = np.cos(angle)
        sin_a = np.sin(angle)
        return np.array([
            [1, 0, 0],
            [0, cos_a, -sin_a],
            [0, sin_a, cos_a]
        ])
    
    def rotation_y(angle):
        cos_a = np.cos(angle)
        sin_a = np.sin(angle)
        return np.array([
            [cos_a, 0, sin_a],
            [0, 1, 0],
            [-sin_a, 0, cos_a]
        ])
    
    def rotation_z(angle):
        cos_a = np.cos(angle)
        sin_a = np.sin(angle)
        return np.array([
            [cos_a, -sin_a, 0],
            [sin_a, cos_a, 0],
            [0, 0, 1]
        ])
    
    # Test with small angles
    roll = 0.001175
    pitch = -0.058798
    yaw = -1.582648
    
    simple_print(f"Roll: {roll}")
    simple_print(f"Pitch: {pitch}")
    simple_print(f"Yaw: {yaw}")
    
    Rx = rotation_x(roll)
    Ry = rotation_y(pitch)
    Rz = rotation_z(yaw)
    
    simple_print(f"Rx:\n{Rx}")
    simple_print(f"Ry:\n{Ry}")
    simple_print(f"Rz:\n{Rz}")
    
    # Test matrix multiplication order
    R = Rz @ Ry @ Rx
    simple_print(f"R = Rz @ Ry @ Rx:\n{R}")
    
    # Test determinant
    det = np.linalg.det(R)
    simple_print(f"Determinant: {det}")
    
    # Test with rays
    simple_print("\n=== TESTING RAY OPERATIONS ===")
    
    # Create test rays
    ray_body_x = np.array([0.0, 0.0, 0.0])
    ray_body_y = np.array([0.0, 0.342020, -0.342020])  # ~±20°
    ray_body_z = np.array([-1.0, -0.939693, -0.939693])  # cos(20°)
    
    ray_body = np.vstack([ray_body_x, ray_body_y, ray_body_z])
    simple_print(f"Ray body shape: {ray_body.shape}")
    simple_print(f"Ray body:\n{ray_body}")
    
    # Apply rotation
    ray_enu = R @ ray_body
    simple_print(f"Ray ENU shape: {ray_enu.shape}")
    simple_print(f"Ray ENU z components: {ray_enu[2, :]}")
    
    # Check downward condition
    dz = ray_enu[2, :]
    downward = dz < -1e-5
    simple_print(f"Downward check: {downward}")
    simple_print(f"Sum downward: {np.sum(downward)}")
    
    return True

def test_array_operations():
    """Test array operations that might differ between platforms."""
    simple_print("\n=== TESTING ARRAY OPERATIONS ===")
    
    # Test array creation and initialization
    size = 5
    arr1 = np.zeros(size)
    arr2 = np.ones(size)
    arr3 = np.full(size, 5.0)
    
    simple_print(f"Zeros: {arr1}")
    simple_print(f"Ones: {arr2}")
    simple_print(f"Full: {arr3}")
    
    # Test boolean operations
    condition = arr2 > 0.5
    simple_print(f"Condition: {condition}")
    simple_print(f"Sum condition: {np.sum(condition)}")
    
    # Test array assignment
    result = np.zeros(size, dtype=bool)
    result[condition] = True
    simple_print(f"Result after assignment: {result}")
    
    return True

def main():
    simple_print("=== PLATFORM COMPARE DIAGNOSTIC ===")
    
    try:
        # Test math functions
        test_math_functions()
        
        # Test array operations
        test_array_operations()
        
        simple_print("\n🎉 PLATFORM COMPARE DIAGNOSTIC COMPLETED!")
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