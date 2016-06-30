# coding=utf-8

import os
import subprocess
import itertools
import errno

from urllib.parse import urlparse


class WarningContext(object):
    def __init__(self, name: str, row_index: int=-1, card_index: int=-1):
        self.name = name
        self.row_index = row_index
        self.card_index = card_index


def warn(message: str, in_context: WarningContext=None, as_error=False) -> None:
    """ Display a command-line warning. """

    apply_red_color = '\033[31m'
    apply_yellow_color = '\033[33m'
    apply_normal_color = '\033[0m'

    apply_color = apply_yellow_color if not as_error else apply_red_color

    message_content = '[{0}]'.format('!' if as_error else '-')

    if in_context is not None:
        if in_context.row_index > -1:
            if in_context.card_index > -1:
                message_content = '{0} [{1}:{2}#{3}]'.format(
                    message_content, in_context.name, in_context.row_index, in_context.card_index)
            else:
                message_content = '{0} [{1}:{2}]'.format(
                    message_content, in_context.name, in_context.row_index)
        else:
            message_content = '{0} [{1}]'.format(
                message_content, in_context.name)

    message_content = message_content + ' ' + message

    print(apply_color + message_content + apply_normal_color)


def most_common(objects: list) -> object:
    """ Returns the object that occurs most frequently in a list of objects. """

    return max(set(objects), key=objects.count)


def lower_first_row(rows):
    """ Returns rows where the first row is all lower-case. """

    return itertools.chain([next(rows).lower()], rows)


def is_url(url):
    return urlparse(url).scheme != ""


def open_path(path: str) -> None:
    """ Opens a path in a cross-platform manner;
        showing e.g. Finder on MacOS or Explorer on Windows
    """

    if sys.platform.startswith('darwin'):
        subprocess.call(('open', path))
    elif os.name == 'nt':
        subprocess.call(('start', path), shell=True)
    elif os.name == 'posix':
        subprocess.call(('xdg-open', path))


def find_file_path(name: str, paths: list) -> (bool, str):
    """ Look for a path with 'name' in the filename in the specified paths.

        If found, returns the first discovered path to a file containing the specified name,
        otherwise returns the first potential path to where it looked for one.
    """

    found_path = None
    first_potential_path = None

    if len(paths) > 0:
        # first look for a file simply named exactly the specified name- we'll just use
        # the first provided path and assume that this is the main directory
        path_directory = os.path.dirname(paths[0])

        potential_path = os.path.join(path_directory, name)

        if os.path.isfile(potential_path):
            # we found one
            found_path = potential_path

    if found_path is None:
        # then attempt looking for a file named like 'some_file.the-name.csv' for each
        # provided path until a file is found, if any
        for path in paths:
            path_components = os.path.splitext(path)

            potential_path = path_components[0] + '.' + name

            if first_potential_path is None:
                first_potential_path = potential_path

            if os.path.isfile(potential_path):
                # we found one
                found_path = potential_path

                break

    return ((True, found_path) if found_path is not None else
            (False, first_potential_path))


def create_missing_directories_if_necessary(path: str) -> None:
    """ Mimics the command 'mkdir -p'. """

    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise
