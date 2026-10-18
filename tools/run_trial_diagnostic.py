"""Selected diagnostic entry; provider-free CLI, shared controller lifecycle."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from talkabout.trial_controller import main

if __name__ == '__main__':
    raise SystemExit(main())
