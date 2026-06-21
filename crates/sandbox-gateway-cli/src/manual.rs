use std::fmt::Write as _;

use crate::request_builder::{
    ArgKindDocument, ArgSpecDocument, OperationCatalogDocument, OperationSpecDocument,
};

#[must_use]
pub fn render_manual(
    manager_catalog: &OperationCatalogDocument,
    runtime_catalog: Option<&OperationCatalogDocument>,
) -> String {
    let mut text = String::new();
    render_section(
        &mut text,
        "Sandbox Manager Operations",
        &manager_catalog.operations,
    );
    text.push('\n');
    match runtime_catalog {
        Some(catalog) => {
            render_section(&mut text, "Sandbox Runtime Operations", &catalog.operations);
        }
        None => {
            let _ = writeln!(text, "Sandbox Runtime Operations");
            let _ = writeln!(
                text,
                "  runtime catalog requires --sandbox-id or a default sandbox"
            );
        }
    }
    text
}

fn render_section(text: &mut String, title: &str, specs: &[OperationSpecDocument]) {
    let _ = writeln!(text, "{title}");
    for spec in specs {
        let _ = writeln!(text, "  {}", spec.name);
        let _ = writeln!(text, "    {}", spec.summary);
        if let Some(cli) = &spec.cli {
            let _ = writeln!(text, "    usage: {}", cli.usage);
        }
        for arg in &spec.args {
            render_arg(text, arg);
        }
        if let Some(cli) = &spec.cli {
            for example in &cli.examples {
                let _ = writeln!(text, "    example: {example}");
            }
        }
    }
}

fn render_arg(text: &mut String, arg: &ArgSpecDocument) {
    let required = if arg.required { "required" } else { "optional" };
    let _ = writeln!(
        text,
        "    {}: {} ({required}) - {}",
        cli_arg_name(arg),
        arg_kind_name(arg.kind),
        arg.help
    );
}

fn cli_arg_name(arg: &ArgSpecDocument) -> &str {
    arg.cli
        .as_ref()
        .and_then(|cli| cli.flag.as_deref().or(cli.positional.as_deref()))
        .unwrap_or(&arg.name)
}

const fn arg_kind_name(kind: ArgKindDocument) -> &'static str {
    match kind {
        ArgKindDocument::String => "string",
        ArgKindDocument::Integer => "integer",
        ArgKindDocument::Float => "float",
        ArgKindDocument::Path => "path",
    }
}
