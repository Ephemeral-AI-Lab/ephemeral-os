use eos_command_session::{
    utf8_consumable_prefix_len, CommandSessionConfig, CommandSessionOutput,
    CommandSessionOutputCursor,
};

#[test]
fn utf8_carry_over_excludes_split_multibyte_tail() {
    let euro = [0xE2, 0x82, 0xAC];
    let mut first = b"ab".to_vec();
    first.extend_from_slice(&euro[..1]);
    let consume = utf8_consumable_prefix_len(&first);
    assert_eq!(consume, 2);
    assert_eq!(&first[..consume], b"ab");

    let mut completed = first[consume..].to_vec();
    completed.extend_from_slice(&euro[1..]);
    assert_eq!(utf8_consumable_prefix_len(&completed), completed.len());
    assert_eq!(String::from_utf8_lossy(&completed), "\u{20AC}");
    assert_eq!(utf8_consumable_prefix_len(b"plain ascii"), 11);
}

#[test]
fn output_cursor_reads_incrementally_without_consuming_other_cursors() {
    let output = CommandSessionOutput::new(&CommandSessionConfig::default());
    let mut model_cursor = CommandSessionOutputCursor::default();
    let mut notification_cursor = CommandSessionOutputCursor::default();

    output.append("hello".to_owned());
    assert_eq!(output.read_since(&mut model_cursor, None), "hello");
    assert_eq!(output.read_since(&mut notification_cursor, None), "hello");
    assert_eq!(output.read_since(&mut model_cursor, None), "");

    output.append(" world".to_owned());
    assert_eq!(output.read_since(&mut model_cursor, None), " world");
    assert_eq!(output.read_since(&mut notification_cursor, None), " world");
}
