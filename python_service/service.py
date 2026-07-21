#!/usr/bin/env python3

from digital_twin.cli import main
from digital_twin.infrastructure.operational_error_reporting import install_unhandled_error_reporter


if __name__ == "__main__":
    install_unhandled_error_reporter("Python service process")
    raise SystemExit(main())
