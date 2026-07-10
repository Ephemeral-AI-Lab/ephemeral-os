#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArgKind {
    String,
    Integer,
    Float,
    Path,
    JsonArray,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ArgSpec {
    pub name: &'static str,
    pub kind: ArgKind,
    pub required: bool,
    pub help: &'static str,
    pub default: Option<&'static str>,
}

impl ArgSpec {
    #[must_use]
    pub const fn required(name: &'static str, kind: ArgKind, help: &'static str) -> Self {
        Self {
            name,
            kind,
            required: true,
            help,
            default: None,
        }
    }

    #[must_use]
    pub const fn optional(
        name: &'static str,
        kind: ArgKind,
        help: &'static str,
        default: Option<&'static str>,
    ) -> Self {
        Self {
            name,
            kind,
            required: false,
            help,
            default,
        }
    }
}
