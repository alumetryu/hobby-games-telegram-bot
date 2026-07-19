from pathlib import Path

DATA_DIR = Path("/app/data") if Path("/app/data").exists() else Path(".")
