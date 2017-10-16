#!/usr/bin/env python3

"""Backup restoration

Restore snapshots of backups created by backup.py.
"""

import os
import datetime
import zipfile

import backup

__author__ = "Dominik Rubo"


def restore(source=None, target=None, snapshot_datetime=None):
    """Restore a snapshot of an archive directory.

    Directories without files to restore are not created.

    Args:
        source: path of the directory holding archived files. None
            prompts for user input.
        target: directory to save the snapshot to. None prompts for
            for user input.
        snapshot_datetime: datetime.datetime object; the file structure
            as it existed at this time will be restored if available.
            None prompts for user input.
    """
    if backup.config.DRY_RUN:
        print("This is a dry run. File operations are only simulated.")

    # Get source dir if not provided and check that it exists
    if source is None:
        input_source = input("Archive directory to restore: ")
        source = os.path.normpath(input_source)
    if not os.path.isdir(source):
        print("Not a directory:", source)
        return

    # Get target dir if not provided and check that it exists and is empty
    if target is None:
        input_target = input("Directory to save restored files to: ")
        target = os.path.normpath(input_target)
    if not os.path.isdir(target):
        if not input(f"Target directory does not exist:\n{target}\n"
                     "Create it ([y]/n)? ").lower().startswith("n"):
            os.makedirs(target)
        else:
            return
    elif os.listdir(target):
        print("Directory is not empty:", target)
        return

    # Get snapshot_datetime if not provided
    if snapshot_datetime is None:
        input_datetime = input("Datetime of snapshot to restore (in UTC; "
                               "format as YYYY-mm-DD HH:MM:SS): ")
        try:
            snapshot_datetime = datetime.datetime.strptime(input_datetime,
                                                           "%Y-%m-%d %H:%M:%S")
        except ValueError:
            print("Datetime format not recognized:", input_datetime)
            return

    backup.printlog(f"Restoring {source}\nto {target}")

    # Traverse and restore archive_dir
    for source_dir, source_subdirs, source_files in os.walk(source):
        relative_path = os.path.relpath(source_dir, source)
        target_dir = os.path.join(target, relative_path)
        target_dir_exists = None  # Not known yet

        # Take stock of archives in source_dir and group them by their
        # unlabeled names
        archives = backup.get_archives(source_dir)
        versions = backup.get_versions(archives)

        for unlabeled_name in versions:

            # Pick version to restore: newest version that is older than
            # snapshot_datetime
            try:
                snapshot_version = max((version
                                        for version in versions[unlabeled_name]
                                        if version.timestamp <
                                        snapshot_datetime),
                                       key=lambda version: version.timestamp)
            except ValueError:  # No such version
                continue
            if snapshot_version.is_deletion_marker:
                print("Deletion marker:", snapshot_version.path)
                continue

            # Create target dir if it does not exist yet
            if not (target_dir_exists or os.path.isdir(target_dir)):
                if not backup.config.DRY_RUN:
                    os.makedirs(target_dir)
                    target_dir_exists = True
                backup.printlog("Restored:\t" + target_dir,
                                level="file operation")

            # Extract files from archive and save them to target
            with zipfile.ZipFile(snapshot_version.path, mode="r") as zipped:
                for archive_member in zipped.namelist():
                    target_path = os.path.join(target_dir, archive_member)
                    if not backup.config.DRY_RUN:
                        try:
                            zipped.extract(archive_member, path=target_dir)
                        except FileExistsError:
                            backup.printlog(f"Could not extract {target_path} "
                                            f"from {snapshot_version.name}: "
                                            "file already exists in target",
                                            level="error")
                        else:
                            backup.printlog("Restored:\t" + target_path,
                                            level="file operation")
                    else:
                        backup.printlog("Restored:\t" + target_path,
                                        level="file operation")

    print()
    backup.printlog("Done")
    input()


if __name__ == "__main__":
    backup.process_config()
    restore()
