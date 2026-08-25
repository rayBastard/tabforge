"""PyInstaller entry point: an entry script runs outside the package, so
tabforge.desktop's relative imports need an absolute-import wrapper."""
from tabforge.desktop import main

if __name__ == "__main__":
    main()
