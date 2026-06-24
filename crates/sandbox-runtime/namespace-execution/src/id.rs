/// One namespace-execution identity: the runner `request_id`, the registry key,
/// and (wrapped as `CommandSessionId`) the public face of the command API.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct NamespaceExecutionId(pub String);
