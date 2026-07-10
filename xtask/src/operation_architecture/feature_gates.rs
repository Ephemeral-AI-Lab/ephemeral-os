use std::collections::BTreeSet;

use super::StaleFacts;

const CATALOG_LIB: &str = "crates/sandbox-operations/catalog/src/lib.rs";
const CATALOG_ROUTES: &str = "crates/sandbox-operations/catalog/src/routes.rs";
const CLI_LIB: &str = "crates/sandbox-cli/src/lib.rs";
const CLI_PROJECTION: &str = "crates/sandbox-cli/src/projection/mod.rs";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Gate {
    Unconditional,
    Feature(&'static str),
    AllDomains,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ItemKind {
    Module,
    Function,
}

impl ItemKind {
    const fn label(self) -> &'static str {
        match self {
            Self::Module => "module",
            Self::Function => "function",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Signature {
    PublicModule,
    PublicConstFunction,
    PublicFunction,
    Other,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct Rule {
    name: &'static str,
    kind: ItemKind,
    signature: Signature,
    gate: Gate,
}

impl Rule {
    const fn module(name: &'static str, gate: Gate) -> Self {
        Self {
            name,
            kind: ItemKind::Module,
            signature: Signature::PublicModule,
            gate,
        }
    }

    const fn const_function(name: &'static str, gate: Gate) -> Self {
        Self {
            name,
            kind: ItemKind::Function,
            signature: Signature::PublicConstFunction,
            gate,
        }
    }

    const fn function(name: &'static str, gate: Gate) -> Self {
        Self {
            name,
            kind: ItemKind::Function,
            signature: Signature::PublicFunction,
            gate,
        }
    }
}

const CATALOG_LIB_RULES: &[Rule] = &[
    Rule::module("internal", Gate::Unconditional),
    Rule::module("routed", Gate::Unconditional),
    Rule::module("routes", Gate::Unconditional),
    Rule::module("manager", Gate::Feature("manager")),
    Rule::module("runtime", Gate::Feature("runtime")),
    Rule::module("observability", Gate::Feature("observability")),
];

const CATALOG_ROUTE_RULES: &[Rule] = &[
    Rule::const_function("manager_routes", Gate::Feature("manager")),
    Rule::const_function("runtime_routes", Gate::Feature("runtime")),
    Rule::const_function("observability_routes", Gate::Feature("observability")),
    Rule::function("public_routes", Gate::AllDomains),
];

const CLI_LIB_RULES: &[Rule] = &[
    Rule::module("help", Gate::Unconditional),
    Rule::module("input", Gate::Unconditional),
    Rule::module("output", Gate::Unconditional),
    Rule::module("projection", Gate::Unconditional),
    Rule::module("manager", Gate::Feature("manager")),
    Rule::module("runtime", Gate::Feature("runtime")),
    Rule::module("observability", Gate::Feature("observability")),
];

const CLI_PROJECTION_RULES: &[Rule] = &[
    Rule::module("document", Gate::Unconditional),
    Rule::module("manager", Gate::Feature("manager")),
    Rule::module("runtime", Gate::Feature("runtime")),
    Rule::module("observability", Gate::Feature("observability")),
];

#[derive(Clone, Debug, Eq, PartialEq)]
enum Token {
    Word(String),
    String(String),
    Punctuation(char),
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct Declaration {
    name: String,
    kind: ItemKind,
    signature: Signature,
    public: bool,
    conditional_attributes: Vec<Vec<Token>>,
}

#[derive(Debug, Default, Eq, PartialEq)]
struct SourceItems {
    declarations: Vec<Declaration>,
    inner_conditional_attributes: usize,
    path_attributes: usize,
    public_reexports: usize,
    top_level_includes: usize,
}

pub fn validate_feature_gates(facts: &StaleFacts) -> Vec<String> {
    let mut violations = Vec::new();
    validate_source(facts, CATALOG_LIB, CATALOG_LIB_RULES, &mut violations);
    validate_source(facts, CATALOG_ROUTES, CATALOG_ROUTE_RULES, &mut violations);
    validate_source(facts, CLI_LIB, CLI_LIB_RULES, &mut violations);
    validate_source(facts, CLI_PROJECTION, CLI_PROJECTION_RULES, &mut violations);
    violations
}

fn validate_source(facts: &StaleFacts, path: &str, rules: &[Rule], violations: &mut Vec<String>) {
    let sources = facts
        .files
        .iter()
        .filter(|file| file.path == path)
        .collect::<Vec<_>>();
    let [source] = sources.as_slice() else {
        violations.push(format!(
            "feature-gate source {path} must be tracked exactly once, found {}",
            sources.len()
        ));
        return;
    };
    let items = source_items(&source.content);
    if items.inner_conditional_attributes > 0 {
        violations.push(format!(
            "{path} has {} forbidden inner cfg or cfg_attr attribute(s)",
            items.inner_conditional_attributes
        ));
    }
    if items.path_attributes > 0 {
        violations.push(format!(
            "{path} has {} forbidden outer path attribute(s)",
            items.path_attributes
        ));
    }
    if items.public_reexports > 0 {
        violations.push(format!(
            "{path} has {} forbidden public top-level re-export(s)",
            items.public_reexports
        ));
    }
    if items.top_level_includes > 0 {
        violations.push(format!(
            "{path} has {} forbidden top-level include invocation(s)",
            items.top_level_includes
        ));
    }
    for declaration in &items.declarations {
        let expected = rules
            .iter()
            .any(|rule| rule.kind == declaration.kind && rule.name == declaration.name);
        if !expected && declaration.kind == ItemKind::Module {
            violations.push(format!(
                "{path} has unexpected top-level module {}",
                declaration.name
            ));
        } else if !expected && declaration.kind == ItemKind::Function && declaration.public {
            violations.push(format!(
                "{path} has unexpected public top-level function {}",
                declaration.name
            ));
        }
    }
    for rule in rules {
        let matches = items
            .declarations
            .iter()
            .filter(|declaration| declaration.kind == rule.kind && declaration.name == rule.name)
            .collect::<Vec<_>>();
        let [declaration] = matches.as_slice() else {
            violations.push(format!(
                "{path} must declare {} {} exactly once, found {}",
                rule.kind.label(),
                rule.name,
                matches.len()
            ));
            continue;
        };
        if declaration.signature != rule.signature {
            violations.push(format!(
                "{path} {} {} has an alternative declaration shape",
                rule.kind.label(),
                rule.name
            ));
        }
        if !gate_matches(&declaration.conditional_attributes, rule.gate) {
            violations.push(format!(
                "{path} {} {} must {}",
                rule.kind.label(),
                rule.name,
                gate_requirement(rule.gate)
            ));
        }
    }
}

fn gate_requirement(gate: Gate) -> &'static str {
    match gate {
        Gate::Unconditional => "be unconditional",
        Gate::Feature("manager") => "have exactly cfg(feature = \"manager\")",
        Gate::Feature("runtime") => "have exactly cfg(feature = \"runtime\")",
        Gate::Feature("observability") => "have exactly cfg(feature = \"observability\")",
        Gate::Feature(_) => "have exactly its domain feature gate",
        Gate::AllDomains => "require exactly all manager, runtime, and observability features",
    }
}

fn gate_matches(attributes: &[Vec<Token>], expected: Gate) -> bool {
    match expected {
        Gate::Unconditional => attributes.is_empty(),
        Gate::Feature(feature) => {
            matches!(attributes, [attribute] if is_feature_gate(attribute, feature))
        }
        Gate::AllDomains => {
            matches!(attributes, [attribute] if is_all_domain_gate(attribute))
        }
    }
}

fn is_feature_gate(attribute: &[Token], feature: &str) -> bool {
    attribute.len() == 6
        && word(&attribute[0], "cfg")
        && punctuation(&attribute[1], '(')
        && word(&attribute[2], "feature")
        && punctuation(&attribute[3], '=')
        && matches!(&attribute[4], Token::String(value) if value == feature)
        && punctuation(&attribute[5], ')')
}

fn is_all_domain_gate(attribute: &[Token]) -> bool {
    if attribute.len() < 10
        || !word(&attribute[0], "cfg")
        || !punctuation(&attribute[1], '(')
        || !word(&attribute[2], "all")
        || !punctuation(&attribute[3], '(')
        || !punctuation(&attribute[attribute.len() - 2], ')')
        || !punctuation(&attribute[attribute.len() - 1], ')')
    {
        return false;
    }
    let mut features = BTreeSet::new();
    let mut count = 0;
    let inner = &attribute[4..attribute.len() - 2];
    let mut index = 0;
    while index < inner.len() {
        if inner.len() - index < 3
            || !word(&inner[index], "feature")
            || !punctuation(&inner[index + 1], '=')
        {
            return false;
        }
        let Token::String(feature) = &inner[index + 2] else {
            return false;
        };
        features.insert(feature.as_str());
        count += 1;
        index += 3;
        if index == inner.len() {
            break;
        }
        if !punctuation(&inner[index], ',') {
            return false;
        }
        index += 1;
        if index == inner.len() {
            break;
        }
    }
    count == 3 && features == BTreeSet::from(["manager", "runtime", "observability"])
}

fn source_items(source: &str) -> SourceItems {
    let tokens = tokenize(source);
    let mut items = SourceItems {
        inner_conditional_attributes: tokens
            .windows(4)
            .filter(|tokens| {
                punctuation(&tokens[0], '#')
                    && punctuation(&tokens[1], '!')
                    && punctuation(&tokens[2], '[')
                    && (word(&tokens[3], "cfg") || word(&tokens[3], "cfg_attr"))
            })
            .count(),
        path_attributes: tokens
            .windows(3)
            .filter(|tokens| {
                punctuation(&tokens[0], '#')
                    && punctuation(&tokens[1], '[')
                    && word(&tokens[2], "path")
            })
            .count(),
        ..SourceItems::default()
    };
    let mut braces = 0_u32;
    let mut brackets = 0_u32;
    let mut parentheses = 0_u32;
    for (index, token) in tokens.iter().enumerate() {
        if braces == 0 && brackets == 0 && parentheses == 0 {
            if word(token, "mod") {
                if let Some(declaration) = module_declaration(&tokens, index) {
                    items.declarations.push(declaration);
                }
            } else if word(token, "fn") {
                if let Some(declaration) = function_declaration(&tokens, index) {
                    items.declarations.push(declaration);
                }
            } else if word(token, "use") && public_visibility_start(&tokens, index).is_some() {
                items.public_reexports += 1;
            } else if word(token, "include")
                && tokens
                    .get(index + 1)
                    .is_some_and(|token| punctuation(token, '!'))
            {
                items.top_level_includes += 1;
            }
        }
        match token {
            Token::Punctuation('{') => braces += 1,
            Token::Punctuation('}') => braces = braces.saturating_sub(1),
            Token::Punctuation('[') => brackets += 1,
            Token::Punctuation(']') => brackets = brackets.saturating_sub(1),
            Token::Punctuation('(') => parentheses += 1,
            Token::Punctuation(')') => parentheses = parentheses.saturating_sub(1),
            Token::Word(_) | Token::String(_) | Token::Punctuation(_) => {}
        }
    }
    items
}

fn module_declaration(tokens: &[Token], index: usize) -> Option<Declaration> {
    let name = token_word(tokens.get(index + 1)?)?.to_owned();
    let public = public_visibility_start(tokens, index);
    let signature = if index > 0
        && word(&tokens[index - 1], "pub")
        && tokens
            .get(index + 2)
            .is_some_and(|token| punctuation(token, ';'))
    {
        Signature::PublicModule
    } else {
        Signature::Other
    };
    let start = public.unwrap_or(index);
    Some(Declaration {
        name,
        kind: ItemKind::Module,
        signature,
        public: public.is_some(),
        conditional_attributes: conditional_attributes(tokens, start),
    })
}

fn function_declaration(tokens: &[Token], index: usize) -> Option<Declaration> {
    let name = token_word(tokens.get(index + 1)?)?.to_owned();
    let signature =
        if index >= 2 && word(&tokens[index - 2], "pub") && word(&tokens[index - 1], "const") {
            Signature::PublicConstFunction
        } else if index > 0 && word(&tokens[index - 1], "pub") {
            Signature::PublicFunction
        } else {
            Signature::Other
        };
    let public = public_visibility_start(tokens, index);
    let start = public.unwrap_or(index);
    Some(Declaration {
        name,
        kind: ItemKind::Function,
        signature,
        public: public.is_some(),
        conditional_attributes: conditional_attributes(tokens, start),
    })
}

fn public_visibility_start(tokens: &[Token], index: usize) -> Option<usize> {
    let mut cursor = index;
    loop {
        let previous = cursor.checked_sub(1)?;
        if word(&tokens[previous], "pub") {
            return Some(previous);
        }
        if matches!(
            token_word(&tokens[previous]),
            Some("const" | "async" | "unsafe" | "extern" | "default")
        ) {
            cursor = previous;
            continue;
        }
        if matches!(&tokens[previous], Token::String(_))
            && previous > 0
            && word(&tokens[previous - 1], "extern")
        {
            cursor = previous;
            continue;
        }
        if punctuation(&tokens[previous], ')') {
            let open = matching_open_parenthesis(tokens, previous)?;
            let public = open.checked_sub(1)?;
            return word(&tokens[public], "pub").then_some(public);
        }
        return None;
    }
}

fn matching_open_parenthesis(tokens: &[Token], close: usize) -> Option<usize> {
    let mut depth = 1_u32;
    for index in (0..close).rev() {
        if punctuation(&tokens[index], ')') {
            depth += 1;
        } else if punctuation(&tokens[index], '(') {
            depth -= 1;
            if depth == 0 {
                return Some(index);
            }
        }
    }
    None
}

fn conditional_attributes(tokens: &[Token], item_start: usize) -> Vec<Vec<Token>> {
    let mut attributes = Vec::new();
    let mut cursor = item_start;
    while let Some((start, attribute)) = preceding_attribute(tokens, cursor) {
        if attribute
            .first()
            .is_some_and(|token| word(token, "cfg") || word(token, "cfg_attr"))
        {
            attributes.push(attribute.to_vec());
        }
        cursor = start;
    }
    attributes.reverse();
    attributes
}

fn preceding_attribute(tokens: &[Token], end: usize) -> Option<(usize, &[Token])> {
    let close = end.checked_sub(1)?;
    if !punctuation(&tokens[close], ']') {
        return None;
    }
    let mut depth = 1_u32;
    for index in (0..close).rev() {
        if punctuation(&tokens[index], ']') {
            depth += 1;
        } else if punctuation(&tokens[index], '[') {
            depth -= 1;
            if depth == 0 {
                let hash = index.checked_sub(1)?;
                if !punctuation(&tokens[hash], '#') {
                    return None;
                }
                return Some((hash, &tokens[index + 1..close]));
            }
        }
    }
    None
}

fn tokenize(source: &str) -> Vec<Token> {
    let bytes = source.as_bytes();
    let mut tokens = Vec::new();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index..].starts_with(b"//") {
            index = source[index..]
                .find('\n')
                .map_or(bytes.len(), |offset| index + offset + 1);
        } else if bytes[index..].starts_with(b"/*") {
            index = block_comment_end(bytes, index);
        } else if let Some((end, value)) = raw_string(source, index) {
            tokens.push(Token::String(value.to_owned()));
            index = end;
        } else if bytes[index] == b'"' {
            if let Some((end, value)) = quoted(source, index, b'"') {
                tokens.push(Token::String(value.to_owned()));
                index = end;
            } else {
                index += 1;
            }
        } else if matches!(bytes[index], b'b' | b'c') && bytes.get(index + 1) == Some(&b'"') {
            if let Some((end, value)) = quoted(source, index + 1, b'"') {
                tokens.push(Token::String(value.to_owned()));
                index = end;
            } else {
                index += 1;
            }
        } else if bytes[index] == b'\'' {
            if let Some(end) = char_literal_end(source, index) {
                index = end;
            } else {
                tokens.push(Token::Punctuation('\''));
                index += 1;
            }
        } else if identifier_start(bytes[index]) {
            let start = index;
            index += 1;
            while bytes
                .get(index)
                .is_some_and(|byte| identifier_continue(*byte))
            {
                index += 1;
            }
            tokens.push(Token::Word(source[start..index].to_owned()));
        } else {
            if !bytes[index].is_ascii_whitespace() {
                tokens.push(Token::Punctuation(char::from(bytes[index])));
            }
            index += 1;
        }
    }
    tokens
}

fn block_comment_end(source: &[u8], start: usize) -> usize {
    let mut depth = 1_u32;
    let mut index = start + 2;
    while index < source.len() && depth > 0 {
        if source[index..].starts_with(b"/*") {
            depth += 1;
            index += 2;
        } else if source[index..].starts_with(b"*/") {
            depth -= 1;
            index += 2;
        } else {
            index += 1;
        }
    }
    index
}

fn quoted(source: &str, start: usize, quote: u8) -> Option<(usize, &str)> {
    let bytes = source.as_bytes();
    let mut index = start + 1;
    while index < bytes.len() {
        if bytes[index] == b'\\' {
            index += 2;
        } else if bytes[index] == quote {
            return Some((index + 1, &source[start + 1..index]));
        } else {
            index += 1;
        }
    }
    None
}

fn char_literal_end(source: &str, start: usize) -> Option<usize> {
    let bytes = source.as_bytes();
    let content = start + 1;
    let mut close = if bytes.get(content) == Some(&b'\\') {
        match bytes.get(content + 1)? {
            b'x' => content + 4,
            b'u' if bytes.get(content + 2) == Some(&b'{') => {
                content + source[content..].find('}')? + 1
            }
            _ => content + 2,
        }
    } else {
        content + source[content..].chars().next()?.len_utf8()
    };
    if bytes.get(close) == Some(&b'\'') {
        close += 1;
        Some(close)
    } else {
        None
    }
}

fn raw_string(source: &str, start: usize) -> Option<(usize, &str)> {
    let bytes = source.as_bytes();
    let raw = if bytes.get(start) == Some(&b'r') {
        start
    } else if matches!(bytes.get(start), Some(b'b' | b'c')) && bytes.get(start + 1) == Some(&b'r') {
        start + 1
    } else {
        return None;
    };
    let mut opening_quote = raw + 1;
    while bytes.get(opening_quote) == Some(&b'#') {
        opening_quote += 1;
    }
    if bytes.get(opening_quote) != Some(&b'"') {
        return None;
    }
    let hashes = opening_quote - raw - 1;
    let mut closing_quote = opening_quote + 1;
    while closing_quote < bytes.len() {
        if bytes[closing_quote] == b'"'
            && bytes
                .get(closing_quote + 1..closing_quote + 1 + hashes)
                .is_some_and(|suffix| suffix.iter().all(|byte| *byte == b'#'))
        {
            return Some((
                closing_quote + 1 + hashes,
                &source[opening_quote + 1..closing_quote],
            ));
        }
        closing_quote += 1;
    }
    None
}

fn word(token: &Token, expected: &str) -> bool {
    matches!(token, Token::Word(actual) if actual == expected)
}

fn token_word(token: &Token) -> Option<&str> {
    match token {
        Token::Word(word) => Some(word),
        Token::String(_) | Token::Punctuation(_) => None,
    }
}

fn punctuation(token: &Token, expected: char) -> bool {
    matches!(token, Token::Punctuation(actual) if *actual == expected)
}

fn identifier_start(byte: u8) -> bool {
    byte.is_ascii_alphabetic() || byte == b'_'
}

fn identifier_continue(byte: u8) -> bool {
    identifier_start(byte) || byte.is_ascii_digit()
}
