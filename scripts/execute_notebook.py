"""Execute the audit notebook non-interactively and commit stable outputs.

The notebook only reads the machine-readable files produced by
`scripts/reproduce.py`, so running it does not recompute the audit. This wrapper
exists to keep the committed notebook byte-stable: nbformat assigns a fresh
random id to every cell on write, and nbclient records per-cell wall-clock
timings, both of which would otherwise churn the file on every execution. The
timings are also a run timestamp, which no committed artefact in this repository
carries.
"""

from __future__ import annotations

import argparse
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_NOTEBOOK = REPOSITORY_ROOT / "notebooks" / "audit.ipynb"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notebook", type=Path, default=DEFAULT_NOTEBOOK)
    args = parser.parse_args()

    import nbformat
    from nbclient import NotebookClient

    notebook = nbformat.read(args.notebook, as_version=4)
    NotebookClient(
        notebook,
        timeout=600,
        kernel_name="python3",
        record_timing=False,
        resources={"metadata": {"path": str(args.notebook.parent)}},
    ).execute()

    for index, cell in enumerate(notebook.cells):
        cell["id"] = f"cell-{index:02d}"
        cell.get("metadata", {}).pop("execution", None)
    # LF explicitly: .gitattributes normalises the notebook to LF, so writing the
    # platform newline would make a fresh checkout differ from what was executed.
    with args.notebook.open("w", encoding="utf-8", newline="\n") as handle:
        nbformat.write(notebook, handle)

    executed = sum(1 for cell in notebook.cells if cell.cell_type == "code")
    print(f"Executed {executed} code cells in {args.notebook}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
