use crate::ArgSpec;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OperationSpec {
    pub name: &'static str,
    pub family: &'static str,
    pub summary: &'static str,
    pub description: &'static str,
    pub args: &'static [ArgSpec],
    pub related: &'static [&'static str],
}
