import sys
from pathlib import Path

# Proje kökünü import yoluna ekle: testler crawler_rehber'i doğrudan import eder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
