from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from qlever.command import QleverCommand
from qlever.log import log
from qlever.util import get_total_file_size


class IndexStatsCommand(QleverCommand):
    """
    Class for executing the `index-stats` command.
    """

    def __init__(self):
        pass

    def description(self) -> str:
        return ("Breakdown of the time and space used for the index build")

    def should_have_qleverfile(self) -> bool:
        return False

    def relevant_qleverfile_arguments(self) -> dict[str: list[str]]:
        return {"data": ["name"]}

    def additional_arguments(self, subparser) -> None:
        subparser.add_argument("--only-time", action="store_true",
                               default=False,
                               help="Show only the time used")
        subparser.add_argument("--only-space", action="store_true",
                               default=False,
                               help="Show only the space used")
        subparser.add_argument("--ignore-text-index", action="store_true",
                               default=False,
                               help="Ignore the text index")

    def execute_time(self, args, log_file_name) -> bool:
        """
        Part of `execute` that shows the time used.
        """

        # Read the content of `log_file_name` into a list of lines.
        try:
            with open(log_file_name, "r") as log_file:
                lines = log_file.readlines()
        except Exception as e:
            log.error(f"Problem reading index log file {log_file_name}: {e}")
            return False
        # If there is a separate `add-text-index-log.txt` file, append those
        # lines.
        try:
            text_log_file_name = f"{args.name}.text-index-log.txt"
            if Path(text_log_file_name).exists():
                with open(text_log_file_name, "r") as text_log_file:
                    lines.extend(text_log_file.readlines())
        except Exception as e:
            log.error(f"Problem reading text index log file "
                      f"{text_log_file_name}: {e}")
            return False

        # Helper function that finds the next line matching the given `regex`,
        # starting from `current_line`, and extracts the time. Returns a tuple
        # of the time and the regex match object. If a match is found,
        # `current_line` is updated to the line after the match. Otherwise,
        # `current_line` will be one beyond the last line, unless
        # `line_is_optional` is true, in which case it will be the same as when
        # the function was entered.
        current_line = 0

        def find_next_line(regex, line_is_optional=False):
            nonlocal lines
            nonlocal current_line
            current_line_backup = current_line
            # Find starting from `current_line`.
            while current_line < len(lines):
                line = lines[current_line]
                current_line += 1
                timestamp_regex = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
                timestamp_format = "%Y-%m-%d %H:%M:%S"
                regex_match = re.search(regex, line)
                if regex_match:
                    try:
                        return datetime.strptime(
                                re.match(timestamp_regex, line).group(),
                                timestamp_format), regex_match
                    except Exception as e:
                        log.error(f"Could not parse timestamp of form "
                                  f"\"{timestamp_regex}\" from line "
                                  f" \"{line.rstrip()}\" ({e})")
            # If we get here, we did not find a matching line.
            if line_is_optional:
                current_line = current_line_backup
            return None, None

        # Find the lines matching the key_lines_regex and extract the time
        # information from them.
        overall_begin, _ = find_next_line(r"INFO:\s*Processing")
        merge_begin, _ = find_next_line(r"INFO:\s*Merging partial vocab")
        convert_begin, _ = find_next_line(r"INFO:\s*Converting triples")
        perm_begin_and_info = []
        while True:
            perm_begin, _ = find_next_line(r"INFO:\s*Creating a pair", True)
            if perm_begin is None:
                break
            _, perm_info = find_next_line(r"INFO:\s*Writing meta data for"
                                          r" ([A-Z]+ and [A-Z]+)", True)
            # if perm_info is None:
            #     break
            perm_begin_and_info.append((perm_begin, perm_info))
        convert_end = (perm_begin_and_info[0][0] if
                       len(perm_begin_and_info) > 0 else None)
        normal_end, _ = find_next_line(r"INFO:\s*Index build completed")
        text_begin, _ = find_next_line(r"INFO:\s*Adding text index", True)
        text_end, _ = find_next_line(r"INFO:\s*Text index build comp", True)
        if args.ignore_text_index:
            text_begin = text_end = None
        # print("DEBUG:", len(perm_begin_and_info), perm_begin_and_info)
        # print("DEBUG:", overall_begin)
        # print("DEBUG:", normal_end)

        # Check whether at least the first phase is done.
        if overall_begin is None:
            log.error("Missing line that index build has started")
            return False
        if overall_begin and not merge_begin:
            log.error("According to the log file, the index build "
                      "has started, but is still in its first "
                      "phase (parsing the input)")
            return False

        # Helper function that shows the duration for a phase (if the start and
        # end timestamps are available).
        def show_duration(heading, start_end_pairs):
            nonlocal unit
            num_start_end_pairs = 0
            diff_seconds = 0
            for start, end in start_end_pairs:
                if start and end:
                    diff_seconds += (end - start).total_seconds()
                    num_start_end_pairs += 1
            if num_start_end_pairs > 0:
                if unit == "h":
                    diff = diff_seconds / 3600
                elif unit == "min":
                    diff = diff_seconds / 60
                else:
                    diff = diff_seconds
                log.info(f"{heading:<21} : {diff:>6.1f} {unit}")

        # Get the times of the various phases (hours or minutes, depending on
        # how long the first phase took).
        unit = "h"
        if merge_begin and overall_begin:
            parse_duration = (merge_begin - overall_begin).total_seconds()
            if parse_duration < 200:
                unit = "s"
            elif parse_duration < 3600:
                unit = "min"
        show_duration("Parse input", [(overall_begin, merge_begin)])
        show_duration("Build vocabularies", [(merge_begin, convert_begin)])
        show_duration("Convert to global IDs", [(convert_begin, convert_end)])
        for i in range(len(perm_begin_and_info)):
            perm_begin, perm_info = perm_begin_and_info[i]
            perm_end = perm_begin_and_info[i + 1][0] if i + 1 < len(
                    perm_begin_and_info) else normal_end
            perm_info_text = (perm_info.group(1).replace(" and ", " & ")
                              if perm_info else f"#{i + 1}")
            show_duration(f"Permutation {perm_info_text}",
                          [(perm_begin, perm_end)])
        show_duration("Text index", [(text_begin, text_end)])
        if text_begin and text_end:
            log.info("")
            show_duration("TOTAL time",
                          [(overall_begin, normal_end),
                           (text_begin, text_end)])
        elif normal_end:
            log.info("")
            show_duration("TOTAL time",
                          [(overall_begin, normal_end)])
        return True

    def execute_space(self, args) -> bool:
        """
        Part of `execute` that shows the space used.
        """

        # Get the sizes for the various groups of index files.
        index_size = get_total_file_size([f"{args.name}.index.*"])
        vocab_size = get_total_file_size([f"{args.name}.vocabulary.*"])
        text_size = get_total_file_size([f"{args.name}.text.*"])
        if args.ignore_text_index:
            text_size = 0
        total_size = index_size + vocab_size + text_size

        # Determing the proper unit for the size.
        unit = "GB"
        if total_size < 1e6:
            unit = "B"
        elif total_size < 1e9:
            unit = "MB"

        # Helper function for showing the size in a uniform way.
        def show_size(heading, size):
            nonlocal unit
            if unit == "GB":
                size /= 1e9
            elif unit == "MB":
                size /= 1e6
            log.info(f"{heading:<21} : {size:>6.1f} {unit}")

        show_size("Files index.*", index_size)
        show_size("Files vocabulary.*", vocab_size)
        if text_size > 0:
            show_size("Files text.*", text_size)
        log.info("")
        show_size("TOTAL size", total_size)
        return True

    def execute(self, args) -> bool:
        ret_value = args.show

        # The "time" part of the command.
        if not args.only_space:
            log_file_name = f"{args.name}.index-log.txt"
            self.show(f"Breakdown of the time used for "
                      f"building the index, based on the timestamps for key "
                      f"lines in \"{log_file_name}\"", only_show=args.show)
            if not args.show:
                ret_value &= self.execute_time(args, log_file_name)
            if not args.only_time:
                log.info("")

        # The "space" part of the command.
        if not args.only_time:
            self.show("Breakdown of the space used for building the index",
                      only_show=args.show)
            if not args.show:
                ret_value &= self.execute_space(args)

        return ret_value
