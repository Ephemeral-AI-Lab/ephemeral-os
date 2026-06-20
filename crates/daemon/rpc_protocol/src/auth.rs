pub const DAEMON_AUTH_FIELD: &str = "_eos_daemon_auth_token";
pub const DAEMON_FORWARD_AUTH_FIELD: &str = "_eos_daemon_forward_auth_token";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DaemonRpcAuth<'a> {
    Raw(Option<&'a str>),
    Forward(Option<&'a str>),
}
