use serde_json::Value;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;
use tokio::task::JoinHandle;

pub async fn fake_gateway(response: Value) -> (String, JoinHandle<Value>) {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind fake gateway");
    let addr = listener.local_addr().expect("fake gateway address");
    let request = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.expect("accept CLI connection");
        let (read, mut write) = stream.into_split();
        let mut line = String::new();
        BufReader::new(read)
            .read_line(&mut line)
            .await
            .expect("read CLI request");
        let request = serde_json::from_str(&line).expect("request JSON line");
        let mut response_line = serde_json::to_vec(&response).expect("response JSON");
        response_line.push(b'\n');
        write
            .write_all(&response_line)
            .await
            .expect("write gateway response");
        request
    });
    (addr.to_string(), request)
}

pub fn parse_json_line(output: &str) -> Value {
    assert!(
        output.ends_with('\n'),
        "output is not newline terminated: {output:?}"
    );
    assert_eq!(output.lines().count(), 1, "output is not one JSON line");
    serde_json::from_str(output).expect("output JSON")
}

pub fn help_operation_names(help: &str) -> Vec<&str> {
    let lines = help.lines().collect::<Vec<_>>();
    lines
        .windows(2)
        .filter_map(|pair| {
            let operation = pair[0];
            let description = pair[1];
            (operation.starts_with("  ")
                && !operation.starts_with("    ")
                && description.starts_with("    "))
            .then(|| operation.trim())
        })
        .collect()
}
