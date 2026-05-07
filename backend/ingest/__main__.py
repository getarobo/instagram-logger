"""Allow `python -m backend.ingest --first-page-only`."""

from backend.ingest.run import main

if __name__ == "__main__":
    raise SystemExit(main())
