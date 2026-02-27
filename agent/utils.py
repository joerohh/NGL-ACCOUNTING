"""Shared utility functions for the NGL agent."""

import os
from pathlib import Path


def strip_motw(file_path: Path) -> None:
    """Remove the Zone.Identifier alternate data stream (Mark of the Web) from a file.

    Windows adds this ADS to files downloaded from the internet, which triggers
    'this file is potentially harmful' warnings. Since our agent downloads
    legitimate invoices from QBO, we strip it automatically.
    """
    try:
        os.remove(str(file_path) + ":Zone.Identifier")
    except OSError:
        pass  # No Zone.Identifier present — nothing to do
