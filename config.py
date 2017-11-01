#!/usr/bin/env python3

"""Sample configuration file for backup.py."""

# Directory pairs: a collection of (source_dir, target_dir) tuples.
# Do not mix these up!
DIR_PAIRS = [("/my/source/directory/1", "/my/target/directory/1"),
             ("/my/source/directory/2", "/my/target/directory/2")]

# Collection of file names to exclude from backup. Supports globbing.
# Case insensitive.
EXCLUDED_FILES = {"Thumbs.db",
                  ".DS_Store",
                  "~*",  # Temporary files of some applications
                  "*.swp"}  # Like Vim temporary files

# Collection of directory names to exclude from backup. Supports
# globbing. Case insensitive.
EXCLUDED_DIRS = {"__pycache__",
                 ".ipython",
                 ".git"}

# Simulate backup run without performing actual file operations. This is
# a good idea when running for the first time or after changing
# settings.
DRY_RUN = True

# Age limit for archived file versions, in days, according to their
# label timestamps. Backup deletes older archives if a trusted newer
# version exists (see TRUSTED_AGE).
# Set to None to remove age limit, keeping archives indefinitely.
MAX_AGE = 400

# Minimum age, in days, of newest version of an archived file before it
# is "trusted" enough to subject older versions to max age check.
TRUSTED_AGE = 90

# Show and log files and directories skipped during a backup run
REPORT_SKIPPED = False

# Location of the log file
LOG_FILE = "/path/to/my/backup.log"

# Keep the terminal window open after backup is done
STAY_OPEN = True

# To use different settings per computer and/or operating system:
# uncomment following block; change and extend as needed
#
# import platform
#
# computer = platform.node()
# operating_system = platform.system()
#
# if operating_system == "Windows":
#     LOG_FILE = r"C:\my\backup.log"
# else:
#     LOG_FILE = "/path/to/my/backup.log"
# if computer == "My_Work_Computer":
#     IGNORED_DIRS.add(".idea")
