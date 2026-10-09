import os
import sys

from .cli.chat import woc_chat_main
from .cli.multi import wocc_main
from .cli.single import woc_main

def main():
    prog = os.path.basename(sys.argv[0])
    argv = sys.argv[1:]
    if prog == "wocc":
        wocc_main(argv)
    elif argv and argv[0] in ("-c", "--chat"):
        woc_chat_main(argv[1:])
    else:
        woc_main(argv)

if __name__ == "__main__":
    main()
