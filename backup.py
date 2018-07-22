#!/usr/bin/env python3

"""Backup tool.

Back up files between folders, keeping UTC-timestamp-labeled archives of
changed files.

Snapshot restoration of backups is done by restore.py.

Configuration is done in the accompanying config file. Changes in that
config will not be loaded when running the program repeatedly in
interactive mode. Restart the interpreter to reload config.
"""

import argparse
import datetime as dt
import fnmatch
import logging
import os
import time
import warnings
import zipfile

try:
    import colorama  # pip install colorama
except ImportError:
    colorama = None
    warnings.warn("colorama package not available - screen output will not be "
                  "in color.")

import config

__author__ = "Dominik Rubo"

LABEL_SEPARATOR = "@"
LABEL_DT_FORMAT = "%Y%m%d-%H%M%SZ"
DEL_MARKER_EXT = ".deleted"

# Some common file types that are already compressed and typically do not
# compress well again, so they can be stored in uncompressed archives to
# increase performance.
COMPRESSED_FORMAT_EXTS = {
                          # Media
                          "jpg", "jpeg", "mp3", "dng", "png", "mov", "avi",
                          "pdf",
                          # Archives
                          "zip", "7z", "gz", "bz2"}


class Archive:
    """Archive version of a file."""

    def __init__(self, path):
        """Create an Archive object by parsing a file path.

        Args:
            path: path of the file

        Archives are identified by the presence of the label separator
        character in the filename.

        Parsing separates the parts of the path: directory, unlabeled
        filename (name of the original file), label separator, datetime
        string, label extension.

        Example: /home/user/somefile.txt@20170410-153431Z.zip
            directory:           /home/user/
            unlabeled name:      somefile.txt
            label:               @20170410-153431Z.zip
            of which
                label separator: @
                label datetime:  20170410-153431Z
                label extension: .zip
            unlabeled path (see unlabeled_path method):
                                 /home/user/somefile.txt

        The extension distinguishes regular archives (".zip") from
        deletion markers (".deleted").

        The timestamp method parses the datetime string as a dt.datetime
        object.
        """
        directory, filename = os.path.split(path)

        # Check whether path is an archive file
        if filename.count(LABEL_SEPARATOR) == 0:  # Not an archive file
            raise ValueError("Not an archive file: " + path)

        # Assign properties if it is an archive file
        self.path = path
        self.dir = directory
        self.filename = filename

        # Separate the parts of path
        (self.unlabeled_name,
         self.label) = filename.rsplit(LABEL_SEPARATOR, maxsplit=1)
        (self.label_datetime,
         self.extension) = os.path.splitext(self.label)
        self.is_deletion_marker = self.extension == DEL_MARKER_EXT

    @property
    def timestamp(self):
        """Parse the archive's label datetime string as a dt.datetime
        object.
        """
        return dt.datetime.strptime(self.label_datetime, LABEL_DT_FORMAT)

    @property
    def unlabeled_path(self):
        """Return a compound value of the archive's directory and
        unlabeled name.
        """
        return os.path.join(self.dir, self.unlabeled_name)

    @staticmethod
    def make(source_path, target_dir):
        """Create a UTC-timestamp-labeled archive of a file.

        Args:
            source_path: path of the file to be backed up
            target_dir: path of the directory to back up to
        """

        # Get the last-modified timestamp of the source file
        try:
            timestamp_raw = os.path.getmtime(source_path)
        except FileNotFoundError:
            printlog("Broken link?\t" + source_path, level="warning")
            return
        timestamp_utc = dt.datetime.utcfromtimestamp(timestamp_raw)
        timestamp_label = dt.datetime.strftime(timestamp_utc, LABEL_DT_FORMAT)

        # Build the name of the target archive file
        filename = os.path.basename(source_path)
        target_path_unlabeled = os.path.join(target_dir, filename)
        target_path = f"{target_path_unlabeled}@{timestamp_label}.zip"

        # Skip archiving if the same archive already exists in the target dir
        if os.path.isfile(target_path):
            return

        # Decide whether to compress the archive file
        ext = os.path.splitext(filename)[1]
        if ext[1:].lower() in COMPRESSED_FORMAT_EXTS:  # Without dot
            compression = zipfile.ZIP_STORED
        else:
            compression = zipfile.ZIP_LZMA

        # Create a new archive
        if not config.DRY_RUN:
            with zipfile.ZipFile(target_path, "w",
                                 compression=compression) as archive:
                archive.write(source_path,
                              arcname=os.path.basename(source_path))

        printlog("Backed up:\t" + source_path, level="file operation")


def run_full():
    """Back up between all (source, target) pairs in config.DIR_PAIRS,
    creating UTC-timestamp-labeled versions of changed files. Delete
    versions older than config.MAX_AGE unless no version older than
    config.TRUSTED_AGE exists. The newest version is always kept.
    """
    printlog("Backing up", level="info")
    if config.DRY_RUN:
        print("This is a dry run. File operations are only simulated.")

    # Traverse collection of dir pairs to back up
    for (source, target) in config.DIR_PAIRS:
        if not os.path.isdir(source):
            printlog("Source is not a directory: " + source, level="error")
            continue
        if not os.path.isdir(target):
            if input(f"\nTarget directory does not exist: {target}\n"
                     "Create it (y/[n])? ").lower().startswith("y"):
                os.makedirs(target)
            else:
                printlog("Skipped source directory: " + source,
                         level="warning")
                continue
        print(f"\nSource: {source}\nTarget: {target}")
        backup_dir(source, target)

    print()
    printlog("Done", level="info")


def backup_dir(source, target):
    """Back up files between directories, creating UTC-timestamp-labeled
    versions of changed files.

    Args:
        source: source directory
        target: target directory
    """

    # Aliases to distinguish similar local names more clearly
    source_main, target_main = source, target

    # Calculate modification time thresholds for deletion and trusted versions
    now = dt.datetime.utcnow()
    if config.MAX_AGE is None:
        delete_before = None
    else:
        delete_before = now - config.MAX_AGE
    if config.TRUSTED_AGE is None:
        trusted_before = None
    else:
        trusted_before = now - config.TRUSTED_AGE

    # Prepare progress updates
    file_count = scan(source_main)
    processed_files_count = 0
    show_progress(processed_files_count, file_count)

    # Keep track of visited target dirs for later removal of leftover empty
    # dirs from target
    visited_target_dirs = set()

    # Traverse source tree and perform backup
    for source_dir, source_subdirs, source_files in os.walk(source_main):
        start_time = time.time()

        # Skip excluded dirs
        if exclude(source_dir, config.EXCLUDED_DIRS):
            if config.REPORT_SKIPPED:
                printlog("Skipped:\t" + source_dir, level="info")
            processed_files_count += len(source_files)
            continue

        # Build target dir name
        relative_path = os.path.relpath(source_dir, start=source_main)
        target_dir = os.path.normpath(os.path.join(target_main,
                                                   relative_path))
        visited_target_dirs.add(target_dir)

        # Create target dir if it does not exist yet
        if not os.path.isdir(target_dir):
            if not config.DRY_RUN:
                os.mkdir(target_dir)
            printlog("Created:\t" + target_dir, level="file operation")

        # Mark deleted files
        else:
            mark_deleted(source_dir=source_dir, target_dir=target_dir)

        # Back up each file in source dir
        for filename in source_files:
            source_path = os.path.join(source_dir, filename)
            if exclude(source_path, config.EXCLUDED_FILES):
                if config.REPORT_SKIPPED:
                    printlog("Skipped:\t" + source_path, level="info")
                processed_files_count += 1
                show_progress(processed_files_count, file_count)
                continue
            Archive.make(source_path, target_dir)
            processed_files_count += 1
            show_progress(processed_files_count, file_count)

        # Delete obsolete archive versions
        if config.MAX_AGE is not None:
            prune_dir(target_dir, delete_before=delete_before,
                      trusted_before=trusted_before)

        dir_processing_times.append((source_dir,
                                     round(time.time() - start_time, 2)))

    # Final status
    show_progress(processed_files_count, file_count, overwrite=False)

    # Remove empty dirs from target that have no corresponding source dir
    print("Checking for leftover directories", end="\r")
    for target_dir, subdirs, files in os.walk(target_main):
        if target_dir in visited_target_dirs or os.listdir(target_dir):
            continue
        relative_path = os.path.relpath(target_dir, start=target_main)
        source_dir = os.path.join(source_main, relative_path)
        if not os.path.isdir(source_dir):
            if not config.DRY_RUN:
                os.rmdir(target_dir)
            printlog("Deleted:\t" + target_dir, level="file operation")
            print("Checking for leftover directories", end="\r")
    print("Checking for leftover directories: done")


def scan(directory):
    """Count files in a directory tree to be processed.

    Args:
        directory: path of directory to scan

    Returns:
        File count
    """
    print("Scanning...", end="\r")
    return sum(len(files) for (_dir, subdirs, files) in os.walk(directory))


def show_progress(processed, total, overwrite=True):
    """Show file processing progress on screen.

    Args:
        processed: number of processed files
        total: number of processed and unprocessed files
        overwrite: boolean; overwrite output line with new output
    """
    if overwrite:
        end = "\r"  # Resets the cursor to the start of the line
    else:
        end = "\n"  # New line
    try:
        ratio = processed / total
    except ZeroDivisionError:
        ratio = 1
    print(f"{processed}/{total} files ({ratio:.0%}) processed", end=end)


def mark_deleted(source_dir, target_dir):
    """Mark files in target_dir whose corresponding files in source_dir
    have been deleted, moved or renamed. Marking is done by creating
    timestamp-labeled, empty ".deleted" files in target_dir.

    Args:
        source_dir: path of directory to be backed up
        target_dir: path of directory to back up to
    """

    # Take stock of files in target
    target_items = {os.path.join(target_dir, item)
                    for item in os.listdir(target_dir)}
    target_subdirs = {item for item in target_items
                      if os.path.isdir(item)}
    target_archives = set()
    for item in target_items:
        try:
            target_archives.add(Archive(item))
        except ValueError:
            pass
    latest_versions = get_latest_versions(target_archives)

    # Find files whose latest archive version is not a deletion marker
    unmarked_files_unlabeled = {archive.unlabeled_name
                                for archive in latest_versions
                                if not archive.is_deletion_marker}

    # Check for deleted dirs and create markers for them
    for target_subdir in target_subdirs:
        original_dir = os.path.join(source_dir,
                                    os.path.basename(target_subdir))
        if not os.path.isdir(original_dir):

            # Create markers for all items contained in a deleted dir
            mark_all_items_deleted(os.path.join(target_dir, target_subdir))

    # Check for deleted files and create markers for them
    for unmarked_file_unlabeled in unmarked_files_unlabeled:
        original_file = os.path.join(source_dir, unmarked_file_unlabeled)
        if not os.path.isfile(original_file):
            check_time = dt.datetime.utcnow()
            stamp = check_time.strftime("@" + LABEL_DT_FORMAT + DEL_MARKER_EXT)
            marker_file = os.path.join(target_dir,
                                       unmarked_file_unlabeled + stamp)

            # Create an empty marker file
            if not config.DRY_RUN:
                open(marker_file, "w").close()

            printlog("Marked deleted:\t" + original_file,
                     level="file operation")


def mark_all_items_deleted(directory):
    """Create markers (labeled, empty files and directories) for all
    archive files in a directory tree, indicating that their
    corresponding source items no longer exist.

    Args:
        directory: path of directory
    """
    check_time = dt.datetime.utcnow()
    stamp = check_time.strftime("@" + LABEL_DT_FORMAT + DEL_MARKER_EXT)
    for _dir, subdirs, files in os.walk(directory):

        # Skip marking if all files were already marked earlier
        archives = get_archives(_dir)
        latest_archives = get_latest_versions(archives)
        if all(archive.is_deletion_marker for archive in latest_archives):
            continue

        for file in files:
            path = os.path.join(_dir, file)
            try:
                archive = Archive(path)
            except ValueError:
                continue
            marker_file = archive.unlabeled_path + stamp
            if not config.DRY_RUN:
                open(marker_file, "w").close()
        printlog("Marked deleted:\t" + _dir, level="file operation")


def get_archives(directory):
    """Get archives from a directory, non-recursively.

    Args:
        directory: path of directory

    Returns:
        Set of Archive instances
    """
    archives = set()
    for item in os.listdir(directory):
        path = os.path.join(directory, item)
        try:
            archive = Archive(path)
        except ValueError:
            pass
        else:
            archives.add(archive)
    return archives


def get_versions(archives):
    """Group archives by their unlabeled names.

    Args:
        archives: collection of Archive instances

    Returns:
        Dictionary with archives' unlabeled names as keys and sets of
            corresponding archives as values
    """
    versions = {}
    for archive in archives:
        unlabeled_name = archive.unlabeled_name
        versions[unlabeled_name] = versions.get(unlabeled_name, set())
        versions[unlabeled_name].add(archive)
    return versions


def get_latest_versions(archives):
    """Get the latest version of each file in a group of archives.

    Args:
        archives: collection of Archive instances

    Yields:
        Next in unordered collection of latest archive versions, as an
            Archive instance
    """
    archives_unlabeled = {archive.unlabeled_name for archive in archives}
    for unlabeled in archives_unlabeled:
        latest_version = max((archive for archive in archives
                              if archive.unlabeled_name == unlabeled),
                             key=lambda archive: archive.timestamp)
        yield latest_version


def prune_dir(directory, delete_before, trusted_before):
    """Delete obsolete archives in a directory.

    Args:
        directory: path of the directory
        delete_before: dt.datetime object; cut-off time for deletion
        trusted_before: dt.datetime object; cut-off time for trusted
            file versions to limit deletion of old ones. Not used if set
            to None. Excludes deletion markers.
    """

    # Take stock of archives in directory and group them by their unlabeled
    # names
    try:
        archives = get_archives(directory)
    except FileNotFoundError:  # directory does not exist, e.g. in dry run
        return
    versions = get_versions(archives)

    # Identify trusted archive versions: versions older than config.TRUSTED_AGE
    if trusted_before is not None:
        trusted = {archive for archive in archives
                   if archive.timestamp < trusted_before}
    else:
        trusted = archives

    # Identify protected archive versions: newest trusted version
    protected_versions = set()
    for unlabeled_name in versions:
        try:
            protected_version = max((archive
                                     for archive in versions[unlabeled_name]
                                     if archive in trusted),
                                    key=lambda archive: archive.timestamp)
        except ValueError:  # Version group includes no trusted version
            pass
        else:
            protected_versions.add(protected_version)

    # Delete versions older than specified limit, except protected versions
    for archive in archives:
        if (archive.timestamp < delete_before
                and archive not in protected_versions):
            if not config.DRY_RUN:
                os.remove(archive.path)
            printlog("Pruned:\t" + archive.path, level="file operation")
            versions[archive.unlabeled_name].remove(archive)

    # Delete leftover deletion markers
    for unlabeled_name in versions:
        if all(version.is_deletion_marker
               for version in versions[unlabeled_name]):
            for marker in versions[unlabeled_name]:
                if not config.DRY_RUN:
                    os.remove(marker.path)


def exclude(path, excluded):
    """Check if a file or directory should be skipped. Globbing is
    supported. Case insensitive.

    Args:
        path: path of the file or directory to be checked
        excluded: collection of excluded patterns

    Returns:
        Boolean
    """
    return any(fnmatch.fnmatch(path, excluded_value)
               for excluded_value in excluded)


def process_config():
    """Check and transform configuration."""

    # When running backup multiple times, parse config values only once
    if "parsed" in dir(config):
        return

    # Check that all configuration names exist
    config.complete = True
    for name in ("DIR_PAIRS", "MAX_AGE", "TRUSTED_AGE", "EXCLUDED_DIRS",
                 "EXCLUDED_FILES", "REPORT_SKIPPED", "DRY_RUN", "LOG_FILE",
                 "STAY_OPEN"):
        if name not in dir(config):
            config.complete = False
            input("Missing configuration: " + name)
    if not config.complete:
        return

    # Sanitize paths
    config.DIR_PAIRS = [(os.path.abspath(source), os.path.abspath(target))
                        for (source, target) in config.DIR_PAIRS]
    config.EXCLUDED_DIRS = ({f"*{os.sep}{value}"  # lowest level
                             for value in config.EXCLUDED_DIRS}
                            | {f"*{os.sep}{value}{os.sep}*"  # subdirs
                               for value in config.EXCLUDED_DIRS})
    config.EXCLUDED_FILES = {f"*{os.sep}{value}"
                             for value in config.EXCLUDED_FILES}

    # Parse and sanity-check day values
    config.MAX_AGE = dt.timedelta(days=config.MAX_AGE)
    config.TRUSTED_AGE = dt.timedelta(days=config.TRUSTED_AGE)
    if config.TRUSTED_AGE > config.MAX_AGE:
        input("Config error: TRUSTED_AGE must be less than MAX_AGE")
        return

    config.parsed = True


def parse_arguments():
    """Parse command-line arguments to override config values.

    Returns:
        Namespace populated with arguments
    """
    argparser = argparse.ArgumentParser()
    arggroup = argparser.add_mutually_exclusive_group()
    arggroup.add_argument("--dry-run",
                          help="Override config to only simulate file "
                               "operations",
                          nargs="?", const=True)
    arggroup.add_argument("--no-dry-run",
                          help="Override config to perform file operations",
                          nargs="?", dest="dry_run", const=False)
    args = argparser.parse_args()
    if args.dry_run is not None and args.dry_run != config.DRY_RUN:
        print(f"Overriding dry-run={config.DRY_RUN} config setting")
        config.DRY_RUN = args.dry_run
    return args


def printlog(content, level):
    """Print and log content.

    Using print is simpler than setting up a logging.StreamHandler with
    custom formatting to add variables to log messages.

    Args:
        content: string to print and log
        level: severity level of the message. One of "info", "file
            operation", "warning" or "error".
    """
    dry_run_indicator = (config.DRY_RUN * (level == "file operation") *
                         "[Dry run] ")
    print_content = (_output_colors[level] + dry_run_indicator +
                     content.replace("\t", " ") + _output_colors["reset"])
    log_content = dry_run_indicator + content
    print(print_content)
    logger.info(log_content)


# Prepare logging
log_file_dir = os.path.split(config.LOG_FILE)[0]
if not os.path.isdir(log_file_dir):
    os.makedirs(log_file_dir)
logger = logging.Logger(name=__name__)
log_handler = logging.NullHandler()  # Replaced later if config is valid
logger.addHandler(log_handler)

# Prepare colored terminal output. May not work in some terminals.
if colorama is not None:
    colorama.init()
    _output_colors = {"info": "",
                      "file operation": colorama.Fore.CYAN,
                      "warning": colorama.Fore.YELLOW,
                      "error": colorama.Fore.RED,
                      "reset": colorama.Fore.RESET}
else:
    _output_colors = {level: "" for level in {"info", "file operation",
                                              "warning", "error", "reset"}}

# Processing times per dir for debugging
dir_processing_times = []

if __name__ == "__main__":

    process_config()
    if config is not None:

        # Get command-line arguments to override configuration
        args = parse_arguments()

        # Reconfigure logger
        log_handler = logging.FileHandler(filename=config.LOG_FILE)
        log_formatter = logging.Formatter(fmt="%(asctime)s\t%(message)s",
                                          datefmt="%Y-%m-%d %H:%M:%S")
        log_handler.setFormatter(log_formatter)
        logger.addHandler(log_handler)

        # Perform backup
        try:
            run_full()
        except KeyboardInterrupt:
            print()
            printlog("Interrupted by user", level="info")

        dir_processing_times.sort(key=lambda item: item[1], reverse=True)
        if config.STAY_OPEN:
            input()
