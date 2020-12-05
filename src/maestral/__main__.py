# -*- coding: utf-8 -*-

# system imports
import sys

# local imports
from .cli import main


if __name__ == "__main__":
    sys.argv[0] = "maestral"
    main()
