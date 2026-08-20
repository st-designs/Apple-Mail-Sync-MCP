import sys

from . import search
from .server import main


if __name__ == "__main__":
    if "--build-index" in sys.argv:
        stats = search.build(progress=lambda m: print(m, file=sys.stderr), full="--full" in sys.argv)
        print(stats, file=sys.stderr)
    else:
        main()
