"""Check frozen reference and computational dependencies; print pilot fingerprints."""
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


root = Path(__file__).resolve().parent
work = root.parent
frozen = work.parent / "further-1/five-band-20261014-integrated-v1"
manifest = json.loads((frozen / "snapshots.json").read_text())["candidate"]
for name, expected in manifest.items():
    assert digest(frozen / "sources/candidate" / name) == expected, name
dependencies = (
    "radiation/radiation_mcica_acc_gpoint_sw.F90",
    "radiation/radiation_cloud_generator_acc.F90",
    "radiation/radiation_adding_ica_sw.F90",
    "radiation/radiation_two_stream.F90",
)
for name in dependencies:
    assert digest(work / name) == manifest[name], name
print(f"Verified {len(manifest)} frozen five-band files and four pilot dependencies")
for name in (
    "radiation/radiation_mcica_sw_batch_mod.F90",
    "batch-pilot/CMakeLists.txt",
    "batch-pilot/reference_sw.F90",
    "batch-pilot/test_sw_batch.F90",
    "batch-pilot/run-gpu.sbatch",
    "batch-pilot/verify_reference.py",
):
    print(digest(work / name), name)
