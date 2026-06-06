//! Limit/offset pagination request and result envelope.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// A limit/offset page request. Construct with [`Page::new`] to clamp `limit`
/// into `1..=MAX_LIMIT`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct Page {
    /// Maximum rows to return.
    pub limit: u32,
    /// Rows to skip.
    pub offset: u32,
}

impl Page {
    /// Largest page a client may request.
    pub const MAX_LIMIT: u32 = 200;
    /// Page size used when the client supplies none.
    pub const DEFAULT_LIMIT: u32 = 50;

    /// Build a page, clamping `limit` into `1..=MAX_LIMIT`.
    #[must_use]
    pub fn new(limit: u32, offset: u32) -> Self {
        Self {
            limit: limit.clamp(1, Self::MAX_LIMIT),
            offset,
        }
    }
}

impl Default for Page {
    fn default() -> Self {
        Self {
            limit: Self::DEFAULT_LIMIT,
            offset: 0,
        }
    }
}

/// A page of results plus the total row count for the unpaginated query.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct PageResult<T> {
    /// The rows on this page.
    pub items: Vec<T>,
    /// Total rows matching the query, ignoring `limit`/`offset`.
    pub total: u64,
    /// The applied limit.
    pub limit: u32,
    /// The applied offset.
    pub offset: u32,
}
