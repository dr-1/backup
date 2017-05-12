#!/usr/bin/env python3

"""Backup tool

Back up files between folders, keeping UTC-timestamp-labeled archives of
changed files.

Snapshot restoration of backups is done by restore.py.

Configuration is done in the accompanying config file. Changes in that
config will not be loaded when running the program repeatedly in
interactive mode. Restart the interpreter to reload config.
"""

import os
import time
import zipfile
import datetime
import fnmatch
import logging
import argparse

try:
    import colorama  # pip install colorama
except ImportError:
    colorama = None

import config

__author__ = "Dominik Rubo"

LABEL_SEPARATOR = "@"
LABEL_DT_FORMAT = "%Y%m%d-%H%M%SZ"
DEL_MARKER_EXT = ".deleted"

# Some common file types that are already compressed and typically do not
# compress well again, so they can be stored in uncompressed archives to
# increase performance.
_COMPRESSED_FORMAT_EXTS = {
                           # Media
                           "jpg", "jpeg", "mp3", "dng", "png", "mov", "avi",
                           "pdf",
                           # Archives
                           "zip", "7z", "gz", "bz2"}


class Archive:
    """Archive version of a file"""

    def __init__(self, path):
        """Create an Archive object by parsing a file path.

        Args:
            path: full path of file

        Archives are identified by the presence of the label separator
        character in the filename.

        Parsing separates the parts of the full path: directory,
        unlabeled filename (name of the original file), label separator,
        datetime string, label extension.

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

        The timestamp method parses the datetime string as a
        datetime.datetime object.

        """
        directory, filename = os.path.split(path)

        # Check whether path is an archive file
        if filename.count(LABEL_SEPARATOR) == 0:  # Not an archive file
            raise RuntimeError("Not an archive file: {}".format(path))

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
        """Parse the archive's label datetime string as a
        datetime.datetime object.
        """
        return datetime.datetime.strptime(self.label_datetime, LABEL_DT_FORMAT)

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
            source_path: full path of the file to be backed up
            target_dir: full path of the dir to back up to
        """

        # Get the last-modified timestamp of the source file
        try:
            timestamp_raw = os.path.getmtime(source_path)
        except FileNotFoundError:
            printlog("Broken link?\t" + source_path, level="warning")
            return
        timestamp_utc = datetime.datetime.utcfromtimestamp(timestamp_raw)
        timestamp_label = datetime.datetime.strftime(timestamp_utc,
                                                     LABEL_DT_FORMAT)

        # Build the name of the target archive file
        filename = os.path.basename(source_path)
        target_path_unlabeled = os.path.join(target_dir, filename)
        target_path = "{}@{}.zip".format(target_path_unlabeled,
                                         timestamp_label)

        # Skip archiving if the same archive already exists in the target dir
        if os.path.isfile(target_path):
            return

        # Decide whether to compress the archive file
        ext = os.path.splitext(filename)[1]
        if ext[1:].lower() in _COMPRESSED_FORMAT_EXTS:  # Without dot
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

    printlog("Backing up")
    if config.DRY_RUN:
        print("This is a dry run. File operations are only simulated.")

    # Traverse collection of dir pairs to back up
    for (source, target) in config.DIR_PAIRS:
        if not os.path.isdir(source):
            printlog("Source is not a directory: " + source, level="error")
            continue
        if not os.path.isdir(target):
            if input("\nTarget directory does not exist: {}\n"
                     "Create it (y/[n])? "
                     "".format(target)).lower().startswith("y"):
                os.makedirs(target)
            else:
                continue
        print("\nSource: {}\nTarget: {}".format(source, target))
        backup_dir(source, target)

    print()
    printlog("Done")


def backup_dir(source, target):
    """Create backups of files from a source dir to a target dir,
    creating UTC-timestamp-labeled versions of changed files.
    """

    # Aliases to distinguish similar local names more clearly
    source_main, target_main = source, target

    # Calculate modification time thresholds for deletion and trusted versions
    now = datetime.datetime.utcnow()
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
                printlog("Skipped:\t" + source_dir)
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

        else:

            # Mark deleted files
            mark_deleted(source_dir=source_dir, target_dir=target_dir)

        # Back up each file in source dir
        for filename in source_files:
            source_path = os.path.join(source_dir, filename)
            if exclude(source_path, config.EXCLUDED_FILES):
                if config.REPORT_SKIPPED:
                    printlog("Skipped:\t" + source_path)
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

    Returns: file count
    """
    print("Scanning...", end="\r")
    return sum(len(files) for (_dir, subdirs, files) in os.walk(directory))


def show_progress(processed, total, overwrite=True):
    """Show file processing progress on screen.

    Args:
        processed: number of processed files
        total: number of processed and unprocessed files
        overwrite: boolean; following output will overwrite output line
    """
    if overwrite:
        end = "\r"  # Resets the cursor to the start of the line
    else:
        end = "\n"  # New line
    print("{}/{} files ({:.0%}) processed"
          "".format(processed, total, processed / total),
          end=end)


def mark_deleted(source_dir, target_dir):
    """Mark files in target_dir whose corresponding files in source_dir
    have been deleted, moved or renamed. Marking is done by creating
    timestamp-labeled, empty ".deleted" files in target_dir.

    Args:
        source_dir: full path of dir to be backed up
        target_dir: full path of dir to back up to
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
        except RuntimeError:
            pass
    target_archives_unlabeled = {archive.unlabeled_name
                                 for archive in target_archives}
    latest_versions = {max((archive for archive in target_archives
                            if archive.unlabeled_name == unlabeled),
                       key=lambda archive: archive.timestamp)
                       for unlabeled in target_archives_unlabeled}

    # Find files whose latest archive version is not a deletion marker
    unmarked_files_unlabeled = {archive.unlabeled_name
                                for archive in latest_versions
                                if not archive.is_deletion_marker}

    # Check for deleted dirs and create markers for them
    for target_subdir in target_subdirs:
        original_dir = os.path.join(source_dir, target_subdir)
        if not os.path.isdir(original_dir):

            # Create markers for all items contained in a deleted dir
            if not config.DRY_RUN:
                mark_all_items_deleted(os.path.join(target_dir, target_subdir))
            printlog("Marked deleted:\t" + original_dir,
                     level="file operation")

    # Check for deleted files and create markers for them
    for unmarked_file_unlabeled in unmarked_files_unlabeled:
        original_file = os.path.join(source_dir, unmarked_file_unlabeled)
        if not os.path.isfile(original_file):
            check_time = datetime.datetime.utcnow()
            stamp = check_time.strftime("@{}{}".format(LABEL_DT_FORMAT,
                                                       DEL_MARKER_EXT))
            marker_file = os.path.join(target_dir,
                                       unmarked_file_unlabeled + stamp)

            # Create an empty marker file
            if not config.DRY_RUN:
                open(marker_file, "w").close()

            printlog("Marked deleted:\t" + original_file,
                     level="file operation")


def mark_all_items_deleted(directory):
    """Create markers (labeled, empty files and dirs) for all archive
    files in a directory tree, indicating that their corresponding
    source items no longer exist.

    Args:
        directory: full path
    """
    check_time = datetime.datetime.utcnow()
    stamp = check_time.strftime("@{}{}".format(LABEL_DT_FORMAT,
                                               DEL_MARKER_EXT))
    for _dir, subdirs, files in os.walk(directory):
        for file in files:
            path = os.path.join(_dir, file)
            try:
                archive = Archive(path)
            except RuntimeError:
                continue
            marker_file = archive.unlabeled_path + stamp
            open(marker_file, "w").close()


def get_archives(directory):
    """Get archives from a directory, non-recursively.

       Args:
           directory: full path
       Returns:
           set of Archive instances
    """
    archives = set()
    for item in os.listdir(directory):
        path = os.path.join(directory, item)
        try:
            archive = Archive(path)
        except RuntimeError:
            pass
        else:
            archives.add(archive)
    return archives


def get_versions(archives):
    """Group archives by their unlabeled names.

    Args:
        archives: collection of Archive instances
    Returns:
        dictionary with archives' unlabeled names as keys and sets of
            corresponding archives as values
    """
    versions = {}
    for archive in archives:
        unlabeled_name = archive.unlabeled_name
        versions[unlabeled_name] = versions.get(unlabeled_name, set())
        versions[unlabeled_name].add(archive)
    return versions


def prune_dir(directory, delete_before, trusted_before):
    """Delete obsolete archives in a dir.

    Args:
        directory: full path
        delete_before: datetime.datetime object; cut-off time for
            deletion
        trusted_before: datetime.datetime object; cut-off time for
            trusted file versions to limit deletion of old ones. Not
            used if set to None. Excludes deletion markers.
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
        if (archive.timestamp < delete_before and
                archive not in protected_versions):
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
    """Check if a file or directory should be skipped.

    Args:
        path: path of the file or dir to be checked
        excluded: collection of excluded patterns
    Returns:
        boolean
    """
    return any(fnmatch.fnmatch(path, excluded_value)
               for excluded_value in excluded)


def process_config():
    """Check and transform configuration."""

    # When running backup multiple times, parse config values only once
    if "parsed" in dir(config):
        return

    # Check that all configuration names exist
    for name in ("DIR_PAIRS", "MAX_AGE", "TRUSTED_AGE", "EXCLUDED_DIRS",
                 "EXCLUDED_FILES", "REPORT_SKIPPED", "DRY_RUN", "LOG_FILE",
                 "STAY_OPEN"):
        if name not in dir(config):
            config.complete = False
            input("Missing configuration: " + name)
    else:
        config.complete = True
    if not config.complete:
        return

    # Sanitize paths
    config.DIR_PAIRS = [(os.path.abspath(source), os.path.abspath(target))
                        for (source, target) in config.DIR_PAIRS]
    config.EXCLUDED_DIRS = ({"*{0}{1}".format(os.sep, value)  # lowest level
                             for value in config.EXCLUDED_DIRS} |
                            {"*{0}{1}{0}*".format(os.sep, value)  # subdirs
                             for value in config.EXCLUDED_DIRS})
    config.EXCLUDED_FILES = {"*{0}{1}".format(os.sep, value)
                             for value in config.EXCLUDED_FILES}

    # Parse and sanity-check day values
    config.MAX_AGE = datetime.timedelta(days=config.MAX_AGE)
    config.TRUSTED_AGE = datetime.timedelta(days=config.TRUSTED_AGE)
    if config.TRUSTED_AGE > config.MAX_AGE:
        input("Config error: TRUSTED_AGE must be less than MAX_AGE")
        return

    config.parsed = True


def parse_arguments():
    """Parse command-line arguments to override config values."""
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
        print("Overriding dry-run={} config setting".format(config.DRY_RUN))
        config.DRY_RUN = args.dry_run
    return args


def printlog(content, level="info"):
    """Print and log content.

    Using print is simpler than setting up a logging.StreamHandler with
    custom formatting to add variables to log messages.

    Args:
        content: string to print and log
        level: severity level of the message. Value can be "info", "file
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
    _output_colors = {"info": "", "file operation": "", "warning": "",
                      "error": "", "reset": ""}

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
        run_full()

        dir_processing_times.sort(key=lambda item: item[1], reverse=True)
        if config.STAY_OPEN:
            input()
