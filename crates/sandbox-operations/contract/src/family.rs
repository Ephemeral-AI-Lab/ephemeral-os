#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OperationFamilySpec {
    pub id: &'static str,
    pub title: &'static str,
    pub summary: &'static str,
    pub description: &'static str,
}
