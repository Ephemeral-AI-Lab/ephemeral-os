//! `/forward` route parsing. Turns a request URI into a typed [`ForwardRoute`]
//! that names the workspace scope, the target port, and the remaining
//! path-and-query to forward. Target resolution and proxying read this; they
//! never re-parse the URI.

use http::Uri;
use sandbox_runtime::WorkspaceSessionId;

use super::ForwardError;

/// A parsed `/forward` route. `path_and_query` is the request target the proxy
/// forwards after the route prefix is stripped.
pub(crate) enum ForwardRoute {
    Shared {
        port: u16,
        path_and_query: String,
    },
    Isolated {
        workspace_id: WorkspaceSessionId,
        port: u16,
        path_and_query: String,
    },
}

impl ForwardRoute {
    /// Parse a `/forward/...` URI into a typed route.
    pub(crate) fn parse(uri: &Uri) -> Result<Self, ForwardError> {
        let rest = uri
            .path()
            .strip_prefix("/forward/")
            .ok_or(ForwardError::InvalidRoute)?;
        let query = uri.query();
        if let Some(shared) = rest.strip_prefix("shared/") {
            let (port, tail) = split_port_and_tail(shared)?;
            return Ok(Self::Shared {
                port,
                path_and_query: forwarded_target(tail, query),
            });
        }
        if let Some(isolated) = rest.strip_prefix("isolated=") {
            let (workspace_id, after) =
                isolated.split_once('/').ok_or(ForwardError::InvalidRoute)?;
            if workspace_id.is_empty() {
                return Err(ForwardError::InvalidRoute);
            }
            let (port, tail) = split_port_and_tail(after)?;
            return Ok(Self::Isolated {
                workspace_id: WorkspaceSessionId(workspace_id.to_owned()),
                port,
                path_and_query: forwarded_target(tail, query),
            });
        }
        Err(ForwardError::InvalidRoute)
    }

    pub(crate) fn path_and_query(&self) -> &str {
        match self {
            Self::Shared { path_and_query, .. } | Self::Isolated { path_and_query, .. } => {
                path_and_query
            }
        }
    }

    pub(crate) const fn kind(&self) -> &'static str {
        match self {
            Self::Shared { .. } => "shared",
            Self::Isolated { .. } => "isolated",
        }
    }

    pub(crate) fn workspace_id(&self) -> Option<&str> {
        match self {
            Self::Shared { .. } => None,
            Self::Isolated { workspace_id, .. } => Some(workspace_id.0.as_str()),
        }
    }

    /// The route prefix the proxy strips, reported as `X-Forwarded-Prefix`.
    pub(crate) fn prefix(&self) -> String {
        match self {
            Self::Shared { port, .. } => format!("/forward/shared/{port}"),
            Self::Isolated {
                workspace_id, port, ..
            } => format!("/forward/isolated={}/{port}", workspace_id.0),
        }
    }
}

fn split_port_and_tail(segment: &str) -> Result<(u16, &str), ForwardError> {
    let (port, tail) = segment.split_once('/').unwrap_or((segment, ""));
    Ok((parse_port(port)?, tail))
}

fn parse_port(value: &str) -> Result<u16, ForwardError> {
    match value.parse::<u16>() {
        Ok(port) if port >= 1 => Ok(port),
        _ => Err(ForwardError::InvalidPort),
    }
}

fn forwarded_target(tail: &str, query: Option<&str>) -> String {
    let mut target = String::with_capacity(1 + tail.len());
    target.push('/');
    target.push_str(tail);
    if let Some(query) = query {
        target.push('?');
        target.push_str(query);
    }
    target
}
