/// Live + completed executions keyed by `NamespaceExecutionId`, with admission.
/// Phase 1: capacity placeholder only — the maps, id lookup, and `try_reserve`
/// land in Phase 2/3.
#[cfg_attr(not(test), allow(dead_code))]
pub(crate) struct ExecutionRegistry {
    max_active: usize,
}

#[cfg_attr(not(test), allow(dead_code))]
impl ExecutionRegistry {
    pub(crate) fn new(max_active: usize) -> Self {
        Self { max_active }
    }

    pub(crate) fn max_active(&self) -> usize {
        self.max_active
    }
}

#[cfg(test)]
mod tests {
    use super::ExecutionRegistry;

    #[test]
    fn reports_configured_capacity() {
        assert_eq!(ExecutionRegistry::new(2).max_active(), 2);
    }
}
