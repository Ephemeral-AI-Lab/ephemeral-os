use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// One XML-like context section.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ContextSection {
    /// Element tag.
    pub tag: String,
    /// Insertion-ordered attributes.
    #[serde(default)]
    pub attrs: Vec<(String, String)>,
    /// Optional text body.
    #[serde(default)]
    pub text: Option<String>,
    /// Child sections.
    #[serde(default)]
    pub children: Vec<ContextSection>,
}

impl ContextSection {
    /// Section with a tag and no content.
    #[must_use]
    pub fn new(tag: impl Into<String>) -> Self {
        Self {
            tag: tag.into(),
            attrs: Vec::new(),
            text: None,
            children: Vec::new(),
        }
    }

    /// Attach attributes.
    #[must_use]
    pub fn with_attrs(mut self, attrs: Vec<(String, String)>) -> Self {
        self.attrs = attrs;
        self
    }

    /// Attach text.
    #[must_use]
    pub fn with_text(mut self, text: impl Into<String>) -> Self {
        self.text = Some(text.into());
        self
    }

    /// Attach children.
    #[must_use]
    pub fn with_children(mut self, children: Vec<ContextSection>) -> Self {
        self.children = children;
        self
    }
}

/// Full role context packet.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct AgentContext {
    /// Top-level sections.
    pub sections: Vec<ContextSection>,
    /// Human-readable section guide shown before the directive.
    pub guidance_contents: Vec<String>,
    /// Role directive.
    pub directive: String,
    /// Explicit context limits.
    #[serde(default)]
    pub context_limits: Vec<String>,
}

/// Render a context packet into the XML-like prompt envelope.
#[must_use]
pub fn render_context_xml(context: &AgentContext) -> String {
    let root = ContextSection::new("context").with_children(context.sections.clone());
    format!("{}\n", render_section(&root))
}

/// Render role guidance from a context packet.
#[must_use]
pub fn render_task_guidance(context: &AgentContext) -> String {
    let mut parts = vec![format!(
        "What's in context:\n{}",
        context.guidance_contents.join("\n")
    )];
    if !context.context_limits.is_empty() {
        parts.push(format!(
            "Context limits:\n{}",
            context
                .context_limits
                .iter()
                .map(|item| format!("- {item}"))
                .collect::<Vec<_>>()
                .join("\n")
        ));
    }
    parts.push(format!("What to do:\n- {}", context.directive));
    parts.join("\n\n")
}

pub(crate) fn render_section(section: &ContextSection) -> String {
    let attrs = section
        .attrs
        .iter()
        .map(|(k, v)| format!(" {}=\"{}\"", escape(k), escape(v)))
        .collect::<String>();
    let mut body = Vec::new();
    if let Some(text) = &section.text {
        body.push(escape(text));
    }
    body.extend(section.children.iter().map(render_section));
    format!(
        "<{}{}>\n{}\n</{}>",
        section.tag,
        attrs,
        body.join("\n"),
        section.tag
    )
}

fn escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}
