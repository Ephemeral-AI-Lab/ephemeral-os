mod command;
mod framing;
mod lsp_values;
mod ops;
mod process;
mod projection;
mod responses;
mod runtime;

pub(super) use self::runtime::PyrightLspRuntime;

const FRESHNESS_ANALYZER_REFLECTED: &str = "analyzer_reflected";
const LANGUAGE_ID: &str = "python";
