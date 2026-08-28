import subprocess
import time
import sys

def trigger_production_sweep():
    print("[SYSTEM LOG] Initiating web scraping sweep for fresh credit matching targets...")
    try:
        subprocess.run([sys.executable, "run_real_scout.py"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"[SYSTEM ERROR] Sweep engine exited with error code: {e}")

if __name__ == "__main__":
    while True:
        trigger_production_sweep()
        # Pause 1 hour between automation execution sweeps
        time.sleep(3600)
