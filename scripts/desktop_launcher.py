"""PyInstaller entry point: an entry script runs outside the package, so
tabforge.desktop's relative imports need an absolute-import wrapper.

Also dispatches the --demucs-worker sentinel: the frozen app cannot run
`python -m demucs`, so separate_stems re-invokes this same binary and the
worker branch runs demucs (whose sys.exit stays inside this child
process, never the server)."""
import sys

if "--demucs-worker" in sys.argv:
    args = [a for a in sys.argv[1:] if a != "--demucs-worker"]
    from demucs.separate import main as demucs_main
    demucs_main(args)
    sys.exit(0)

from tabforge.desktop import main

if __name__ == "__main__":
    main()
