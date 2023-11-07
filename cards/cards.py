# coding=utf-8

"""
Generate print-ready cards for your tabletop game

https://github.com/jhauberg/cards.py

Copyright 2015-2017 Jacob Hauberg Hansen.
License: MIT (see LICENSE)
"""

import os
import csv
import math
import shutil
import datetime

from datetime import timedelta

from cards.template import (
    Template, fill_each, fill_card, fill_index, fill_image_fields, fill_definitions,
    template_from_path, strip_styles
)

from cards.templatefield import TemplateField

from cards.autotemplate import template_from_data

from cards.column import (
    Row, get_invalid_columns, size_identifier_from_columns
)

from cards.resource import (
    get_unused_resources, get_resources_path,
    transformed_image_paths, copy_images_to_output_directory
)

from cards.constants import Columns, TemplateFields, CardSizes
from cards.warning import WarningDisplay, WarningContext

from cards.util import (
    FileWrapper, find_file_path, open_path, lower_first_row, terminal_supports_color,
    copy_file_if_necessary, create_directories_if_necessary, directory_size, pretty_size
)


def get_definitions_from_file(path: str) -> dict:
    """ Return a dict with all definitions found in file. """

    definitions = {}

    if path is not None and len(path) > 0:
        if not os.path.isfile(path):
            WarningDisplay.bad_definitions_file_error(path)
        else:
            with open(path) as data_file_raw:
                data_file = FileWrapper(data_file_raw)

                # skip the first row (column headers)
                next(data_file)

                # map all rows into key-value pairs (assume no more than 2 columns are present)
                # and skipping ignored rows
                definitions = {k: v for k, v in csv.reader(data_file)
                               if not Row.is_excluded(data_file.raw_line)}

    return definitions


def get_section_name(contexts: list) -> str:
    section_name = ''

    for context in contexts:
        if len(section_name) > 0:
            section_name += ', '

        section_name += os.path.splitext(context)[0]

    return section_name


def get_section(name: str,
                section_template: str,
                is_card_backs: bool=False,
                is_filler: bool=False) -> str:
    """ Populate a section with a name. """

    template = Template(section_template)

    section_class = 'ui-section do-not-print'

    if is_card_backs:
        section_class = '{0} {1}'.format(section_class, 'page-backs')

    if is_filler:
        section_class = '{0} {1}'.format(section_class, 'filler')

    fill_each('_section_class', section_class, template)
    fill_each('_datasource_name', name, template)

    return template.content + '\n'


def get_page(page_number: int,
             cards: str,
             page_template: str,
             section_template: str,
             contexts: list,
             is_card_backs: bool=False,
             is_filler: bool=False,
             exclude_section: bool=False) -> str:
    """ Populate a page with cards. """

    template = Template(page_template)

    page_class = 'page'

    if is_card_backs:
        page_class = '{0} {1}'.format(page_class, 'page-backs')

    if is_filler:
        page_class = '{0} {1}'.format(page_class, 'filler')

    fill_each('_page_class', page_class, template)
    fill_each(TemplateFields.PAGE_NUMBER, str(page_number), template)
    fill_each(TemplateFields.CARDS, cards, template, indenting=True)

    page = template.content + '\n'

    if not exclude_section and contexts is not None:
        section_name = get_section_name(contexts)

        if is_card_backs:
            section_name = '{0} - backs'.format(section_name)

        section = get_section(section_name, section_template, is_card_backs, is_filler)

        page = section + page

    return page


def get_sized_card(card_template: str,
                   size_class: str,
                   content: str) -> str:
    """ Populate and return a card in a given size with the specified content. """

    template = Template(card_template)

    fill_each(TemplateFields.CARD_SIZE, size_class, template)
    fill_each(TemplateFields.CARD_CONTENT, content, template, indenting=True)

    return template.content + '\n'


def get_template(template_path: str) -> (str, list):
    template, template_not_found = Template.from_path(template_path)

    if template_not_found:
        print('could not open template \'{0}\''.format(template_path))

    image_paths = fill_image_fields(template)
    image_paths = transformed_image_paths(image_paths, template.path)

    return template.content, image_paths


def get_base_path() -> str:
    """ Return the path of the actual location of the current script; i.e. the path from
        which we can reach included project resources like base templates, icons and so on.
    """

    return os.path.dirname(os.path.realpath(__file__))


def make_empty_project(in_path: str,
                       name: str=None) -> bool:
    """ Build an empty project that can be used as a starting point. """

    destination_path = in_path

    if name is not None and len(name) > 0:
        # make sure any whitespace is replaced with dashes
        name_components = name.split(' ')
        name = '-'.join(name_components).lower()

        destination_path = os.path.join(destination_path, name)

    empty_project_path = os.path.join(get_base_path(), 'templates/project')
    destination_path = os.path.join(destination_path, 'src')

    if os.path.isdir(destination_path):
        WarningDisplay.could_not_make_new_project_error(
            destination_path, already_exists=True)

        return False

    try:
        shutil.copytree(empty_project_path, destination_path)

        print('Made new project\n -> {0}\'{1}\'{2}'.format(
            WarningDisplay.apply_normal_color_underlined, destination_path,
            WarningDisplay.apply_normal_color))

        print()

        open_path(destination_path)

        return True
    except IOError as error:
        WarningDisplay.could_not_make_new_project_error(
            destination_path, reason=str(error))

    return False


def determine_ambiguous_references(columns: set, definitions: set) -> set:
    """ Return the set of reference names that exist as both a column and a definition. """

    # get the diffs between the two sets
    unambiguous_columns = columns - definitions
    unambiguous_definitions = definitions - columns

    # then, by removing all the unambiguous names, we'll be left with the ambiguous ones
    ambiguous_references = ((columns | definitions) - unambiguous_definitions) - unambiguous_columns

    return ambiguous_references


def previous_or_current_path(current_path: str, previous_path: str) -> str:
    """ Return previous path only if the current path is used as a pointer
        to the contents of the previous path.
    """

    return (previous_path
            if (current_path is not None and current_path.strip() == '^')
            else current_path)


def get_data_path_names(data_paths: list) -> (list, int):
    """ Return a list of datasource names and total number of duplicates.

        Each name is suffixed appropriately if it's a duplicate.
    """

    total_duplicates = 0
    data_path_names = []

    abs_data_paths = [os.path.abspath(data_path) for data_path in data_paths]

    for i, data_path in enumerate(abs_data_paths):
        data_path_names.append(os.path.basename(data_path))

        for j, other_data_path in reversed(list(enumerate(abs_data_paths))):
            if i == j:
                continue

            if data_path == other_data_path:
                data_path_name = os.path.basename(other_data_path)
                data_path_name = '{0} (duplicate)'.format(data_path_name)
                data_path_names.append(data_path_name)

                total_duplicates += 1

                del abs_data_paths[j]

    return data_path_names, total_duplicates


def discover_datasources(in_directory: str, except_datasource_name: str=None) -> list:
    """ Return a list of paths to any datasources in a directory. """

    return [os.path.join(in_directory, datasource) for datasource in os.listdir(in_directory)
            if datasource.endswith('.csv') and
            (except_datasource_name is None or
             (except_datasource_name is not None and datasource != except_datasource_name))]


def make(data_paths: list,
         header_path: str=None,
         definitions_path: str=None,
         output_path: str=None,
         output_filename: str=None,
         force_page_breaks: bool=False,
         should_disable_backs: bool=False,
         should_disable_page_sections: bool=False,
         default_card_size_identifier: str='standard',
         is_preview: bool=False,
         clean_unused_resources: bool=False):
    """ Build cards for all specified datasources. """

    time_started_make = datetime.datetime.now()

    datasource_count = len(data_paths)

    exclude_datasource_named = (os.path.basename(definitions_path)
                                if definitions_path is not None
                                else None)

    if datasource_count == 0:
        # attempt finding any datasources in current working directory
        data_paths = discover_datasources(in_directory='.',
                                          except_datasource_name=exclude_datasource_named)

        datasource_count = len(data_paths)
    elif datasource_count > 0:
        # determine whether any datasources point to a directory
        for i, datasource_path in enumerate(data_paths):
            if os.path.isdir(datasource_path):
                # discover any datasources within the specified directory
                discovered_datasource_paths = discover_datasources(
                    datasource_path, except_datasource_name=exclude_datasource_named)
                # replace the datasource directory with any datasources discovered within
                data_paths = data_paths[:i] + discovered_datasource_paths + data_paths[i + 1:]

        datasource_count = len(data_paths)

    if datasource_count > 0:
        data_path_names, duplicates_count = get_data_path_names(data_paths)

        duplicates = (' ({0} {1})'.format(
            duplicates_count, 'duplicate' if duplicates_count == 1 else 'duplicates')
                      if duplicates_count > 0 else '')

        print('Generating cards from {0} {1}{2}:\n {3}'.format(
            datasource_count, 'datasources' if datasource_count > 1 else 'datasource', duplicates,
            data_path_names))
        print()
    else:
        WarningDisplay.no_datasources()

        # just quit- there's nothing to do
        return

    disable_auto_templating = False

    if definitions_path is None:
        # no definitions file has been explicitly specified, so try looking for it automatically
        found, potential_definitions_path = find_file_path('definitions.csv', data_paths)

        if found and potential_definitions_path is not None:
            definitions_path = potential_definitions_path

            WarningDisplay.using_automatically_found_definitions_info(
                definitions_path)

    definitions = get_definitions_from_file(definitions_path)

    if is_preview:
        WarningDisplay.preview_enabled_info()

    # dict of all image paths discovered for each context during card generation
    context_image_paths = {}

    # some definitions are always guaranteed to be referenced,
    # if not by cards, then by the final page output
    all_referenced_definitions = {TemplateFields.TITLE,
                                  TemplateFields.DESCRIPTION,
                                  TemplateFields.COPYRIGHT,
                                  TemplateFields.AUTHOR,
                                  TemplateFields.VERSION}

    # resolve any image fields found in definitions
    image_paths_from_definitions = []

    for definition, content in definitions.items():
        # build a temporary template with the definition content
        template = Template(content)
        # fill any partial definitions, as this might reveal other stuff
        all_referenced_definitions |= fill_definitions(definitions, template)
        # fill any image fields within
        image_paths_in_definition = fill_image_fields(template)
        # store every path found
        image_paths_from_definitions.extend(image_paths_in_definition)
        # update definition with resolved content (note that we only pre-resolve image fields here
        # and any complex/partially defined image fields will not be resolved at this point)
        definitions[definition] = template.content

    if definitions_path is not None:
        image_paths_from_definitions = transformed_image_paths(image_paths_from_definitions,
                                                               definitions_path)

        context_image_paths[definitions_path] = list(set(image_paths_from_definitions))

    base_path = get_base_path()

    card_template_path = os.path.join(base_path, 'templates/base/card.html')
    card, filled_image_paths = get_template(card_template_path)

    if len(filled_image_paths) > 0:
        context_image_paths[card_template_path] = list(set(filled_image_paths))

    page_template_path = os.path.join(base_path, 'templates/base/page.html')
    page, filled_image_paths = get_template(page_template_path)

    if len(filled_image_paths) > 0:
        context_image_paths[page_template_path] = list(set(filled_image_paths))

    page_filler_template_path = os.path.join(base_path, 'templates/base/page_filler.html')
    page_filler, filled_image_paths = get_template(page_filler_template_path)

    if len(filled_image_paths) > 0:
        context_image_paths[page_filler_template_path] = list(set(filled_image_paths))

    section_template_path = os.path.join(base_path, 'templates/base/section.html')
    section, filled_image_paths = get_template(section_template_path)

    if len(filled_image_paths) > 0:
        context_image_paths[section_template_path] = list(set(filled_image_paths))

    index_template_path = os.path.join(base_path, 'templates/base/index.html')
    index, filled_image_paths = get_template(index_template_path)

    if len(filled_image_paths) > 0:
        context_image_paths[index_template_path] = list(set(filled_image_paths))

    not_found_template_path = os.path.join(base_path, 'templates/base/error/could_not_open.html')

    with open(not_found_template_path) as error_template:
        template_not_opened = error_template.read()

    no_front_template_path = os.path.join(base_path, 'templates/base/error/not_provided.html')

    with open(no_front_template_path) as error_template:
        template_not_provided = error_template.read()

    no_back_template_path = os.path.join(base_path, 'templates/base/error/back_not_provided.html')

    with open(no_back_template_path) as error_template:
        template_back_not_provided = error_template.read()

    default_card_size = CardSizes.get_card_size(default_card_size_identifier)

    if default_card_size is None:
        default_card_size = CardSizes.get_default_card_size()

        WarningDisplay.bad_card_size(
            WarningContext(), size_identifier=default_card_size_identifier)

    # buffer that will contain at most MAX_CARDS_PER_PAGE amount of cards
    cards = ''
    # buffer that will contain at most MAX_CARDS_PER_PAGE amount of card backs
    backs = ''
    # buffer of a row of backs that is filled in reverse to support double-sided printing
    backs_row = ''
    # buffer for all generated pages
    pages = ''

    embedded_styles = {}

    # incremented each time a card is generated, but reset to 0 for each page
    cards_on_page = 0
    # incremented each time a card is generated
    cards_total = 0
    # incremented each time a page is generated
    pages_total = 0
    # incremented for each unique card (i.e. not incremented for copies/duplicates)
    cards_total_unique = 0
    # holds total card counts per datasource
    cards_total_per_context = {}

    previous_card_size = None

    page_size = CardSizes.get_page_size()

    pages_contain_backs = False

    if not should_disable_backs:
        # if pages should render card backs, we need to figure out if any datasources
        # actually *do* contain specifications for card back templates
        # if any do, we need to know this beforehand to handle the synchronization issue
        # with mixing non-back and back datasources for double-sided printing
        for data_path in data_paths:
            if not os.path.isfile(data_path):
                continue

            with open(data_path) as data_file:
                # read the first line which should contain the column names
                header_line = data_file.readline()

                if Columns.TEMPLATE_BACK in header_line:
                    pages_contain_backs = True
                    # we don't need to continue; we figured out that at least one datasource
                    # should render card backs
                    break

    previous_context = None
    contexts_per_page = []

    disable_backs = should_disable_backs

    for data_path_index, data_path in enumerate(data_paths):
        # define the context as the base filename of the current data- useful when troubleshooting
        context = os.path.basename(data_path)

        card_size = default_card_size

        image_paths_from_datasource = []

        # determine whether this path leads to anything
        if not os.path.isfile(data_path):
            # if it doesn't, warn that the path to the datasource is not right
            WarningDisplay.bad_data_path_error(WarningContext(context), data_path)
            # and skip this datasource
            continue

        cards_total_per_context[context] = 0

        with open(data_path) as data_file_raw:
            # wrap the file stream to retain access to unparsed lines
            data_file = FileWrapper(data_file_raw)
            # read the csv as a dict, so that we can access each column by name
            data = csv.DictReader(lower_first_row(data_file))

            # make a list of all column names as they are (but stripped of excess whitespace)
            column_names = [column_name.strip() for column_name in data.fieldnames]

            # then determine the size identifier (if any; e.g. '@template:jumbo')
            size_identifier, stripped_column_names = size_identifier_from_columns(column_names)

            # determine whether this datasource contains invalid columns
            invalid_column_names = get_invalid_columns(stripped_column_names)

            if len(invalid_column_names) > 0:
                # warn that this datasource will be skipped
                WarningDisplay.invalid_columns_error(
                    WarningContext(context), invalid_column_names)

                continue

            # replace the column keys with stripped/parsed representations
            # (e.g. '@template:jumbo' becomes just '@template')
            data.fieldnames = stripped_column_names

            if size_identifier is not None:
                new_card_size = CardSizes.get_card_size(size_identifier)

                if new_card_size is not None:
                    card_size = new_card_size
                else:
                    WarningDisplay.bad_card_size(
                        WarningContext(context), size_identifier)

            if card_size != previous_card_size:
                if cards_on_page > 0:
                    # card sizing is different for this datasource, so any remaining cards
                    # must be added to a new page at this point
                    pages += get_page(pages_total + 1, cards, page, section, contexts_per_page,
                                      exclude_section=should_disable_page_sections)
                    pages_total += 1

                    if not disable_backs:
                        # using the last value of cards_per_row
                        cards_on_last_row = cards_on_page % cards_per_row

                        if cards_on_last_row is not 0:
                            # less than MAX_CARDS_PER_ROW cards were added to the current line, so
                            # we have to add additional blank filler cards to ensure correct layout
                            remaining_backs = cards_per_row - cards_on_last_row

                            while remaining_backs > 0:
                                # keep adding empty filler card backs until we've filled a row
                                backs_row = empty_back + backs_row

                                remaining_backs -= 1

                        backs += backs_row

                        backs_row = ''

                        # fill another page with the backs
                        pages += get_page(pages_total + 1, backs, page, section, contexts_per_page,
                                          is_card_backs=True,
                                          exclude_section=should_disable_page_sections)
                        pages_total += 1

                        backs = ''

                    if pages_contain_backs and disable_backs:
                        # we know some pages with backs have been added, and we know that this
                        # datasource does not contain any card backs, so in order to keep
                        # two-sided printing in sync, we need to add a filler page

                        # the filler page counts as a page full of backs, but contains content
                        # that will not be printed (not even a footer)
                        pages += get_page(pages_total + 1, '', page_filler, section, contexts_per_page,
                                          is_card_backs=True, is_filler=True,
                                          exclude_section=should_disable_page_sections)
                        pages_total += 1

                        WarningDisplay.datasource_contains_filler_pages(
                            WarningContext(previous_context))

                    # reset to prepare for the next page
                    cards_on_page = 0
                    cards = ''

                # we're finished with the current datasource, and we'll be starting a new page
                # so we reset any saved contexts
                contexts_per_page = []

            contexts_per_page.append(context)

            disable_backs = should_disable_backs
            contains_filler_pages = False

            card_width, card_height = card_size.size_in_inches
            page_width, page_height = page_size.size_in_inches

            cards_per_row = math.floor(page_width / card_width)
            cards_per_column = math.floor(page_height / card_height)

            max_cards_per_page = cards_per_column * cards_per_row

            if disable_auto_templating:
                default_template_content = None
            else:
                # get a fitting template by analyzing the content of the data
                default_template_content = template_from_data(data)

                # reset the iterator
                # (note how this is done directly on the file stream; i.e. not on the wrapper)
                data_file_raw.seek(0)

                # and start over
                data = csv.DictReader(
                    lower_first_row(data_file),
                    fieldnames=stripped_column_names)

                # setting fieldnames explicitly causes the first row
                # to be treated as data, so skip it
                next(data)

            if default_template_content is None and Columns.TEMPLATE not in data.fieldnames:
                WarningDisplay.missing_default_template(
                    WarningContext(context))

            if not disable_backs and Columns.TEMPLATE_BACK in data.fieldnames:
                WarningDisplay.assume_backs_info(
                    WarningContext(context))
            else:
                # there's no back templates specified; so we can't render any
                if not disable_backs:
                    WarningDisplay.no_backs_info(
                        WarningContext(context))
                    # so disable them completely
                    disable_backs = True

            if not disable_backs:
                # empty backs may be necessary to fill in empty spots on a page to ensure
                # that the layout remains correct
                # note that we're using a completely empty template, except for the size class field
                empty_back = get_sized_card(
                    '<div class="card {0}"></div>'.format(
                        str(TemplateField(name=TemplateFields.CARD_SIZE))),
                    size_class=card_size.style, content='')

            ambiguous_references = determine_ambiguous_references(
                set(stripped_column_names),
                set(definitions.keys()))

            if len(ambiguous_references) > 0:
                WarningDisplay.potential_ambiguous_references(
                    WarningContext(context), list(ambiguous_references))

            previous_template_path = None
            previous_template_path_back = None

            row_index = 1

            for row_data in data:
                # since the column names counts as a row, and most editors
                # do not use a zero-based row index, the first row == 2
                row_index += 1

                if Row.is_excluded(data_file.raw_line):
                    # this row should be ignored - so skip and continue
                    # note that we still need to increment the row_index;
                    # otherwise row references will be offset incorrectly
                    continue

                row = Row(row_data, data_path, row_index)

                if row.is_prototype():
                    # prototype rows should be skipped, but since the skip is intentional,
                    # we should not warn about it
                    count = 0
                else:
                    count, indeterminable_count = row.determine_count()

                    if indeterminable_count:
                        WarningDisplay.indeterminable_count(
                            WarningContext(context, row_index))
                    elif count == 0:
                        # the count was explicitly set to 0, but as this might be a temporary thing,
                        # we should warn about skipping this card
                        WarningDisplay.card_was_skipped_intentionally_info(
                            WarningContext(context, row_index))

                    if count > 100:
                        # the count was unusually high; ask whether it's an error or not
                        if WarningDisplay.abort_unusually_high_count(
                                WarningContext(context, row_index), count):
                            # it was an error, so break out and continue with the next card
                            continue

                    if count > 0 and is_preview:
                        # only render 1 card unless it should be skipped
                        count = 1

                # determine which template to use for this card, if any
                template_path = row_data.get(Columns.TEMPLATE, None)
                template_path = previous_or_current_path(
                    template_path, previous_template_path)

                previous_template_path = template_path

                if not disable_backs:
                    template_path_back = row_data.get(Columns.TEMPLATE_BACK, None)
                    template_path_back = previous_or_current_path(
                        template_path_back, previous_template_path_back)

                    previous_template_path_back = template_path_back

                if count == 0:
                    # might as well move on to the next card-
                    # this card should not count towards number of unique cards either
                    # note, however, that we *do* want to register the template paths
                    continue

                resolved_template_path = None

                if template_path is not None and len(template_path) > 0:
                    template_content, not_found, resolved_template_path = template_from_path(
                        template_path, relative_to_path=data_path)

                    if not_found:
                        template_content = template_not_opened

                        WarningDisplay.bad_template_path_error(
                            WarningContext(context, row_index),
                            resolved_template_path, cards_affected=count)
                    elif len(template_content) == 0:
                        template_content = default_template_content

                        WarningDisplay.empty_template(
                            WarningContext(context, row_index),
                            resolved_template_path, cards_affected=count)
                else:
                    template_content = default_template_content

                    if template_content is not None:
                        WarningDisplay.using_auto_template(
                            WarningContext(context, row_index), cards_affected=count)

                if template_content is None:
                    template_content = template_not_provided

                    WarningDisplay.missing_template_error(
                        WarningContext(context, row_index), cards_affected=count)

                # build a template object
                # note that we apply the path *as is*; i.e. not the resolved path- this is done to
                # let any warning show the path to the template as it was defined in the data
                template_front = Template(template_content, template_path)

                embedded_styles[template_front.path] = strip_styles(template_front)

                stripped_template_content = template_front.content
                resolved_template_path_back = None

                if not disable_backs:
                    template_back_content = None

                    if template_path_back is not None and len(template_path_back) > 0:
                        template_back_content, not_found, resolved_template_path_back = template_from_path(
                            template_path_back, relative_to_path=data_path)

                        if not_found:
                            template_back_content = template_not_opened

                            WarningDisplay.bad_template_path_error(
                                WarningContext(context, row_index),
                                resolved_template_path_back, is_back=True,
                                cards_affected=count)
                        elif len(template_back_content) == 0:
                            WarningDisplay.empty_template(
                                WarningContext(context, row_index),
                                resolved_template_path_back, is_back_template=True,
                                cards_affected=count)

                    if template_back_content is None:
                        template_back_content = template_back_not_provided

                    template_back = Template(template_back_content, template_path_back)

                    embedded_styles[template_back.path] = strip_styles(template_back)

                    stripped_template_back_content = template_back.content

                # this is also the shared index for any instance of this card
                cards_total_unique += 1

                for i in range(count):
                    card_index = cards_total + 1

                    # since we're mutating the template for each card, we need to make a new one
                    template_front = Template(
                        stripped_template_content, resolved_template_path)

                    card_content, render_data = fill_card(
                        template_front,
                        row.front_row(),
                        card_index, cards_total_unique,
                        definitions)

                    if (template_front.content is not template_not_provided
                            and template_front.content is not template_not_opened):
                        if len(render_data.unused_fields) > 0:
                            WarningDisplay.missing_fields_in_template(
                                WarningContext(context, row_index),
                                list(render_data.unused_fields),
                                cards_affected=count)

                        if len(render_data.unknown_fields) > 0:
                            WarningDisplay.unknown_fields_in_template(
                                WarningContext(context, row_index),
                                list(render_data.unknown_fields),
                                template_path,
                                cards_affected=count)

                    all_referenced_definitions |= render_data.referenced_definitions

                    embedded_styles.update(render_data.embedded_styles)

                    image_paths_from_datasource.extend(render_data.image_paths)

                    current_card = get_sized_card(
                        card, size_class=card_size.style, content=card_content)

                    cards += current_card

                    cards_on_page += 1
                    cards_total += 1

                    cards_total_per_context[context] += 1

                    if not disable_backs:
                        template_back = Template(
                            stripped_template_back_content, resolved_template_path_back)

                        back_content, render_data = fill_card(
                            template_back,
                            row.back_row(),
                            card_index, cards_total_unique,
                            definitions)

                        if (template_back.content is not template_back_not_provided
                                and template_back.content is not template_not_opened):
                            if len(render_data.unused_fields) > 0:
                                WarningDisplay.missing_fields_in_template(
                                    WarningContext(context, row_index),
                                    list(render_data.unused_fields), is_back_template=True,
                                    cards_affected=count)

                            if len(render_data.unknown_fields) > 0:
                                WarningDisplay.unknown_fields_in_template(
                                    WarningContext(context, row_index),
                                    list(render_data.unknown_fields),
                                    template_path_back,
                                    is_back_template=True,
                                    cards_affected=count)

                        all_referenced_definitions |= render_data.referenced_definitions

                        embedded_styles.update(render_data.embedded_styles)

                        image_paths_from_datasource.extend(render_data.image_paths)

                        current_card_back = get_sized_card(
                            card, size_class=card_size.style, content=back_content)

                        # prepend this card back to the current line of backs
                        backs_row = current_card_back + backs_row

                        # card backs are prepended rather than appended to
                        # ensure correct layout when printing doublesided

                        if cards_on_page % cards_per_row is 0:
                            # a line has been filled- append the 3 card backs
                            # to the page in the right order
                            backs += backs_row

                            # reset to prepare for the next line
                            backs_row = ''

                    if cards_on_page == max_cards_per_page:
                        # add another page full of cards
                        pages += get_page(pages_total + 1, cards, page, section, contexts_per_page,
                                          exclude_section=should_disable_page_sections)
                        pages_total += 1

                        if not disable_backs:
                            # and one full of backs
                            pages += get_page(pages_total + 1, backs, page, section,
                                              contexts_per_page, is_card_backs=True,
                                              exclude_section=should_disable_page_sections)
                            pages_total += 1

                            # reset to prepare for the next page
                            backs = ''

                        if pages_contain_backs and disable_backs:
                            pages += get_page(pages_total + 1, '', page_filler, section,
                                              contexts_per_page, is_card_backs=True, is_filler=True,
                                              exclude_section=should_disable_page_sections)
                            pages_total += 1

                            contains_filler_pages = True

                        # reset to prepare for the next page
                        cards_on_page = 0
                        cards = ''

                        # we're not necesarilly done with the current context, but any other context
                        # should be cleared at this point
                        contexts_per_page = [context]

        if (force_page_breaks or data_path is data_paths[-1]) and cards_on_page > 0:
            # in case we're forcing pagebreaks for each datasource, or we're on the last datasource
            # and there's still cards remaining, then do a pagebreak and fill those into a new page
            pages += get_page(pages_total + 1, cards, page, section, contexts_per_page,
                              exclude_section=should_disable_page_sections)
            pages_total += 1

            if not disable_backs:
                cards_on_last_row = cards_on_page % cards_per_row

                if cards_on_last_row is not 0:
                    # less than MAX_CARDS_PER_ROW cards were added to the current line,
                    # so we have to add additional blank filler cards to ensure a correct layout

                    remaining_backs = cards_per_row - cards_on_last_row

                    while remaining_backs > 0:
                        # keep adding empty filler card backs until we've filled a row
                        backs_row = empty_back + backs_row

                        remaining_backs -= 1

                backs += backs_row

                backs_row = ''

                # fill another page with the backs
                pages += get_page(pages_total + 1, backs, page, section, contexts_per_page,
                                  is_card_backs=True,
                                  exclude_section=should_disable_page_sections)
                pages_total += 1

                backs = ''

            if pages_contain_backs and disable_backs:
                pages += get_page(pages_total + 1, '', page_filler, section, contexts_per_page,
                                  is_card_backs=True, is_filler=True,
                                  exclude_section=should_disable_page_sections)
                pages_total += 1

                contains_filler_pages = True

            # reset to prepare for the next page
            cards_on_page = 0
            cards = ''

            # we're finished with this context
            contexts_per_page = []

        if contains_filler_pages:
            WarningDisplay.datasource_contains_filler_pages(
                WarningContext(context))

        # temporary solution involving creating new Template object only used for fill_each,
        # could be prettier; refactor as part of #34
        pages_template = Template(pages)

        fill_each(TemplateFields.CARDS_TOTAL_IN_CONTEXT,
                  str(cards_total_per_context[context]),
                  pages_template)

        pages = pages_template.content

        # store the card size that was just used, so we can determine
        # whether or not the size changes for the next datasource
        previous_card_size = card_size

        # ensure there are no duplicate image paths, since that would just
        # cause unnecessary copy operations
        context_image_paths[data_path] = list(set(image_paths_from_datasource))

        previous_context = context

    # determine unused definitions, if any
    unused_definitions = list(set(definitions.keys()) - all_referenced_definitions)

    if len(unused_definitions) > 0:
        WarningDisplay.unused_definitions(unused_definitions)

    if output_path is None:
        # output to current working directory unless otherwise specified
        output_path = ''

    output_directory_name = 'generated'

    # construct the final output path
    output_path = os.path.join(output_path, output_directory_name)

    # ensure all directories exist or created if missing
    create_directories_if_necessary(output_path)

    output_filepath = os.path.join(output_path, output_filename)

    # begin writing pages to the output file (overwriting any existing file)
    with open(output_filepath, 'w') as result:
        styles = ''

        for template_path, style in embedded_styles.items():
            styles = styles + '\n' + style if len(styles) > 0 else style

        header = ''

        if header_path is not None:
            try:
                with open(header_path) as header_file:
                    header = header_file.read().strip()
            except IOError:
                WarningDisplay.bad_header_file_error(header_path)

        index, render_data = fill_index(
            index, styles, pages, header, pages_total, cards_total, definitions)

        if len(render_data.image_paths) > 0:
            image_paths_from_index = transformed_image_paths(render_data.image_paths,
                                                             index_template_path)
            # we assume that any leftover images would have been from a definition
            context_image_paths[index_template_path] = list(set(image_paths_from_index))

        result.write(index)

    css_path = os.path.join(output_path, 'css')
    js_path = os.path.join(output_path, 'js')

    resources_path = os.path.join(output_path, get_resources_path())

    create_directories_if_necessary(css_path)
    create_directories_if_necessary(js_path)
    create_directories_if_necessary(resources_path)

    copy_file_if_necessary(os.path.join(base_path, 'templates/base/css/cards.css'),
                           os.path.join(css_path, 'cards.css'))

    copy_file_if_necessary(os.path.join(base_path, 'templates/base/css/index.css'),
                           os.path.join(css_path, 'index.css'))

    copy_file_if_necessary(os.path.join(base_path, 'templates/base/js/index.js'),
                           os.path.join(js_path, 'index.js'))

    all_copied_image_filenames = []

    # additionally, copy all referenced images to the output directory
    for context in context_image_paths:
        image_paths = context_image_paths[context]
        image_filenames = [os.path.basename(image_path) for image_path in image_paths]

        copy_images_to_output_directory(
            image_paths, context, output_path)

        all_copied_image_filenames.extend(image_filenames)

    unused_resources, unused_resource_paths = get_unused_resources(
        output_path, all_copied_image_filenames)

    if len(unused_resources) > 0:
        if clean_unused_resources:
            for unused_resource_path in unused_resource_paths:
                os.remove(unused_resource_path)

            WarningDisplay.unused_resources_were_cleaned(
                unused_resources, in_resource_dir=resources_path)
        else:
            WarningDisplay.unused_resources(
                unused_resources, in_resource_dir=resources_path)

    output_location_message = (' -> \033[4m\'{0}\'\033[0m'.format(output_filepath)
                               if terminal_supports_color() else
                               ' -> \'{0}\''.format(output_filepath))

    # get the grammar right
    errors_or_error = 'error' if WarningDisplay.error_count == 1 else 'errors'
    warnings_or_warning = 'warning' if WarningDisplay.warning_count == 1 else 'warnings'

    warnings_and_errors_message = (' ({0} {1}, {2} {3}{4})'
                                   .format(WarningDisplay.error_count, errors_or_error,
                                           WarningDisplay.warning_count, warnings_or_warning,
                                           ('; set --verbose for more'
                                            if not WarningDisplay.is_verbose else ''))
                                   if WarningDisplay.has_encountered_errors()
                                   or WarningDisplay.has_encountered_warnings()
                                   else '')

    now = datetime.datetime.now()

    time_difference = now - time_started_make
    time_difference_in_seconds = time_difference / timedelta(seconds=1)

    if WarningDisplay.has_displayed_messages():
        # break line to separate next output
        print()

    print('[{0}] Finished in {1:.3f} seconds{2}'.format(
        '-' if not WarningDisplay.has_encountered_errors() else '!',
        time_difference_in_seconds, warnings_and_errors_message))
    print()

    # find the total size of the generated directory
    generated_directory_size = pretty_size(directory_size(output_path))

    if cards_total > 0:
        # get the grammar right
        pages_or_page = 'pages' if pages_total > 1 else 'page'
        cards_or_card = 'cards' if cards_total > 1 else 'card'

        if cards_total > cards_total_unique:
            print('Generated {0} ({1} unique) {2} on {3} {4} ({5})\n{6}'
                  .format(cards_total, cards_total_unique, cards_or_card,
                          pages_total, pages_or_page,
                          generated_directory_size, output_location_message))
        else:
            print('Generated {0} {1} on {2} {3} ({4})\n{5}'
                  .format(cards_total, cards_or_card,
                          pages_total, pages_or_page,
                          generated_directory_size, output_location_message))
    else:
        print('Generated 0 cards ({0})\n{1}'
              .format(generated_directory_size, output_location_message))

    print()

    #open_path(output_path)
