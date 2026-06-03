from .console import configure_utf8_stdio


configure_utf8_stdio()

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
