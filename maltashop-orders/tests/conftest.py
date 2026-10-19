import os, tempfile, sys, pathlib
tmp = tempfile.mkdtemp()
for name in ("ORDER", "INVENTORY", "PAYMENT", "NOTIFICATION"):
    os.environ[f"{name}_DB_PATH"] = os.path.join(tmp, f"{name.lower()}.db")
os.environ["SERVICE_NAME"] = "test"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
