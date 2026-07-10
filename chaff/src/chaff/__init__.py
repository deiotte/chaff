"""chaff — synthetic demo data engine.

Fake signals convincing enough to work with.
Spec-driven: see chaff.spec.DatasetSpec. UI/CLI/API are all just
different ways to produce a spec; the engine does the rest.
"""

from .engine import generate_rows, run
from .spec import DatasetSpec, load_spec

__version__ = "0.1.0"
__all__ = ["DatasetSpec", "load_spec", "generate_rows", "run", "__version__"]
