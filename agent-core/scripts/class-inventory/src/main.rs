use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};

use anyhow::{Context, Result};
use html_escape::encode_text;
use proc_macro2::Span;
use quote::ToTokens;
use serde::Serialize;
use syn::spanned::Spanned;
use walkdir::WalkDir;

fn main() -> Result<()> {
    let workspace = find_agent_core_root()?;
    let out_dir = workspace.join("docs/class-inventory");
    let mut inventory = Inventory {
        workspace: "agent-core".to_string(),
        generated_by: "scripts/class-inventory".to_string(),
        crates: Vec::new(),
    };

    for crate_dir in crate_dirs(&workspace)? {
        inventory.crates.push(scan_crate(&workspace, &crate_dir)?);
    }

    inventory.crates.sort_by(|a, b| a.name.cmp(&b.name));
    fs::create_dir_all(out_dir.join("assets"))?;
    fs::create_dir_all(out_dir.join("crates"))?;
    write_json(&out_dir, &inventory)?;
    write_assets(&out_dir, &inventory)?;
    write_index(&out_dir, &inventory)?;
    for krate in &inventory.crates {
        write_crate_page(&out_dir, krate)?;
    }

    println!(
        "wrote {} crate inventories to {}",
        inventory.crates.len(),
        out_dir.display()
    );
    Ok(())
}

fn find_agent_core_root() -> Result<PathBuf> {
    let mut dir = std::env::current_dir()?;
    loop {
        if dir.join("Cargo.toml").exists() && dir.join("crates").is_dir() {
            return Ok(dir);
        }
        if !dir.pop() {
            anyhow::bail!("could not find agent-core root from current directory");
        }
    }
}

fn crate_dirs(workspace: &Path) -> Result<Vec<PathBuf>> {
    let crates_root = workspace.join("crates");
    let mut dirs = Vec::new();
    for entry in fs::read_dir(&crates_root).context("read crates directory")? {
        let entry = entry?;
        let path = entry.path();
        if path.join("Cargo.toml").exists() && path.join("src").is_dir() {
            dirs.push(path);
        }
    }
    dirs.sort();
    Ok(dirs)
}

fn scan_crate(workspace: &Path, crate_dir: &Path) -> Result<CrateInventory> {
    let name = crate_dir
        .file_name()
        .and_then(|name| name.to_str())
        .context("crate directory has no utf-8 name")?
        .to_string();
    let mut modules = Vec::new();

    for entry in WalkDir::new(crate_dir.join("src"))
        .into_iter()
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file())
        .filter(|entry| entry.path().extension().is_some_and(|ext| ext == "rs"))
    {
        let file = entry.path();
        let source = fs::read_to_string(file)
            .with_context(|| format!("read Rust source {}", file.display()))?;
        let parsed = syn::parse_file(&source)
            .with_context(|| format!("parse Rust source {}", file.display()))?;
        let rel_file = relative(workspace, file);
        let module_path = module_path(crate_dir, file);
        let mut items = Vec::new();
        collect_items(&parsed.items, &source, &rel_file, &module_path, &mut items);
        items.sort_by(|a, b| a.line.cmp(&b.line).then_with(|| a.name.cmp(&b.name)));
        modules.push(ModuleInventory {
            path: rel_file,
            module: module_path,
            source,
            items,
        });
    }

    modules.sort_by(|a, b| a.path.cmp(&b.path).then_with(|| a.module.cmp(&b.module)));
    Ok(CrateInventory {
        name,
        path: relative(workspace, crate_dir),
        stats: CrateStats::from_modules(&modules),
        modules,
    })
}

fn collect_items(
    items: &[syn::Item],
    source: &str,
    file: &str,
    module: &str,
    out: &mut Vec<ItemInventory>,
) {
    for item in items {
        match item {
            syn::Item::Struct(item) => out.push(struct_item(item, file, module)),
            syn::Item::Enum(item) => out.push(enum_item(item, file, module)),
            syn::Item::Trait(item) => out.push(trait_item(item, source, file, module)),
            syn::Item::Type(item) => out.push(type_item(item, file, module)),
            syn::Item::Fn(item) => out.push(fn_item(item, source, file, module)),
            syn::Item::Impl(item) => out.push(impl_item(item, source, file, module)),
            syn::Item::Mod(item) => {
                if let Some((_, nested)) = &item.content {
                    let nested_module = if module == "crate" {
                        item.ident.to_string()
                    } else {
                        format!("{module}::{}", item.ident)
                    };
                    collect_items(nested, source, file, &nested_module, out);
                }
            }
            _ => {}
        }
    }
}

fn struct_item(item: &syn::ItemStruct, file: &str, module: &str) -> ItemInventory {
    let fields = fields(&item.fields);
    ItemInventory {
        kind: "struct".to_string(),
        name: item.ident.to_string(),
        visibility: visibility(&item.vis),
        signature: format!(
            "{}struct {}{}",
            visibility_prefix(&item.vis),
            item.ident,
            item.generics.to_token_stream()
        ),
        fields,
        variants: Vec::new(),
        methods: Vec::new(),
        impl_target: None,
        trait_name: None,
        docs: doc_comments(&item.attrs),
        source: None,
        tags: tags_for_type(&item.attrs, &item.ident.to_string()),
        file: file.to_string(),
        module: module.to_string(),
        line: line(item.span()),
    }
}

fn enum_item(item: &syn::ItemEnum, file: &str, module: &str) -> ItemInventory {
    let variants = item
        .variants
        .iter()
        .map(|variant| VariantInventory {
            name: variant.ident.to_string(),
            fields: fields(&variant.fields),
        })
        .collect();
    ItemInventory {
        kind: "enum".to_string(),
        name: item.ident.to_string(),
        visibility: visibility(&item.vis),
        signature: format!(
            "{}enum {}{}",
            visibility_prefix(&item.vis),
            item.ident,
            item.generics.to_token_stream()
        ),
        fields: Vec::new(),
        variants,
        methods: Vec::new(),
        impl_target: None,
        trait_name: None,
        docs: doc_comments(&item.attrs),
        source: None,
        tags: tags_for_type(&item.attrs, &item.ident.to_string()),
        file: file.to_string(),
        module: module.to_string(),
        line: line(item.span()),
    }
}

fn trait_item(item: &syn::ItemTrait, source: &str, file: &str, module: &str) -> ItemInventory {
    let methods = item
        .items
        .iter()
        .filter_map(|trait_item| match trait_item {
            syn::TraitItem::Fn(method) => Some(MethodInventory {
                name: method.sig.ident.to_string(),
                signature: method.sig.to_token_stream().to_string(),
                kind: if method.default.is_some() {
                    "provided".to_string()
                } else {
                    "required".to_string()
                },
                docs: doc_comments(&method.attrs),
                source: source_snippet(source, method.span()),
                line: line(method.span()),
            }),
            _ => None,
        })
        .collect();
    ItemInventory {
        kind: "trait".to_string(),
        name: item.ident.to_string(),
        visibility: visibility(&item.vis),
        signature: format!(
            "{}trait {}{}",
            visibility_prefix(&item.vis),
            item.ident,
            item.generics.to_token_stream()
        ),
        fields: Vec::new(),
        variants: Vec::new(),
        methods,
        impl_target: None,
        trait_name: None,
        docs: doc_comments(&item.attrs),
        source: None,
        tags: tags_for_type(&item.attrs, &item.ident.to_string()),
        file: file.to_string(),
        module: module.to_string(),
        line: line(item.span()),
    }
}

fn type_item(item: &syn::ItemType, file: &str, module: &str) -> ItemInventory {
    ItemInventory {
        kind: "type".to_string(),
        name: item.ident.to_string(),
        visibility: visibility(&item.vis),
        signature: format!(
            "{}type {}{} = {}",
            visibility_prefix(&item.vis),
            item.ident,
            item.generics.to_token_stream(),
            item.ty.to_token_stream()
        ),
        fields: Vec::new(),
        variants: Vec::new(),
        methods: Vec::new(),
        impl_target: None,
        trait_name: None,
        docs: doc_comments(&item.attrs),
        source: None,
        tags: tags_for_type(&item.attrs, &item.ident.to_string()),
        file: file.to_string(),
        module: module.to_string(),
        line: line(item.span()),
    }
}

fn fn_item(item: &syn::ItemFn, source: &str, file: &str, module: &str) -> ItemInventory {
    ItemInventory {
        kind: "fn".to_string(),
        name: item.sig.ident.to_string(),
        visibility: visibility(&item.vis),
        signature: format!(
            "{}{}",
            visibility_prefix(&item.vis),
            item.sig.to_token_stream()
        ),
        fields: Vec::new(),
        variants: Vec::new(),
        methods: Vec::new(),
        impl_target: None,
        trait_name: None,
        docs: doc_comments(&item.attrs),
        source: Some(source_snippet(source, item.span())),
        tags: tags_for_fn(&item.attrs, &item.sig),
        file: file.to_string(),
        module: module.to_string(),
        line: line(item.span()),
    }
}

fn impl_item(item: &syn::ItemImpl, source: &str, file: &str, module: &str) -> ItemInventory {
    let target = item.self_ty.to_token_stream().to_string();
    let trait_name = item
        .trait_
        .as_ref()
        .map(|(_, path, _)| path.to_token_stream().to_string());
    let name = trait_name.as_ref().map_or_else(
        || format!("impl {target}"),
        |trait_name| format!("impl {trait_name} for {target}"),
    );
    let methods = item
        .items
        .iter()
        .filter_map(|impl_item| match impl_item {
            syn::ImplItem::Fn(method) => Some(MethodInventory {
                name: method.sig.ident.to_string(),
                signature: format!(
                    "{}{}",
                    visibility_prefix(&method.vis),
                    method.sig.to_token_stream()
                ),
                kind: method_kind(&method.sig),
                docs: doc_comments(&method.attrs),
                source: source_snippet(source, method.span()),
                line: line(method.span()),
            }),
            _ => None,
        })
        .collect();
    let mut tags = BTreeSet::new();
    tags.insert("impl".to_string());
    if trait_name.is_some() {
        tags.insert("trait-impl".to_string());
    }
    tags.extend(attribute_tags(&item.attrs));
    ItemInventory {
        kind: "impl".to_string(),
        name,
        visibility: "private".to_string(),
        signature: item.trait_.as_ref().map_or_else(
            || format!("impl {target}"),
            |(_, path, _)| format!("impl {} for {target}", path.to_token_stream()),
        ),
        fields: Vec::new(),
        variants: Vec::new(),
        methods,
        impl_target: Some(target),
        trait_name,
        docs: doc_comments(&item.attrs),
        source: None,
        tags: tags.into_iter().collect(),
        file: file.to_string(),
        module: module.to_string(),
        line: line(item.span()),
    }
}

fn fields(fields: &syn::Fields) -> Vec<FieldInventory> {
    fields
        .iter()
        .enumerate()
        .map(|(index, field)| FieldInventory {
            name: field
                .ident
                .as_ref()
                .map_or_else(|| index.to_string(), ToString::to_string),
            visibility: visibility(&field.vis),
            ty: field.ty.to_token_stream().to_string(),
        })
        .collect()
}

fn doc_comments(attrs: &[syn::Attribute]) -> Option<String> {
    let lines = attrs
        .iter()
        .filter(|attr| attr.path().is_ident("doc"))
        .filter_map(|attr| match &attr.meta {
            syn::Meta::NameValue(meta) => match &meta.value {
                syn::Expr::Lit(expr) => match &expr.lit {
                    syn::Lit::Str(value) => Some(value.value().trim().to_string()),
                    _ => None,
                },
                _ => None,
            },
            _ => None,
        })
        .collect::<Vec<_>>();
    let text = lines.join("\n").trim().to_string();
    if text.is_empty() {
        None
    } else {
        Some(text)
    }
}

fn tags_for_type(attrs: &[syn::Attribute], name: &str) -> Vec<String> {
    let mut tags = BTreeSet::new();
    tags.extend(attribute_tags(attrs));
    let lower = name.to_ascii_lowercase();
    if lower.ends_with("dto") || lower.contains("request") || lower.contains("response") {
        tags.insert("dto".to_string());
    }
    if lower.ends_with("port") || lower.ends_with("service") || lower.ends_with("client") {
        tags.insert("port/service".to_string());
    }
    if lower.contains("config") {
        tags.insert("config".to_string());
    }
    for attr in attrs {
        let text = attr.to_token_stream().to_string();
        if text.contains("Serialize") || text.contains("Deserialize") {
            tags.insert("serde".to_string());
        }
        if text.contains("Error") {
            tags.insert("error".to_string());
        }
    }
    tags.into_iter().collect()
}

fn tags_for_fn(attrs: &[syn::Attribute], sig: &syn::Signature) -> Vec<String> {
    let mut tags = BTreeSet::new();
    tags.extend(attribute_tags(attrs));
    if sig.asyncness.is_some() {
        tags.insert("async".to_string());
    }
    if sig.unsafety.is_some() {
        tags.insert("unsafe".to_string());
    }
    if is_constructor_name(&sig.ident.to_string()) {
        tags.insert("constructor".to_string());
    }
    tags.into_iter().collect()
}

fn attribute_tags(attrs: &[syn::Attribute]) -> BTreeSet<String> {
    let mut tags = BTreeSet::new();
    for attr in attrs {
        if attr.path().is_ident("doc") {
            continue;
        }
        let path = attr.path().to_token_stream().to_string().replace(' ', "");
        match &attr.meta {
            syn::Meta::Path(_) => {
                tags.insert(path);
            }
            syn::Meta::List(list) => {
                let content = normalize_attr_tokens(&list.tokens.to_string());
                if path == "derive" {
                    for value in split_attr_args(&content) {
                        if !value.is_empty() {
                            tags.insert(format!("derive: {value}"));
                        }
                    }
                } else {
                    for value in split_attr_args(&content) {
                        let label = attr_arg_label(&value);
                        if !label.is_empty() {
                            tags.insert(format!("{path}: {label}"));
                        }
                    }
                }
            }
            syn::Meta::NameValue(value) => {
                let label = normalize_attr_tokens(&value.value.to_token_stream().to_string());
                tags.insert(format!("{path}: {}", trim_attr_label(&label)));
            }
        }
    }
    tags
}

fn split_attr_args(content: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut start = 0;
    let mut depth = 0usize;
    for (index, ch) in content.char_indices() {
        match ch {
            '(' | '[' | '{' => depth += 1,
            ')' | ']' | '}' => depth = depth.saturating_sub(1),
            ',' if depth == 0 => {
                parts.push(trim_attr_label(&content[start..index]));
                start = index + ch.len_utf8();
            }
            _ => {}
        }
    }
    parts.push(trim_attr_label(&content[start..]));
    parts
}

fn attr_arg_label(value: &str) -> String {
    let value = trim_attr_label(value);
    let end = value.find(['=', '(']).unwrap_or(value.len());
    trim_attr_label(&value[..end])
}

fn trim_attr_label(value: &str) -> String {
    let value = value.trim();
    let value = value.strip_prefix('"').unwrap_or(value);
    let value = value.strip_suffix('"').unwrap_or(value);
    let mut chars = value.chars();
    let short = chars.by_ref().take(64).collect::<String>();
    if chars.next().is_some() {
        format!("{short}...")
    } else {
        short
    }
}

fn normalize_attr_tokens(value: &str) -> String {
    value
        .replace(" :: ", "::")
        .replace(" ,", ",")
        .replace("( ", "(")
        .replace(" )", ")")
        .replace(" = ", "=")
}

fn method_kind(sig: &syn::Signature) -> String {
    if sig.receiver().is_some() {
        "method".to_string()
    } else if is_constructor_name(&sig.ident.to_string()) {
        "constructor".to_string()
    } else {
        "associated-fn".to_string()
    }
}

fn is_constructor_name(name: &str) -> bool {
    matches!(name, "new" | "default" | "from" | "try_from")
        || name.starts_with("with_")
        || name.starts_with("from_")
}

fn visibility(vis: &syn::Visibility) -> String {
    match vis {
        syn::Visibility::Public(_) => "pub".to_string(),
        syn::Visibility::Restricted(restricted) => {
            format!("pub({})", restricted.path.to_token_stream())
        }
        syn::Visibility::Inherited => "private".to_string(),
    }
}

fn visibility_prefix(vis: &syn::Visibility) -> String {
    match vis {
        syn::Visibility::Inherited => String::new(),
        _ => format!("{} ", visibility(vis)),
    }
}

fn line(span: Span) -> usize {
    span.start().line
}

fn source_snippet(source: &str, span: Span) -> String {
    let start = span.start().line.saturating_sub(1);
    let end = span.end().line;
    source
        .lines()
        .skip(start)
        .take(end.saturating_sub(start))
        .map(str::trim_end)
        .collect::<Vec<_>>()
        .join("\n")
}

fn module_path(crate_dir: &Path, file: &Path) -> String {
    let src = crate_dir.join("src");
    let rel = file.strip_prefix(&src).unwrap_or(file);
    let without_ext = rel.with_extension("");
    let parts: Vec<String> = without_ext
        .components()
        .filter_map(|component| component.as_os_str().to_str())
        .filter(|part| *part != "lib" && *part != "main" && *part != "mod")
        .map(ToString::to_string)
        .collect();
    if parts.is_empty() {
        match rel.file_stem().and_then(|stem| stem.to_str()) {
            Some("main") => "bin".to_string(),
            Some("lib") => "lib".to_string(),
            _ => "crate".to_string(),
        }
    } else {
        parts.join("::")
    }
}

fn relative(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

fn write_json(out_dir: &Path, inventory: &Inventory) -> Result<()> {
    let json = serde_json::to_string_pretty(inventory)?;
    fs::write(out_dir.join("assets/inventory.json"), json)?;
    Ok(())
}

fn write_assets(out_dir: &Path, inventory: &Inventory) -> Result<()> {
    fs::write(out_dir.join("assets/inventory.css"), CSS)?;
    fs::write(out_dir.join("assets/inventory.js"), JS)?;
    let json = serde_json::to_string(inventory)?;
    fs::write(
        out_dir.join("assets/inventory-data.js"),
        format!("window.CLASS_INVENTORY = {json};\n"),
    )?;
    Ok(())
}

fn write_index(out_dir: &Path, inventory: &Inventory) -> Result<()> {
    let mut crates = String::new();
    for krate in &inventory.crates {
        crates.push_str(&format!(
            r#"<a class="crate-card" href="crates/{name}.html">
  <strong>{name}</strong>
  <span>{path}</span>
  <small>{types} types · {functions} fns · {methods} methods · {modules} modules</small>
</a>"#,
            name = esc(&krate.name),
            path = esc(&krate.path),
            types = krate.stats.structs + krate.stats.enums + krate.stats.traits,
            functions = krate.stats.functions,
            methods = krate.stats.methods,
            modules = krate.modules.len(),
        ));
    }
    let html = page_shell(
        "agent-core Rust Inventory",
        "agent-core Rust Inventory",
        &format!(
            r#"<section class="summary">
  <div><b>{}</b><span>crates</span></div>
  <div><b>{}</b><span>modules</span></div>
  <div><b>{}</b><span>items</span></div>
  <div><b>{}</b><span>methods</span></div>
</section>
<section class="toolbar">
  <input id="filter" type="search" placeholder="Filter crates" autofocus>
</section>
<section class="crate-grid" id="filter-root">{}</section>"#,
            inventory.crates.len(),
            inventory
                .crates
                .iter()
                .map(|c| c.modules.len())
                .sum::<usize>(),
            inventory
                .crates
                .iter()
                .map(|c| c.stats.items)
                .sum::<usize>(),
            inventory
                .crates
                .iter()
                .map(|c| c.stats.methods)
                .sum::<usize>(),
            crates
        ),
        "",
    );
    fs::write(out_dir.join("index.html"), html)?;
    Ok(())
}

fn write_crate_page(out_dir: &Path, krate: &CrateInventory) -> Result<()> {
    let links = SymbolLinks::from_crate(krate);
    for module in &krate.modules {
        write_crate_file_page(out_dir, krate, module, &links)?;
    }
    Ok(())
}

fn write_crate_file_page(
    out_dir: &Path,
    krate: &CrateInventory,
    module: &ModuleInventory,
    links: &SymbolLinks,
) -> Result<()> {
    let module_stats = ModuleStats::from_module(module);
    let display_path = display_file_path(&krate.name, &module.path);
    let module_nav = file_tree_nav(krate, &module.path);
    let items = module_inventory_body(module, links);
    let source_section = file_source_section(module, links);
    let section = format!(
        r#"<section class="module-section" data-filter-text="{filter}">
  <div class="module-head">
    <div>
      <h2>{path}</h2>
      <p class="path">module <code>{module}</code></p>
    </div>
    <div class="module-stats">
      <span>file owns</span>
      <span>{types} types</span>
      <span>{impls} impls</span>
      <span>{fns} fns</span>
      <span>{methods} methods</span>
    </div>
  </div>
  <div class="items">{items}</div>
</section>"#,
        filter = esc(&format!(
            "{} {} {}",
            module.module,
            module.path,
            module
                .items
                .iter()
                .map(|i| &i.name)
                .cloned()
                .collect::<Vec<_>>()
                .join(" ")
        )),
        module = esc(&module.module),
        path = esc(&display_path),
        types = module_stats.types,
        impls = module_stats.impls,
        fns = module_stats.functions,
        methods = module_stats.methods,
        items = items
    );
    let html = page_shell(
        &format!("{} · {}", krate.name, display_path),
        &format!("{} File Inventory", krate.name),
        &format!(
            r#"<a class="back-link" href="../index.html">agent-core</a>
<section class="summary">
  <div><b>{}</b><span>modules</span></div>
  <div><b>{}</b><span>items</span></div>
  <div><b>{}</b><span>fields</span></div>
  <div><b>{}</b><span>methods</span></div>
</section>
<section class="toolbar">
  <input id="filter" type="search" placeholder="Filter this file by item, field, method, or signature" autofocus>
  <select id="kind-filter">
    <option value="">All items</option>
    <option value="type-model">Type model</option>
    <option value="struct">Structs</option>
    <option value="enum">Enums</option>
    <option value="trait">Traits</option>
    <option value="impl">Impls</option>
    <option value="fn">Functions</option>
    <option value="type">Aliases</option>
  </select>
</section>
<main class="crate-layout">
  <nav class="module-nav">{}</nav>
  <div class="module-list" id="filter-root">{}</div>
</main>
{}"#,
            krate.modules.len(),
            krate.stats.items,
            krate.stats.fields,
            krate.stats.methods,
            module_nav,
            section,
            source_section
        ),
        "../",
    );
    fs::write(
        out_dir.join(format!("crates/{}", crate_file_page_name(krate, module))),
        html,
    )?;
    Ok(())
}

fn file_tree_nav(krate: &CrateInventory, selected_path: &str) -> String {
    let mut root = TreeNode::default();
    for module in &krate.modules {
        let display_path = display_file_path(&krate.name, &module.path);
        let parts = display_path.split('/').collect::<Vec<_>>();
        root.insert(&parts, krate, module);
    }
    root.render_children(selected_path)
}

fn crate_file_page_name(krate: &CrateInventory, module: &ModuleInventory) -> String {
    let Some(first) = krate.modules.first() else {
        return format!("{}.html", krate.name);
    };
    if module.path == first.path {
        format!("{}.html", krate.name)
    } else {
        format!(
            "{}--{}.html",
            krate.name,
            slug(&display_file_path(&krate.name, &module.path))
        )
    }
}

#[derive(Default)]
struct TreeNode {
    children: BTreeMap<String, TreeNode>,
    file: Option<TreeFile>,
}

struct TreeFile {
    href: String,
    source_path: String,
    stats: ModuleStats,
}

impl TreeNode {
    fn insert(&mut self, parts: &[&str], krate: &CrateInventory, module: &ModuleInventory) {
        let Some((head, tail)) = parts.split_first() else {
            return;
        };
        let child = self.children.entry((*head).to_string()).or_default();
        if tail.is_empty() {
            child.file = Some(TreeFile {
                href: crate_file_page_name(krate, module),
                source_path: module.path.clone(),
                stats: ModuleStats::from_module(module),
            });
        } else {
            child.insert(tail, krate, module);
        }
    }

    fn render_children(&self, selected_path: &str) -> String {
        if self.children.is_empty() {
            return String::new();
        }
        let mut html = String::from("<ul class=\"file-tree\">");
        for (name, child) in &self.children {
            html.push_str("<li>");
            if let Some(file) = &child.file {
                let active = if file.source_path == selected_path {
                    " active"
                } else {
                    ""
                };
                html.push_str(&format!(
                    r#"<a class="file-link{active}" href="{href}"><span>{name}</span><small>{types}/{fns}</small></a>"#,
                    active = active,
                    href = esc(&file.href),
                    name = esc(name),
                    types = file.stats.types,
                    fns = file.stats.functions
                ));
            } else {
                html.push_str(&format!(r#"<span class="folder">{}</span>"#, esc(name)));
            }
            html.push_str(&child.render_children(selected_path));
            html.push_str("</li>");
        }
        html.push_str("</ul>");
        html
    }
}

fn display_file_path(crate_name: &str, path: &str) -> String {
    let prefix = format!("crates/{crate_name}/");
    path.strip_prefix(&prefix).unwrap_or(path).to_string()
}

fn module_inventory_body(module: &ModuleInventory, links: &SymbolLinks) -> String {
    let impls_by_target = impls_by_target(module);
    let mut attached_impl_lines = BTreeSet::new();
    let mut body = String::new();

    let mut types = module
        .items
        .iter()
        .filter(|item| is_type_model(item))
        .collect::<Vec<_>>();
    types.sort_by(|a, b| item_rank(a).cmp(&item_rank(b)));

    if !types.is_empty() {
        body.push_str(r#"<div class="group-label">File-Owned Types</div>"#);
        for item in types {
            let key = owner_key(&item.name);
            let impls = impls_by_target
                .get(&key)
                .map_or_else(Vec::new, |impls| impls.clone());
            for impl_item in &impls {
                attached_impl_lines.insert(impl_item.line);
            }
            body.push_str(&type_card(item, &impls, links));
        }
    }

    let mut aliases = module
        .items
        .iter()
        .filter(|item| item.kind == "type")
        .collect::<Vec<_>>();
    aliases.sort_by(|a, b| item_rank(a).cmp(&item_rank(b)));
    if !aliases.is_empty() {
        body.push_str(r#"<div class="group-label">Aliases</div>"#);
        for item in aliases {
            body.push_str(&item_card(item, links));
        }
    }

    let mut fns = module
        .items
        .iter()
        .filter(|item| item.kind == "fn")
        .collect::<Vec<_>>();
    fns.sort_by(|a, b| item_rank(a).cmp(&item_rank(b)));
    if !fns.is_empty() {
        body.push_str(r#"<div class="group-label">Standalone Functions</div>"#);
        for item in fns {
            body.push_str(&item_card(item, links));
        }
    }

    let mut remaining_impls = module
        .items
        .iter()
        .filter(|item| item.kind == "impl" && !attached_impl_lines.contains(&item.line))
        .collect::<Vec<_>>();
    remaining_impls.sort_by(|a, b| item_rank(a).cmp(&item_rank(b)));
    if !remaining_impls.is_empty() {
        body.push_str(r#"<div class="group-label">Other Implementations</div>"#);
        for item in remaining_impls {
            body.push_str(&item_card(item, links));
        }
    }

    body
}

fn impls_by_target(module: &ModuleInventory) -> BTreeMap<String, Vec<&ItemInventory>> {
    let mut impls: BTreeMap<String, Vec<&ItemInventory>> = BTreeMap::new();
    for item in &module.items {
        if item.kind != "impl" {
            continue;
        }
        if let Some(target) = &item.impl_target {
            impls.entry(owner_key(target)).or_default().push(item);
        }
    }
    for items in impls.values_mut() {
        items.sort_by(|a, b| item_rank(a).cmp(&item_rank(b)));
    }
    impls
}

fn type_card(item: &ItemInventory, impls: &[&ItemInventory], links: &SymbolLinks) -> String {
    let mut details = String::new();
    details.push_str(&item_details(item, links));
    let mut inherent_methods = Vec::new();
    let mut trait_impls = Vec::new();
    for impl_item in impls {
        if impl_item.trait_name.is_some() {
            trait_impls.push(*impl_item);
        } else {
            inherent_methods.extend(impl_item.methods.iter());
        }
    }
    inherent_methods.sort_by(|a, b| method_rank(a).cmp(&method_rank(b)));
    trait_impls.sort_by(|a, b| item_rank(a).cmp(&item_rank(b)));
    if !inherent_methods.is_empty() {
        details.push_str("<h4>Methods Implemented For This Type</h4>");
        details.push_str(&method_table(&inherent_methods, links));
    }
    if !trait_impls.is_empty() {
        details.push_str("<h4>Trait Implementations For This Type</h4><div class=\"impl-list\">");
        for impl_item in trait_impls {
            details.push_str(&format!(
                r#"<details><summary><code>{}</code></summary>{}</details>"#,
                link_signature(&impl_item.signature, links),
                method_table(&impl_item.methods.iter().collect::<Vec<_>>(), links)
            ));
        }
        details.push_str("</div>");
    }
    let kind_prefix = if impls.is_empty() {
        "type-model"
    } else {
        "type-model impl method"
    };
    decorated_card(item, "type-card", kind_prefix, &details, links)
}

fn item_card(item: &ItemInventory, links: &SymbolLinks) -> String {
    let details = item_details(item, links);
    decorated_card(item, "item-card", &item.kind, &details, links)
}

fn item_details(item: &ItemInventory, links: &SymbolLinks) -> String {
    let mut details = String::new();
    if !item.fields.is_empty() {
        details.push_str("<h4>Fields</h4><table><tbody>");
        for field in &item.fields {
            details.push_str(&format!(
                "<tr><td>{}</td><td>{}</td><td><code>{}</code></td></tr>",
                esc(&field.visibility),
                esc(&field.name),
                link_signature(&field.ty, links)
            ));
        }
        details.push_str("</tbody></table>");
    }
    if !item.variants.is_empty() {
        details.push_str("<h4>Variants</h4><table><tbody>");
        for variant in &item.variants {
            let fields = variant
                .fields
                .iter()
                .map(|field| format!("{}: {}", field.name, field.ty))
                .collect::<Vec<_>>()
                .join(", ");
            details.push_str(&format!(
                "<tr><td>{}</td><td><code>{}</code></td></tr>",
                esc(&variant.name),
                link_signature(&fields, links)
            ));
        }
        details.push_str("</tbody></table>");
    }
    if !item.methods.is_empty() {
        details.push_str("<h4>Methods</h4>");
        let mut methods = item.methods.iter().collect::<Vec<_>>();
        methods.sort_by(|a, b| method_rank(a).cmp(&method_rank(b)));
        details.push_str(&method_table(&methods, links));
    }
    if let Some(source) = &item.source {
        details.push_str(&function_source_block(source, links));
    }
    details
}

fn method_table(methods: &[&MethodInventory], links: &SymbolLinks) -> String {
    let mut rows = String::new();
    for method in methods {
        let source = function_source_block(&method.source, links);
        let docs = docs_block(method.docs.as_deref());
        rows.push_str(&format!(
            r#"<tr id="{anchor}"><td>{kind}</td><td>{name}</td><td><code>{signature}</code>{docs}{source}</td></tr>"#,
            anchor = esc(&method_anchor(&method.name, method.line)),
            kind = esc(&method.kind),
            name = esc(&method.name),
            signature = link_signature(&method.signature, links),
            docs = docs,
            source = source,
        ));
    }
    format!("<table><tbody>{rows}</tbody></table>")
}

fn function_source_block(source: &str, links: &SymbolLinks) -> String {
    if source.trim().is_empty() {
        return String::new();
    }
    format!(
        r#"<details class="function-source">
  <summary>code</summary>
  <pre><code>{}</code></pre>
</details>"#,
        highlight_rust(source, links)
    )
}

fn file_source_section(module: &ModuleInventory, links: &SymbolLinks) -> String {
    if module.source.trim().is_empty() {
        return String::new();
    }
    format!(
        r#"<section class="file-source-section">
  <div class="source-head">
    <h2>Source File</h2>
    <p class="path">{}</p>
  </div>
  <pre class="file-source"><code>{}</code></pre>
</section>"#,
        esc(&module.path),
        highlight_rust(&module.source, links)
    )
}

#[derive(Default)]
struct SymbolLinks {
    targets: BTreeMap<String, String>,
}

impl SymbolLinks {
    fn from_crate(krate: &CrateInventory) -> Self {
        let mut links = Self::default();
        for module in &krate.modules {
            let page = crate_file_page_name(krate, module);
            for item in &module.items {
                let anchor = item_anchor(&item.kind, &item.name, item.line);
                if is_linkable_item(item) {
                    links
                        .targets
                        .entry(item.name.clone())
                        .or_insert_with(|| format!("{page}#{anchor}"));
                }
                for method in &item.methods {
                    links.targets.entry(method.name.clone()).or_insert_with(|| {
                        format!("{page}#{}", method_anchor(&method.name, method.line))
                    });
                }
            }
        }
        links
    }

    fn target(&self, symbol: &str) -> Option<&str> {
        self.targets.get(symbol).map(String::as_str)
    }
}

fn is_linkable_item(item: &ItemInventory) -> bool {
    matches!(
        item.kind.as_str(),
        "struct" | "enum" | "trait" | "type" | "fn"
    )
}

fn item_anchor(kind: &str, name: &str, line: usize) -> String {
    format!("item-{}-{}-{line}", slug(kind), slug(name))
}

fn method_anchor(name: &str, line: usize) -> String {
    format!("method-{}-{line}", slug(name))
}

fn link_signature(signature: &str, links: &SymbolLinks) -> String {
    let mut out = String::new();
    let mut chars = signature.char_indices().peekable();
    let mut previous_identifier = String::new();
    while let Some((start, ch)) = chars.next() {
        if ch == '&' {
            out.push_str("&amp;");
        } else if ch == '<' {
            out.push_str("&lt;");
        } else if ch == '>' {
            out.push_str("&gt;");
        } else if ch == '"' {
            out.push_str("&quot;");
        } else if ch == '\'' {
            out.push_str("&#x27;");
        } else if ch.is_ascii_alphabetic() || ch == '_' {
            let mut end = start + ch.len_utf8();
            while let Some((next_index, next_ch)) = chars.peek().copied() {
                if next_ch.is_ascii_alphanumeric() || next_ch == '_' {
                    chars.next();
                    end = next_index + next_ch.len_utf8();
                } else {
                    break;
                }
            }
            let token = &signature[start..end];
            let should_link =
                token.chars().next().is_some_and(char::is_uppercase) || previous_identifier == "fn";
            if should_link {
                if let Some(target) = links.target(token) {
                    out.push_str(&format!(
                        r#"<a class="symbol-link" href="{}">{}</a>"#,
                        esc(target),
                        esc(token)
                    ));
                } else {
                    out.push_str(&esc(token));
                }
            } else {
                out.push_str(&esc(token));
            }
            previous_identifier.clear();
            previous_identifier.push_str(token);
        } else {
            out.push(ch);
        }
    }
    out
}

fn highlight_rust(source: &str, links: &SymbolLinks) -> String {
    let mut out = String::new();
    for line in source.lines() {
        if let Some(comment_index) = line.find("//") {
            out.push_str(&highlight_rust_code(&line[..comment_index], links));
            out.push_str(r#"<span class="rs-comment">"#);
            out.push_str(&esc(&line[comment_index..]));
            out.push_str("</span>\n");
        } else {
            out.push_str(&highlight_rust_code(line, links));
            out.push('\n');
        }
    }
    out
}

fn highlight_rust_code(code: &str, links: &SymbolLinks) -> String {
    let mut out = String::new();
    let mut chars = code.char_indices().peekable();
    while let Some((start, ch)) = chars.next() {
        if ch == '&' {
            out.push_str("&amp;");
        } else if ch == '<' {
            out.push_str("&lt;");
        } else if ch == '>' {
            out.push_str("&gt;");
        } else if ch == '"' {
            out.push_str("&quot;");
        } else if ch == '\'' {
            out.push_str("&#x27;");
        } else if ch.is_ascii_alphabetic() || ch == '_' {
            let mut end = start + ch.len_utf8();
            while let Some((next_index, next_ch)) = chars.peek().copied() {
                if next_ch.is_ascii_alphanumeric() || next_ch == '_' {
                    chars.next();
                    end = next_index + next_ch.len_utf8();
                } else {
                    break;
                }
            }
            let token = &code[start..end];
            let next_sig = code[end..].chars().find(|next| !next.is_ascii_whitespace());
            let can_link =
                token.chars().next().is_some_and(char::is_uppercase) || next_sig == Some('(');
            let rendered = if RUST_KEYWORDS.contains(&token) {
                format!(r#"<span class="rs-kw">{}</span>"#, esc(token))
            } else if token == "self" || token == "Self" {
                format!(r#"<span class="rs-self">{}</span>"#, esc(token))
            } else if can_link {
                if let Some(target) = links.target(token) {
                    let class = if token.chars().next().is_some_and(char::is_uppercase) {
                        "symbol-link rs-type"
                    } else {
                        "symbol-link rs-fn"
                    };
                    format!(
                        r#"<a class="{class}" href="{}">{}</a>"#,
                        esc(target),
                        esc(token)
                    )
                } else if token.chars().next().is_some_and(char::is_uppercase) {
                    format!(r#"<span class="rs-type">{}</span>"#, esc(token))
                } else {
                    esc(token)
                }
            } else if token.chars().next().is_some_and(char::is_uppercase) {
                format!(r#"<span class="rs-type">{}</span>"#, esc(token))
            } else {
                esc(token)
            };
            out.push_str(&rendered);
        } else {
            out.push(ch);
        }
    }
    out
}

const RUST_KEYWORDS: &[&str] = &[
    "as", "async", "await", "break", "const", "continue", "crate", "dyn", "else", "enum", "false",
    "fn", "for", "if", "impl", "in", "let", "match", "mod", "move", "mut", "pub", "ref", "return",
    "static", "struct", "super", "trait", "true", "type", "unsafe", "use", "where", "while",
];

fn decorated_card(
    item: &ItemInventory,
    class_name: &str,
    kind_list_prefix: &str,
    details: &str,
    links: &SymbolLinks,
) -> String {
    let tags = item
        .tags
        .iter()
        .map(|tag| format!("<span>{}</span>", esc(tag)))
        .collect::<String>();
    let kind_list = card_kind_list(kind_list_prefix, item);
    let details_block = if details.is_empty() {
        String::new()
    } else {
        format!("  {details}\n")
    };
    let docs_block = docs_block(item.docs.as_deref());
    let docs_line = if docs_block.is_empty() {
        String::new()
    } else {
        format!("  {docs_block}\n")
    };
    format!(
        r#"<article id="{anchor}" class="{class_name}" data-kind="{kind}" data-kind-list="{kind_list}" data-filter-text="{filter}">
  <div class="item-head">
    <span class="kind">{kind}</span>
    <h3>{name}</h3>
    <span class="visibility">{visibility}</span>
  </div>
  <code class="signature">{signature}</code>
{docs_line}  <div class="tags">{tags}</div>
{details_block}
  <p class="source">{file}:{line}</p>
</article>"#,
        class_name = class_name,
        anchor = esc(&item_anchor(&item.kind, &item.name, item.line)),
        kind = esc(&item.kind),
        kind_list = esc(&kind_list),
        filter = esc(&format!(
            "{} {} {} {} {} {} {}",
            item.kind,
            item.name,
            item.visibility,
            item.signature,
            item.file,
            item.fields
                .iter()
                .map(|f| format!("{} {}", f.name, f.ty))
                .collect::<Vec<_>>()
                .join(" "),
            item.methods
                .iter()
                .map(|m| format!("{} {}", m.name, m.signature))
                .collect::<Vec<_>>()
                .join(" ")
        )),
        name = esc(&item.name),
        visibility = esc(&item.visibility),
        signature = link_signature(&item.signature, links),
        docs_line = docs_line,
        tags = tags,
        details_block = details_block,
        file = esc(&item.file),
        line = item.line,
    )
}

fn docs_block(docs: Option<&str>) -> String {
    let Some(docs) = docs else {
        return String::new();
    };
    let docs = docs.trim();
    if docs.is_empty() {
        return String::new();
    }
    let body = docs
        .split('\n')
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(esc)
        .collect::<Vec<_>>()
        .join("<br>");
    format!(r#"<p class="docstring">{body}</p>"#)
}

fn card_kind_list(prefix: &str, item: &ItemInventory) -> String {
    let mut kinds = BTreeSet::new();
    for kind in prefix.split_whitespace() {
        kinds.insert(kind.to_string());
    }
    kinds.insert(item.kind.clone());
    if !item.methods.is_empty() {
        kinds.insert("method".to_string());
    }
    if !item.fields.is_empty() || !item.variants.is_empty() {
        kinds.insert("field".to_string());
    }
    kinds.into_iter().collect::<Vec<_>>().join(" ")
}

fn is_type_model(item: &ItemInventory) -> bool {
    matches!(item.kind.as_str(), "struct" | "enum" | "trait")
}

fn item_rank(item: &ItemInventory) -> (u8, u8, usize, &str) {
    (
        visibility_rank(&item.visibility),
        kind_rank(&item.kind),
        item.line,
        item.name.as_str(),
    )
}

fn visibility_rank(visibility: &str) -> u8 {
    match visibility {
        "pub" => 0,
        value if value.starts_with("pub(") => 1,
        _ => 2,
    }
}

fn kind_rank(kind: &str) -> u8 {
    match kind {
        "struct" => 0,
        "enum" => 1,
        "trait" => 2,
        "impl" => 3,
        "fn" => 4,
        "type" => 5,
        _ => 9,
    }
}

fn method_rank(method: &MethodInventory) -> (u8, usize, &str) {
    let lifecycle = matches!(
        method.name.as_str(),
        "start" | "run" | "execute" | "finish" | "close" | "cancel" | "shutdown" | "drop"
    );
    let rank = match method.kind.as_str() {
        "constructor" => 0,
        _ if lifecycle => 1,
        "method" => 2,
        "associated-fn" => 3,
        "required" => 4,
        "provided" => 5,
        _ => 6,
    };
    (rank, method.line, method.name.as_str())
}

fn owner_key(value: &str) -> String {
    let base = value
        .split('<')
        .next()
        .unwrap_or(value)
        .split("::")
        .last()
        .unwrap_or(value)
        .trim()
        .trim_start_matches('&')
        .trim();
    base.split_whitespace()
        .last()
        .unwrap_or(base)
        .trim_matches(|c: char| !c.is_ascii_alphanumeric() && c != '_')
        .to_string()
}

fn page_shell(title: &str, h1: &str, body: &str, asset_prefix: &str) -> String {
    format!(
        r#"<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="{asset_prefix}assets/inventory.css">
</head>
<body>
  <header>
    <div class="header-row">
      <div>
        <p class="eyebrow">Generated Rust source inventory · file-owned OOP map</p>
        <h1>{h1}</h1>
      </div>
      <div class="refresh-control">
        <button id="refresh-inventory" type="button" title="Regenerate this inventory from the local Rust sources" data-refresh-command="{refresh_command}">Refresh</button>
        <span id="refresh-status" role="status"></span>
      </div>
    </div>
  </header>
  {body}
  <script src="{asset_prefix}assets/inventory-data.js"></script>
  <script src="{asset_prefix}assets/inventory.js"></script>
</body>
</html>"#,
        title = esc(title),
        h1 = esc(h1),
        body = body,
        asset_prefix = asset_prefix,
        refresh_command = esc(REFRESH_COMMAND),
    )
}

fn esc(value: &str) -> String {
    encode_text(value).to_string()
}

fn slug(value: &str) -> String {
    value
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
        .collect()
}

#[derive(Debug, Serialize)]
struct Inventory {
    workspace: String,
    generated_by: String,
    crates: Vec<CrateInventory>,
}

#[derive(Debug, Serialize)]
struct CrateInventory {
    name: String,
    path: String,
    stats: CrateStats,
    modules: Vec<ModuleInventory>,
}

#[derive(Debug, Serialize)]
struct ModuleInventory {
    path: String,
    module: String,
    #[serde(skip_serializing)]
    source: String,
    items: Vec<ItemInventory>,
}

#[derive(Debug, Serialize)]
struct ItemInventory {
    kind: String,
    name: String,
    visibility: String,
    signature: String,
    fields: Vec<FieldInventory>,
    variants: Vec<VariantInventory>,
    methods: Vec<MethodInventory>,
    impl_target: Option<String>,
    trait_name: Option<String>,
    docs: Option<String>,
    #[serde(skip_serializing)]
    source: Option<String>,
    tags: Vec<String>,
    file: String,
    module: String,
    line: usize,
}

#[derive(Debug, Serialize)]
struct FieldInventory {
    name: String,
    visibility: String,
    ty: String,
}

#[derive(Debug, Serialize)]
struct VariantInventory {
    name: String,
    fields: Vec<FieldInventory>,
}

#[derive(Debug, Serialize)]
struct MethodInventory {
    name: String,
    signature: String,
    kind: String,
    docs: Option<String>,
    #[serde(skip_serializing)]
    source: String,
    line: usize,
}

#[derive(Debug, Default, Serialize)]
struct CrateStats {
    modules: usize,
    items: usize,
    structs: usize,
    enums: usize,
    traits: usize,
    functions: usize,
    impls: usize,
    fields: usize,
    methods: usize,
}

impl CrateStats {
    fn from_modules(modules: &[ModuleInventory]) -> Self {
        let mut stats = Self {
            modules: modules.len(),
            ..Self::default()
        };
        for module in modules {
            for item in &module.items {
                stats.items += 1;
                stats.fields += item.fields.len()
                    + item
                        .variants
                        .iter()
                        .map(|variant| variant.fields.len())
                        .sum::<usize>();
                stats.methods += item.methods.len();
                match item.kind.as_str() {
                    "struct" => stats.structs += 1,
                    "enum" => stats.enums += 1,
                    "trait" => stats.traits += 1,
                    "fn" => stats.functions += 1,
                    "impl" => stats.impls += 1,
                    _ => {}
                }
            }
        }
        stats
    }
}

#[derive(Debug, Default)]
struct ModuleStats {
    types: usize,
    impls: usize,
    functions: usize,
    methods: usize,
}

impl ModuleStats {
    fn from_module(module: &ModuleInventory) -> Self {
        let mut stats = Self::default();
        for item in &module.items {
            if is_type_model(item) {
                stats.types += 1;
            }
            match item.kind.as_str() {
                "impl" => stats.impls += 1,
                "fn" => stats.functions += 1,
                _ => {}
            }
            stats.methods += item.methods.len();
        }
        stats
    }
}

const CSS: &str = r#":root {
  color-scheme: light;
  --bg: #f7f8fa;
  --panel: #ffffff;
  --text: #18202a;
  --muted: #637083;
  --line: #d9dee7;
  --accent: #0f766e;
  --accent-2: #8b5cf6;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
header, .summary, .toolbar, .crate-grid, .crate-layout {
  width: min(1440px, calc(100vw - 32px));
  margin: 0 auto;
}
header { padding: 28px 0 16px; }
.header-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.eyebrow {
  margin: 0 0 4px;
  color: var(--accent);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}
h1 { margin: 0; font-size: 30px; letter-spacing: 0; }
.refresh-control {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 38px;
}
button {
  height: 34px;
  border: 1px solid var(--accent);
  border-radius: 6px;
  background: var(--accent);
  color: #fff;
  padding: 0 12px;
  font-weight: 700;
  cursor: pointer;
}
button:disabled {
  cursor: wait;
  opacity: 0.7;
}
#refresh-status {
  min-width: 88px;
  color: var(--muted);
  font-size: 12px;
}
.back-link { display: block; width: min(1440px, calc(100vw - 32px)); margin: 0 auto 8px; color: var(--accent); }
.summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.summary div, .crate-card, .item-card, .type-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.summary div { padding: 14px; }
.summary b { display: block; font-size: 24px; }
.summary span, .crate-card span, .crate-card small, .path, .source, .visibility { color: var(--muted); }
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
}
.global-results {
  width: min(1440px, calc(100vw - 32px));
  margin: -6px auto 14px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}
.global-results-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
  border-bottom: 1px solid var(--line);
}
.global-results a {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr) auto;
  gap: 10px;
  padding: 8px 10px;
  color: inherit;
  text-decoration: none;
  border-top: 1px solid var(--line);
}
.global-results a:first-of-type { border-top: 0; }
.global-results a:hover { background: #eef8f6; }
.result-kind {
  color: var(--accent);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}
.result-main {
  min-width: 0;
  overflow-wrap: anywhere;
}
.result-main strong { display: block; }
.result-main small, .result-path {
  color: var(--muted);
  font-size: 12px;
}
.result-path { text-align: right; overflow-wrap: anywhere; }
input, select {
  height: 38px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  color: var(--text);
  padding: 0 10px;
}
input { flex: 1; min-width: 160px; }
.crate-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 10px;
  padding-bottom: 30px;
}
.crate-card {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 14px;
  color: inherit;
  text-decoration: none;
}
.crate-card:hover { border-color: var(--accent); }
.crate-layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
  padding-bottom: 40px;
}
.module-nav {
  position: sticky;
  top: 12px;
  max-height: calc(100vh - 24px);
  overflow: auto;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px;
}
.module-nav::before {
  content: "Source files";
  display: block;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  padding: 4px 8px 8px;
}
.file-tree {
  list-style: none;
  margin: 0;
  padding: 0;
}
.file-tree .file-tree {
  margin-left: 12px;
  padding-left: 10px;
  border-left: 1px solid var(--line);
}
.file-tree li {
  margin: 1px 0;
}
.folder {
  display: block;
  padding: 6px 8px 3px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}
.module-nav a {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 8px;
  color: inherit;
  text-decoration: none;
  border-radius: 6px;
}
.module-nav a:hover { background: #eef8f6; }
.module-nav a.active {
  background: #dff3ef;
  color: var(--accent);
  font-weight: 800;
}
.module-nav span { overflow-wrap: anywhere; }
.module-nav small { color: var(--muted); white-space: nowrap; }
.module-section {
  margin-bottom: 22px;
  scroll-margin-top: 14px;
}
.module-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;
}
h2 { margin: 0 0 3px; font-size: 20px; letter-spacing: 0; }
.path { margin: 0 0 9px; }
.module-stats {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}
.module-stats span {
  border: 1px solid var(--line);
  background: #fff;
  border-radius: 999px;
  padding: 2px 8px;
  color: var(--muted);
  font-size: 12px;
}
.module-stats span:first-child {
  color: var(--accent);
  font-weight: 700;
}
.items {
  display: grid;
  gap: 10px;
}
.group-label {
  margin: 8px 0 -2px;
  color: var(--accent);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0;
  text-transform: uppercase;
}
.item-card, .type-card {
  padding: 12px;
  overflow: hidden;
}
.type-card {
  border-left: 4px solid var(--accent);
}
.item-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.item-head h3 {
  margin: 0;
  font-size: 16px;
  overflow-wrap: anywhere;
}
.kind {
  flex: 0 0 auto;
  color: #fff;
  background: var(--accent);
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 12px;
  font-weight: 700;
}
.signature {
  display: block;
  margin-top: 8px;
  padding: 8px;
  overflow-x: auto;
  background: #f1f4f8;
  border-radius: 6px;
  white-space: pre;
}
.docstring {
  margin: 8px 0 0;
  max-width: 900px;
  color: #374151;
  background: #f8fafc;
  border-left: 3px solid var(--line);
  padding: 7px 9px;
  border-radius: 4px;
}
.symbol-link {
  color: #0369a1;
  text-decoration: none;
  border-bottom: 1px dotted #0369a1;
}
.symbol-link:hover {
  color: var(--accent);
  border-bottom-color: var(--accent);
}
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size: 12px;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.tags span {
  border: 1px solid var(--line);
  color: var(--accent-2);
  border-radius: 999px;
  padding: 1px 7px;
  font-size: 12px;
}
h4 { margin: 12px 0 4px; font-size: 13px; }
.impl-list {
  display: grid;
  gap: 6px;
}
details {
  border-top: 1px solid var(--line);
  padding-top: 6px;
}
summary {
  cursor: pointer;
  color: var(--muted);
  overflow-wrap: anywhere;
}
.function-source {
  margin-top: 6px;
}
.function-source summary {
  display: inline-block;
  color: var(--accent);
  font-size: 12px;
  font-weight: 700;
}
.function-source pre {
  max-height: 460px;
  overflow: auto;
  margin: 6px 0 0;
  padding: 10px;
  background: #fbfcfe;
  color: #1f2937;
  border: 1px solid var(--line);
  border-radius: 6px;
}
.file-source-section {
  width: min(1440px, calc(100vw - 32px));
  margin: 0 auto 42px;
}
.source-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 12px;
  margin: 12px 0 8px;
}
.source-head h2 {
  margin: 0;
  font-size: 18px;
}
.source-head .path {
  margin: 0;
  text-align: right;
}
.file-source {
  overflow: auto;
  margin: 0;
  padding: 12px;
  background: #fbfcfe;
  color: #1f2937;
  border: 1px solid var(--line);
  border-radius: 8px;
}
.rs-kw { color: #1d4ed8; font-weight: 700; }
.rs-type { color: #a16207; }
.rs-self { color: #7c3aed; }
.rs-fn { color: #047857; }
.rs-comment { color: #64748b; }
table {
  width: 100%;
  border-collapse: collapse;
}
td {
  border-top: 1px solid var(--line);
  padding: 5px 6px;
  vertical-align: top;
}
td:first-child { width: 120px; color: var(--muted); }
.source { margin: 10px 0 0; font-size: 12px; }
.is-hidden { display: none !important; }

@media (max-width: 860px) {
  .header-row { flex-direction: column; }
  .summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .toolbar { flex-direction: column; }
  .crate-layout { grid-template-columns: 1fr; }
  .module-nav { position: static; max-height: 260px; }
  .module-head { flex-direction: column; }
  .module-stats { justify-content: flex-start; }
}
"#;

const JS: &str = r##"const filterInput = document.querySelector("#filter");
const kindFilter = document.querySelector("#kind-filter");
const root = document.querySelector("#filter-root");
const refreshButton = document.querySelector("#refresh-inventory");
const refreshStatus = document.querySelector("#refresh-status");
const refreshEndpoint = "/__class_inventory_refresh";
const globalResults = document.createElement("section");
globalResults.id = "global-results";
globalResults.className = "global-results";
globalResults.hidden = true;
document.querySelector(".toolbar")?.after(globalResults);

if (refreshButton && window.location.protocol === "file:") {
  refreshButton.textContent = "Reload";
  refreshButton.title = "Chrome cannot run local commands from file://. Click to copy the refresh command.";
}

function applyFilter() {
  if (!root) return;
  const query = (filterInput?.value || "").trim().toLowerCase();
  const kind = kindFilter?.value || "";
  renderGlobalResults(query);
  const richCards = root.querySelectorAll(".crate-card, .item-card, .type-card");
  richCards.forEach((card) => {
    const text = (card.dataset.filterText || card.textContent || "").toLowerCase();
    const kindList = card.dataset.kindList || card.dataset.kind || "";
    const kindMatch = !kind || kindList.split(/\s+/).includes(kind);
    card.classList.toggle("is-hidden", Boolean(query && !text.includes(query)) || !kindMatch);
  });
  root.querySelectorAll(".module-section").forEach((section) => {
    const visible = section.querySelector(".item-card:not(.is-hidden), .type-card:not(.is-hidden)");
    const sectionText = (section.dataset.filterText || section.textContent || "").toLowerCase();
    section.classList.toggle("is-hidden", Boolean(query && !sectionText.includes(query) && !visible));
  });
}

filterInput?.addEventListener("input", applyFilter);
kindFilter?.addEventListener("change", applyFilter);

function renderGlobalResults(query) {
  if (!globalResults) return;
  if (!query || query.length < 2 || !window.CLASS_INVENTORY) {
    globalResults.hidden = true;
    globalResults.innerHTML = "";
    return;
  }
  const terms = query.split(/\s+/).filter(Boolean);
  const matches = symbolIndex()
    .map((entry) => ({ entry, score: scoreEntry(entry, terms) }))
    .filter((match) => match.score > 0)
    .sort((a, b) => b.score - a.score || a.entry.name.localeCompare(b.entry.name))
    .slice(0, 40);
  if (!matches.length) {
    globalResults.hidden = false;
    globalResults.innerHTML = `<div class="global-results-head"><span>All-symbol search</span><span>No matches</span></div>`;
    return;
  }
  const rows = matches
    .map(({ entry }) => `<a href="${escapeAttr(entry.href)}"><span class="result-kind">${escapeHtml(entry.kind)}</span><span class="result-main"><strong>${escapeHtml(entry.name)}</strong><small>${escapeHtml(entry.detail)}</small></span><span class="result-path">${escapeHtml(entry.path)}</span></a>`)
    .join("");
  globalResults.hidden = false;
  globalResults.innerHTML = `<div class="global-results-head"><span>All-symbol search</span><span>${matches.length} matches</span></div>${rows}`;
}

let cachedSymbolIndex;

function symbolIndex() {
  if (cachedSymbolIndex) return cachedSymbolIndex;
  const inventory = window.CLASS_INVENTORY;
  const entries = [];
  const cratesPrefix = location.pathname.includes("/crates/") ? "" : "crates/";
  for (const crateInfo of inventory?.crates || []) {
    for (const moduleInfo of crateInfo.modules || []) {
      const page = `${cratesPrefix}${crateFilePageName(crateInfo, moduleInfo)}`;
      const filePath = moduleInfo.path || "";
      entries.push({
        kind: "file",
        name: displayFilePath(crateInfo.name, filePath),
        detail: `${crateInfo.name} module ${moduleInfo.module}`,
        path: filePath,
        href: page,
        search: `${crateInfo.name} ${moduleInfo.module} ${filePath}`,
      });
      for (const item of moduleInfo.items || []) {
        const itemHref = `${page}#${itemAnchor(item.kind, item.name, item.line)}`;
        entries.push({
          kind: item.kind,
          name: item.name,
          detail: item.signature || "",
          path: filePath,
          href: itemHref,
          search: `${crateInfo.name} ${moduleInfo.module} ${filePath} ${item.kind} ${item.name} ${item.signature || ""} ${item.docs || ""}`,
        });
        for (const field of item.fields || []) {
          entries.push({
            kind: "field",
            name: `${item.name}.${field.name}`,
            detail: `${field.name}: ${field.ty}`,
            path: filePath,
            href: itemHref,
            search: `${crateInfo.name} ${moduleInfo.module} ${filePath} field ${item.name} ${field.name} ${field.ty}`,
          });
        }
        for (const variant of item.variants || []) {
          entries.push({
            kind: "variant",
            name: `${item.name}::${variant.name}`,
            detail: item.name,
            path: filePath,
            href: itemHref,
            search: `${crateInfo.name} ${moduleInfo.module} ${filePath} variant ${item.name} ${variant.name}`,
          });
        }
        for (const method of item.methods || []) {
          entries.push({
            kind: method.kind || "method",
            name: `${ownerName(item)}.${method.name}`,
            detail: method.signature || "",
            path: filePath,
            href: `${page}#${methodAnchor(method.name, method.line)}`,
            search: `${crateInfo.name} ${moduleInfo.module} ${filePath} method ${ownerName(item)} ${method.name} ${method.signature || ""} ${method.docs || ""}`,
          });
        }
      }
    }
  }
  cachedSymbolIndex = entries;
  return entries;
}

function scoreEntry(entry, terms) {
  const search = entry.search.toLowerCase();
  let score = 0;
  for (const term of terms) {
    const name = entry.name.toLowerCase();
    if (name === term) score += 100;
    else if (name.startsWith(term)) score += 60;
    else if (name.includes(term)) score += 35;
    else if (search.includes(term)) score += 10;
    else return 0;
  }
  if (["struct", "enum", "trait"].includes(entry.kind)) score += 8;
  if (entry.kind === "method" || entry.kind === "constructor") score += 4;
  return score;
}

function crateFilePageName(crateInfo, moduleInfo) {
  const first = crateInfo.modules?.[0];
  if (!first || moduleInfo.path === first.path) return `${crateInfo.name}.html`;
  return `${crateInfo.name}--${slug(displayFilePath(crateInfo.name, moduleInfo.path))}.html`;
}

function displayFilePath(crateName, filePath) {
  const prefix = `crates/${crateName}/`;
  return filePath.startsWith(prefix) ? filePath.slice(prefix.length) : filePath;
}

function itemAnchor(kind, name, line) {
  return `item-${slug(kind)}-${slug(name)}-${line}`;
}

function methodAnchor(name, line) {
  return `method-${slug(name)}-${line}`;
}

function slug(value) {
  return String(value || "").split("").map((ch) => /[A-Za-z0-9]/.test(ch) ? ch : "-").join("");
}

function ownerName(item) {
  return (item.impl_target || item.name || "").split("<")[0].split("::").pop().trim() || item.name;
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[ch]));
}

function escapeAttr(value) {
  return escapeHtml(value);
}

refreshButton?.addEventListener("click", async () => {
  if (window.location.protocol === "file:") {
    const command = refreshButton.dataset.refreshCommand || "";
    try {
      await navigator.clipboard.writeText(command);
      if (refreshStatus) refreshStatus.textContent = "Copied; reloading";
    } catch (error) {
      window.prompt("Run this command, then reload this HTML file:", command);
      if (refreshStatus) refreshStatus.textContent = "Reloading";
    }
    window.setTimeout(() => window.location.reload(), 900);
    return;
  }
  refreshButton.disabled = true;
  if (refreshStatus) refreshStatus.textContent = "Refreshing";
  try {
    const response = await fetch(refreshEndpoint, { method: "POST" });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.ok === false) {
      throw new Error(result.error || `refresh failed (${response.status})`);
    }
    if (refreshStatus) refreshStatus.textContent = "Reloading";
    window.location.reload();
  } catch (error) {
    if (refreshStatus) refreshStatus.textContent = "Refresh unavailable";
    console.error(error);
    refreshButton.disabled = false;
  }
});
"##;

const REFRESH_COMMAND: &str =
    "cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/agent-core && cargo run --manifest-path scripts/class-inventory/Cargo.toml";
