use super::tar_single_file;

#[test]
fn tar_single_file_builds_executable_ustar_stream() {
    let tar = tar_single_file("eosd", b"payload", 0o755).expect("tar stream");
    assert_eq!(&tar[0..4], b"eosd");
    assert_eq!(&tar[100..108], b"0000755\0");
    assert_eq!(&tar[124..136], b"00000000007\0");
    assert_eq!(tar[156], b'0');
    assert_eq!(&tar[257..263], b"ustar\0");
    assert_eq!(tar.len() % 512, 0);
}
