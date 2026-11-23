"""Run M3 module-E orchestration from a JSON configuration."""
import argparse
import sys
from pathlib import Path

from .pipeline import preflight, run_pipeline


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--preflight',action='store_true',help='Validate configuration and adapter availability without writing outputs')
    args=parser.parse_args()
    try:
        if args.preflight:
            preflight(args.config)
            print('Preflight OK; no outputs written.')
        else:
            print(run_pipeline(args.config))
    except (OSError,ValueError,RuntimeError) as error:
        print(str(error),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
