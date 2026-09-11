"""Allow `python -m paxbot` when the console script is unavailable."""
from paxbot.cli import main

raise SystemExit(main())
