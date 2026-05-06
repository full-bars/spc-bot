import time
import hashlib
import os

# Try to import Rust core
try:
    import spc_rust_core
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False

def main():
    # Create 10MB of random data
    data = os.urandom(10 * 1024 * 1024)
    iterations = 100
    
    print(f"Benchmarking Hashing (10MB data, {iterations} iterations)")
    print("-" * 50)
    
    # 1. Pure Python (hashlib SHA256)
    start = time.perf_counter()
    for _ in range(iterations):
        _ = hashlib.sha256(data).hexdigest()
    python_time = time.perf_counter() - start
    print(f"Python (SHA256): {python_time:.4f}s ({python_time/iterations:.6f}s per hash)")
    
    # 2. Rust (XXH3)
    if RUST_AVAILABLE:
        start = time.perf_counter()
        for _ in range(iterations):
            _ = spc_rust_core.calculate_fast_hash(data)
        rust_time = time.perf_counter() - start
        print(f"Rust (XXH3):     {rust_time:.4f}s ({rust_time/iterations:.6f}s per hash)")
        
        speedup = python_time / rust_time
        print(f"\nSpeedup: {speedup:.1f}x faster")
    else:
        print("\nRust core not available for benchmark.")

if __name__ == "__main__":
    main()
