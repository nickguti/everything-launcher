from spotlight.search import SearchResult, build_query, is_excluded


def test_plain_text_is_passed_through():
    assert build_query("  report  ") == "report"


def test_filters_prefix_the_query():
    assert build_query("report", "files") == "file: report"
    assert build_query("report", "folders") == "folder: report"
    assert build_query("report", "all") == "report"


def test_exclusions_become_wildcard_negations():
    query = build_query("report", "all", ["C:\\Windows\\", "\\.git\\", ""])
    assert query == 'report !"*C:\\Windows\\*" !"*\\.git\\*"'


def test_or_queries_are_grouped_so_exclusions_apply_to_all_branches():
    assert build_query("a|b", "all", ["\\x\\"]) == '<a|b> !"*\\x\\*"'


def test_is_excluded_is_a_case_insensitive_substring_match():
    assert is_excluded("C:\\Users\\me\\Project\\.GIT\\config", ["\\.git\\"])
    assert not is_excluded("C:\\Users\\me\\notes.txt", ["\\.git\\", ""])


def test_full_path_joins_folder_and_name():
    assert SearchResult("a.txt", "C:\\dir", False).full_path == "C:\\dir\\a.txt"


def test_full_path_of_a_drive_root_keeps_the_backslash():
    assert SearchResult("C:", "", True).full_path == "C:\\"
