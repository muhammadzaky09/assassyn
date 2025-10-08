#!/usr/bin/env python3

import sys
import os

# Add assassyn to path (adjust if needed)
lib_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'python/'))
sys.path.append(lib_path)

from assassyn.frontend import *
from assassyn.backend import elaborate
from assassyn import utils
import assassyn

# ============================================
# Module Definitions
# ============================================

class Multiplier(Module):
    """A simple multiplier module that takes two inputs and outputs their product"""
    
    def __init__(self):
        super().__init__(
            ports={
                'x': Port(UInt(32)),
                'y': Port(UInt(32)),
            },
        )
    
    @module.combinational
    def build(self):
        x, y = self.pop_all_ports(True)
        result = x * y
        log("Multiplier: {} * {} = {}", x, y, result[0:31])
        return result[0:31]


class DoubleValue(Downstream):
    """A downstream module that doubles a value (pure combinational logic)"""
    
    def __init__(self):
        super().__init__()
    
    @downstream.combinational
    def build(self, value: Value):
        # Use optional to provide a default value if upstream doesn't provide input
        value = value.optional(UInt(32)(0))
        doubled = value + value
        log("Downstream: {} * 2 = {}", value, doubled)
        return doubled


class Driver(Module):
    """Main driver module - acts as the entry point, runs every cycle"""
    
    def __init__(self):
        super().__init__(ports={})
    
    @module.combinational
    def build(self, multiplier: Multiplier, doubler: Downstream):
        # Create a counter register
        cnt = RegArray(UInt(32), 1)
        v = cnt[0]
        
        # Calculate next value (combinational)
        next_v = v + UInt(32)(1)
        
        # Update counter for next cycle (sequential)
        (cnt & self)[0] <= next_v
        
        # Log the current count
        log("Driver: count = {}", v)
        
        # Only call multiplier when count is less than 10
        with Condition(v < UInt(32)(10)):
            multiplier.async_called(x=v, y=UInt(32)(3))
        
        # Call downstream doubler with the counter value
        # Note: This happens in the same cycle, so it sees the current value
        doubled_result = doubler.build(v)


def check_output(raw):
    driver_count = 0
    mult_count = 0
    downstream_count = 0
    
    for line in raw.split('\n'):
        if 'Driver:' in line:
            driver_count += 1
            
        elif 'Multiplier:' in line:
            # Extract and verify multiplication
            tokens = line.split()
            x = int(tokens[-5])
            y = int(tokens[-3])
            result = int(tokens[-1])
            expected = x * y
            # Note: result is truncated to 31 bits
            assert result == (expected & 0x7FFFFFFF), \
                f"Multiplier error: {x} * {y} = {result}, expected {expected & 0x7FFFFFFF}"
            mult_count += 1
            
        elif 'Downstream:' in line:
            # Extract and verify doubling
            # Format: "... Downstream: X * 2 = Y"
            tokens = line.split()
            value = int(tokens[-5])  # First operand before '*'
            doubled = int(tokens[-1])  # Result after '='
            expected = value * 2
            assert doubled == expected, \
                f"Downstream error: {value} * 2 = {doubled}, expected {expected}"
            downstream_count += 1
    
    # Verify counts
    assert driver_count == 100, f"Expected 100 driver cycles, got {driver_count}"
    assert mult_count == 10, f"Expected 10 multiplications, got {mult_count}"
    assert downstream_count == 100, f"Expected 100 downstream calls, got {downstream_count}"
    
    print(f"   - Driver ran {driver_count} cycles")
    print(f"   - Multiplier executed {mult_count} times")
    print(f"   - Downstream executed {downstream_count} times")


# ============================================
# Main Execution
# ============================================

def main():
    
    # 1. Build the system
    print("\n[1] Building system...")
    sys = SysBuilder('simple_example')
    with sys:
        # Instantiate modules
        multiplier = Multiplier()
        multiplier.build()
        
        doubler = DoubleValue()
        
        driver = Driver()
        driver.build(multiplier, doubler)
    

    print(f"\nSystem structure:\n{sys}")
    
    # 2. Configure simulation
    config = assassyn.backend.config(
        verilog=utils.has_verilator(),
        sim_threshold=100,  # Run for 100 cycles
        idle_threshold=200,
        random=True
    )
    

    simulator_path, verilator_path = elaborate(sys, **config)
    print(f" Simulator generated at: {simulator_path}")
    
    # 4. Run simulation
    print("\n Running simulation...")
    raw = utils.run_simulator(simulator_path)
    
    print("\nsim output")
    for i, line in enumerate(raw.split('\n')):
        if line.strip():
            print(line)
    print("...")
    

    check_output(raw)
    

    if verilator_path:
        print("\n Running Verilator verification...")
        raw_verilator = utils.run_verilator(verilator_path)
        check_output(raw_verilator)
        print(" Verilator verification passed!")
   
    



if __name__ == "__main__":
    main()