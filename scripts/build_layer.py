"""Build a Lambda layer directory from pinned, hashed pure-Python wheels (no compilation, reproducible).

    python scripts/build_layer.py db        # -> infra/envs/dev/.build/layers/db/python/...

Terraform zips that directory into the layer (infra/envs/dev/main.tf), so run this before plan/apply – the Makefile
targets and the CI workflow do.
"""

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "infra" / "envs" / "dev" / ".build" / "layers"


def build(name: str) -> Path:
    requirements = ROOT / "layers" / name / "requirements.txt"
    target = OUT / name / "python"
    shutil.rmtree(target.parent, ignore_errors=True)
    target.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(  # noqa: S603 – fixed arguments, hashes enforced
            [sys.executable, "-m", "pip", "download", "--quiet", "--require-hashes", "--no-deps",
             "--only-binary=:all:", "--python-version", "3.12", "-r", str(requirements), "-d", tmp],
            check=True,
        )  # fmt: skip
        wheels = sorted(Path(tmp).glob("*.whl"))
        for wheel in wheels:
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(target)  # noqa: S202 – wheels verified by hash above
    print(f"Built layer {name}: {len(wheels)} wheels -> {target.relative_to(ROOT)}")
    return target


if __name__ == "__main__":
    for layer in sys.argv[1:] or ["db"]:
        build(layer)
