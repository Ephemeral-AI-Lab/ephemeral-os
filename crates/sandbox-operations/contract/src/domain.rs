#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OperationDomain {
    Manager,
    Runtime,
    Observability,
}

#[must_use]
pub const fn operation_domain_name(domain: OperationDomain) -> &'static str {
    match domain {
        OperationDomain::Manager => "manager",
        OperationDomain::Runtime => "runtime",
        OperationDomain::Observability => "observability",
    }
}
