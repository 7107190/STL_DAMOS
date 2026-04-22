#!/usr/bin/env python3

import argparse
import sys

from scenic_custom_walker_injector import (
    add_integration_args,
    config_from_args,
    run_scenic_custom_walker_integration,
)


def main():
    parser = argparse.ArgumentParser()
    add_integration_args(parser)
    args = parser.parse_args()
    run_scenic_custom_walker_integration(config_from_args(args))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
