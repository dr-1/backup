# Simple Backup Program

## Purpose and Usage

This program creates archives of all files in a given directory and saves those
archives to a target directory. Whenever files in the source change (based on
their last-modified dates), running the backup tool creates new archive
versions of those files. The first run may take a long time but subsequent runs
should be quick unless a large number of files, or large files, changed.

Versions are labeled with UTC timestamps and can be automatically deleted when
reaching a certain age, as long as a newer trusted version (based on a minimum
age) of the file exists.

The original file structure and content as it existed at a given time can be
**restored** to a new directory using restore.py.

File operations are logged in detail.

**Dry runs**, running the program without performing any file operations, can
be done through a setting or via the command line with the --dry-run option.

All settings are done in config.py.

The tool is only intended to prevent accidental data loss and does not aim for
cryptographic security.

Tested on Ubuntu-based distributions, Arch Linux, Windows 7 and 10.

Suggestions for improvement are welcome.

## Requirements

* Python >= 3.6

### Recommended

* colorama package for colored terminal output. To install, run
  `pip install colorama`.

## Automation
Backups should be automated to run periodically, for example through cron or
Windows Task Scheduler.