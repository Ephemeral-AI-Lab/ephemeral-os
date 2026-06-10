use eos_workspace_runtime::command_session::tail_lines;

#[test]
fn tail_lines_returns_requested_suffix_without_cursor_state() {
    assert_eq!(tail_lines("a\nb\nc\n", 2), "b\nc\n");
    assert_eq!(tail_lines("a\nb\nc", 1), "c");
    assert_eq!(tail_lines("a\nb\nc", 10), "a\nb\nc");
    assert_eq!(tail_lines("", 10), "");
    assert_eq!(tail_lines("a\nb", 0), "");
}
