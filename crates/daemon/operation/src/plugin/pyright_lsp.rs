use std::collections::BTreeMap;
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, Sender};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use config::configs::daemon::{PluginRuntimeConfig, PyrightLspConfig, PYRIGHT_LSP_PLUGIN_ID};
use layerstack::{LayerPath, LayerStack, MergedView};
use serde_json::{json, Value};

use super::contract::{
    PyrightLspDefinitionInput, PyrightLspDiagnosticsInput, PyrightLspQuerySymbolsInput,
    PyrightLspReferencesInput,
};
use super::{PluginRuntime, PluginRuntimeError};

const FRESHNESS_ANALYZER_REFLECTED: &str = "analyzer_reflected";
const LANGUAGE_ID: &str = "python";
const PYRIGHT_PYPI_VERSION: &str = "1.1.410";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BuiltinPluginProvider {
    PyrightLsp,
}

impl BuiltinPluginProvider {
    #[must_use]
    pub const fn id(self) -> &'static str {
        match self {
            Self::PyrightLsp => PYRIGHT_LSP_PLUGIN_ID,
        }
    }
}

pub(super) struct PyrightLspRuntime {
    process: Option<LspProcess>,
    active_manifest_key: Option<String>,
    projection_root: PathBuf,
    initialize_result: Option<Value>,
    resolved_command: Vec<String>,
    last_init_error: Option<String>,
    last_refresh_error: Option<String>,
    last_analysis_error: Option<String>,
    last_process_exit_error: Option<String>,
}

impl PyrightLspRuntime {
    pub(super) fn new(config: &PyrightLspConfig) -> Self {
        Self {
            process: None,
            active_manifest_key: None,
            projection_root: config.workspace_root.clone(),
            initialize_result: None,
            resolved_command: Vec::new(),
            last_init_error: None,
            last_refresh_error: None,
            last_analysis_error: None,
            last_process_exit_error: None,
        }
    }

    fn ensure_ready(
        &mut self,
        config: &PluginRuntimeConfig,
        layer_stack_root: &str,
        target_file: Option<&str>,
    ) -> Result<ReadyPyright<'_>, PluginRuntimeError> {
        let projection = self.ensure_projection_current(&config.pyright_lsp, layer_stack_root)?;
        let process_missing = self.process.is_none();
        if process_missing {
            self.start_process(config)?;
        }
        let Some(process) = self.process.as_mut() else {
            return Err(PluginRuntimeError::PyrightLsp(
                "pyright_lsp process was not started".to_owned(),
            ));
        };
        if let Some(file_path) = target_file {
            process.open_document(
                &projection.projection_root,
                file_path,
                projection.manifest_version,
            )?;
        }
        Ok(ReadyPyright {
            process,
            manifest_key: projection.manifest_key,
            projection_root: projection.projection_root,
        })
    }

    fn ensure_projection_current(
        &mut self,
        config: &PyrightLspConfig,
        layer_stack_root: &str,
    ) -> Result<ProjectionState, PluginRuntimeError> {
        let stack_root = PathBuf::from(layer_stack_root);
        let stack = LayerStack::open(stack_root.clone())?;
        let lease = stack.acquire_snapshot("pyright_lsp:projection")?;
        let manifest_key = manifest_key(lease.manifest_version, &lease.root_hash);
        let manifest_version = lease.manifest_version;
        let projection_root = config.workspace_root.clone();
        if self.active_manifest_key.as_deref() == Some(&manifest_key)
            && projection_root == self.projection_root
        {
            release_snapshot(&stack_root, &lease.lease_id);
            return Ok(ProjectionState {
                manifest_key,
                manifest_version,
                projection_root,
            });
        }

        self.stop_process();
        let project_result = project_snapshot(&stack_root, &projection_root, &lease.manifest);
        release_snapshot(&stack_root, &lease.lease_id);
        if let Err(err) = project_result {
            let message = err.to_string();
            self.last_refresh_error = Some(message.clone());
            return Err(PluginRuntimeError::PyrightLsp(message));
        }
        self.projection_root = projection_root.clone();
        self.active_manifest_key = Some(manifest_key.clone());
        self.last_refresh_error = None;
        Ok(ProjectionState {
            manifest_key,
            manifest_version,
            projection_root,
        })
    }

    fn start_process(&mut self, config: &PluginRuntimeConfig) -> Result<(), PluginRuntimeError> {
        let command = resolve_pyright_command(&config.pyright_lsp)?;
        let timeout = Duration::from_millis(config.pyright_lsp.analysis_timeout_ms)
            .max(Duration::from_secs(60));
        let mut process = LspProcess::start(
            command.clone(),
            &self.projection_root,
            config.max_response_bytes,
        )
        .map_err(|err| {
            self.last_init_error = Some(err.clone());
            PluginRuntimeError::PyrightLsp(err)
        })?;
        let initialize_result =
            process
                .initialize(&self.projection_root, timeout)
                .map_err(|err| {
                    self.last_init_error = Some(err.clone());
                    PluginRuntimeError::PyrightLsp(err)
                })?;
        self.resolved_command = command;
        self.initialize_result = Some(initialize_result);
        self.last_init_error = None;
        self.process = Some(process);
        Ok(())
    }

    fn stop_process(&mut self) {
        if let Some(mut process) = self.process.take() {
            process.teardown();
        }
        self.initialize_result = None;
        self.resolved_command.clear();
    }

    fn health_value(&mut self, config: &PluginRuntimeConfig, enabled: bool) -> Value {
        let running = self.process.as_mut().is_some_and(LspProcess::is_running);
        let pid = self.process.as_ref().map(LspProcess::pid);
        let capabilities = self
            .initialize_result
            .as_ref()
            .and_then(|result| result.get("capabilities"))
            .cloned()
            .unwrap_or(Value::Null);
        let server_info = self
            .initialize_result
            .as_ref()
            .and_then(|result| result.get("serverInfo"))
            .cloned()
            .unwrap_or(Value::Null);
        json!({
            "provider": PYRIGHT_LSP_PLUGIN_ID,
            "enabled": enabled,
            "running": running,
            "process_id": pid,
            "pid": pid,
            "node_path": config.pyright_lsp.node_path,
            "pyright_langserver_path": config.pyright_lsp.pyright_langserver_path,
            "resolved_command": self.resolved_command,
            "initialize_success": self.initialize_result.is_some(),
            "capabilities": capabilities,
            "server_info": server_info,
            "active_manifest_key": self.active_manifest_key,
            "projection_root": self.projection_root,
            "last_init_error": self.last_init_error,
            "last_refresh_error": self.last_refresh_error,
            "last_analysis_error": self.last_analysis_error,
            "last_process_exit_error": self.last_process_exit_error,
        })
    }
}

impl PluginRuntime {
    #[must_use]
    pub fn builtin_plugin_list(&self) -> Value {
        json!({
            "success": true,
            "providers": [{
                "provider": PYRIGHT_LSP_PLUGIN_ID,
                "enabled": self.config.pyright_lsp_enabled(),
                "state": if self.config.pyright_lsp_enabled() { "enabled" } else { "disabled" },
            }],
        })
    }

    pub fn builtin_plugin_health(
        &self,
        layer_stack_root: Option<&str>,
    ) -> Result<Value, PluginRuntimeError> {
        let enabled = self.config.pyright_lsp_enabled();
        if enabled {
            let root = layer_stack_root.ok_or_else(|| {
                PluginRuntimeError::InvalidRequest(
                    "layer_stack_root is required for sandbox.plugin.health".to_owned(),
                )
            })?;
            let mut runtime = self.lock_pyright_lsp()?;
            runtime.ensure_ready(&self.config, root, None)?;
            Ok(json!({
                "success": true,
                "providers": [runtime.health_value(&self.config, true)],
            }))
        } else {
            let mut runtime = self.lock_pyright_lsp()?;
            Ok(json!({
                "success": true,
                "providers": [runtime.health_value(&self.config, false)],
            }))
        }
    }

    pub fn pyright_lsp_query_symbols(
        &self,
        input: &PyrightLspQuerySymbolsInput,
    ) -> Result<Value, PluginRuntimeError> {
        self.ensure_pyright_enabled()?;
        let mut runtime = self.lock_pyright_lsp()?;
        let ready = runtime.ensure_ready(
            &self.config,
            &input.layer_stack_root,
            Some(&input.file_path),
        )?;
        let symbols = ready.process.document_symbols(
            &ready.projection_root,
            &input.file_path,
            input.query.as_deref(),
        )?;
        Ok(base_pyright_response(
            &ready.manifest_key,
            json!({ "symbols": symbols }),
        ))
    }

    pub fn pyright_lsp_definition(
        &self,
        input: &PyrightLspDefinitionInput,
    ) -> Result<Value, PluginRuntimeError> {
        self.ensure_pyright_enabled()?;
        let mut runtime = self.lock_pyright_lsp()?;
        let ready = runtime.ensure_ready(
            &self.config,
            &input.layer_stack_root,
            Some(&input.file_path),
        )?;
        let locations = ready.process.definition(
            &ready.projection_root,
            &input.file_path,
            input.position.line,
            input.position.character,
        )?;
        Ok(base_pyright_response(
            &ready.manifest_key,
            json!({ "locations": locations }),
        ))
    }

    pub fn pyright_lsp_references(
        &self,
        input: &PyrightLspReferencesInput,
    ) -> Result<Value, PluginRuntimeError> {
        self.ensure_pyright_enabled()?;
        let mut runtime = self.lock_pyright_lsp()?;
        let ready = runtime.ensure_ready(
            &self.config,
            &input.layer_stack_root,
            Some(&input.file_path),
        )?;
        let locations = ready.process.references(
            &ready.projection_root,
            &input.file_path,
            input.position.line,
            input.position.character,
            input.include_declaration,
        )?;
        Ok(base_pyright_response(
            &ready.manifest_key,
            json!({ "locations": locations }),
        ))
    }

    pub fn pyright_lsp_diagnostics(
        &self,
        input: &PyrightLspDiagnosticsInput,
    ) -> Result<Value, PluginRuntimeError> {
        self.ensure_pyright_enabled()?;
        let timeout = Duration::from_millis(self.config.pyright_lsp.analysis_timeout_ms);
        let mut runtime = self.lock_pyright_lsp()?;
        let (manifest_key, diagnostics_result) = {
            let ready = runtime.ensure_ready(
                &self.config,
                &input.layer_stack_root,
                Some(&input.file_path),
            )?;
            (
                ready.manifest_key.clone(),
                ready
                    .process
                    .diagnostics(&ready.projection_root, &input.file_path, timeout),
            )
        };
        let diagnostics = match diagnostics_result {
            Ok(diagnostics) => {
                runtime.last_analysis_error = None;
                base_pyright_response(&manifest_key, json!({ "diagnostics": diagnostics }))
            }
            Err(err) if err.starts_with("timed out waiting for diagnostics") => {
                runtime.last_analysis_error = Some(err.clone());
                pyright_timeout_response(&manifest_key, err)
            }
            Err(err) => {
                runtime.last_analysis_error = Some(err.clone());
                return Err(PluginRuntimeError::PyrightLsp(err));
            }
        };
        Ok(diagnostics)
    }

    fn ensure_pyright_enabled(&self) -> Result<(), PluginRuntimeError> {
        if self.config.pyright_lsp_enabled() {
            Ok(())
        } else {
            Err(PluginRuntimeError::PluginDisabled(
                PYRIGHT_LSP_PLUGIN_ID.to_owned(),
            ))
        }
    }
}

struct ReadyPyright<'a> {
    process: &'a mut LspProcess,
    manifest_key: String,
    projection_root: PathBuf,
}

struct ProjectionState {
    manifest_key: String,
    manifest_version: i64,
    projection_root: PathBuf,
}

fn base_pyright_response(manifest_key: &str, fields: Value) -> Value {
    let mut response = json!({
        "success": true,
        "provider": PYRIGHT_LSP_PLUGIN_ID,
        "manifest_key": manifest_key,
        "freshness": FRESHNESS_ANALYZER_REFLECTED,
        "stale": false,
        "analysis_status": "reflected",
    });
    if let (Some(target), Some(source)) = (response.as_object_mut(), fields.as_object()) {
        for (key, value) in source {
            target.insert(key.clone(), value.clone());
        }
    }
    response
}

fn pyright_timeout_response(manifest_key: &str, message: String) -> Value {
    json!({
        "success": true,
        "provider": PYRIGHT_LSP_PLUGIN_ID,
        "manifest_key": manifest_key,
        "freshness": FRESHNESS_ANALYZER_REFLECTED,
        "stale": true,
        "analysis_status": "timeout",
        "diagnostics": [],
        "error": {
            "kind": "analysis_timeout",
            "message": message,
        },
    })
}

fn manifest_key(version: i64, root_hash: &str) -> String {
    format!("version:{version}:{root_hash}")
}

fn project_snapshot(
    stack_root: &Path,
    projection_root: &Path,
    manifest: &layerstack::Manifest,
) -> Result<(), PluginRuntimeError> {
    validate_projection_root(projection_root)?;
    if projection_root.exists() {
        std::fs::remove_dir_all(projection_root)?;
    }
    if let Some(parent) = projection_root.parent() {
        std::fs::create_dir_all(parent)?;
    }
    MergedView::new(stack_root.to_path_buf()).project(projection_root, manifest)?;
    Ok(())
}

fn validate_projection_root(path: &Path) -> Result<(), PluginRuntimeError> {
    if !path.is_absolute() || path == Path::new("/") {
        return Err(PluginRuntimeError::InvalidRequest(format!(
            "pyright_lsp workspace_root must be an absolute non-root path: {}",
            path.display()
        )));
    }
    Ok(())
}

fn release_snapshot(stack_root: &Path, lease_id: &str) {
    if let Ok(mut stack) = LayerStack::open(stack_root.to_path_buf()) {
        let _ = stack.release_lease(lease_id);
    }
}

fn resolve_pyright_command(config: &PyrightLspConfig) -> Result<Vec<String>, PluginRuntimeError> {
    if config.node_path.exists() && config.pyright_langserver_path.exists() {
        return Ok(vec![
            config.node_path.to_string_lossy().into_owned(),
            config
                .pyright_langserver_path
                .to_string_lossy()
                .into_owned(),
            "--stdio".to_owned(),
        ]);
    }
    if let Some(path) = find_executable("pyright-langserver") {
        return Ok(vec![
            path.to_string_lossy().into_owned(),
            "--stdio".to_owned(),
        ]);
    }
    for candidate in [
        "/usr/local/bin/pyright-langserver",
        "/usr/bin/pyright-langserver",
        "/opt/miniconda3/bin/pyright-langserver",
        "/opt/miniconda3/envs/testbed/bin/pyright-langserver",
    ] {
        let path = PathBuf::from(candidate);
        if path.exists() {
            return Ok(vec![candidate.to_owned(), "--stdio".to_owned()]);
        }
    }
    if let Some(command) = provision_python_pyright_command(config)? {
        return Ok(command);
    }
    Err(PluginRuntimeError::PyrightLsp(format!(
        "pyright_lsp could not find configured node/langserver ({}, {}) or pyright-langserver in PATH",
        config.node_path.display(),
        config.pyright_langserver_path.display()
    )))
}

fn provision_python_pyright_command(
    config: &PyrightLspConfig,
) -> Result<Option<Vec<String>>, PluginRuntimeError> {
    let Some(python) = find_python() else {
        return Ok(None);
    };
    let root = pyright_python_root(config)?;
    let marker = root.join("pyright").join("langserver.py");
    if !marker.exists() {
        fs::create_dir_all(&root).map_err(|err| {
            PluginRuntimeError::PyrightLsp(format!(
                "create pyright_lsp Python asset root {}: {err}",
                root.display()
            ))
        })?;
        let output = Command::new(&python)
            .args([
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--target",
            ])
            .arg(&root)
            .arg(format!("pyright=={PYRIGHT_PYPI_VERSION}"))
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .map_err(|err| {
                PluginRuntimeError::PyrightLsp(format!(
                    "install pyright_lsp Python assets with {}: {err}",
                    python.display()
                ))
            })?;
        if !output.status.success() {
            return Err(PluginRuntimeError::PyrightLsp(format!(
                "install pyright_lsp Python assets failed: {}",
                bounded_command_output(&output.stderr, &output.stdout)
            )));
        }
    }
    Ok(Some(vec![
        "/bin/sh".to_owned(),
        "-lc".to_owned(),
        format!(
            "PYTHONPATH={} exec {} -m pyright.langserver --stdio",
            shell_quote(&root),
            shell_quote(&python)
        ),
    ]))
}

fn pyright_python_root(config: &PyrightLspConfig) -> Result<PathBuf, PluginRuntimeError> {
    let parent = config.workspace_root.parent().ok_or_else(|| {
        PluginRuntimeError::PyrightLsp(format!(
            "pyright_lsp workspace_root has no parent: {}",
            config.workspace_root.display()
        ))
    })?;
    Ok(parent.join("python"))
}

fn find_python() -> Option<PathBuf> {
    find_executable("python3")
        .or_else(|| find_executable("python"))
        .or_else(|| {
            [
                "/opt/miniconda3/bin/python",
                "/usr/local/bin/python3",
                "/usr/bin/python3",
            ]
            .into_iter()
            .map(PathBuf::from)
            .find(|candidate| candidate.exists())
        })
}

fn shell_quote(path: &Path) -> String {
    let value = path.to_string_lossy();
    format!("'{}'", value.replace('\'', "'\\''"))
}

fn bounded_command_output(stderr: &[u8], stdout: &[u8]) -> String {
    let mut text = String::new();
    if !stderr.is_empty() {
        text.push_str("stderr=");
        text.push_str(&String::from_utf8_lossy(stderr));
    }
    if !stdout.is_empty() {
        if !text.is_empty() {
            text.push_str("; ");
        }
        text.push_str("stdout=");
        text.push_str(&String::from_utf8_lossy(stdout));
    }
    const LIMIT: usize = 4096;
    if text.len() > LIMIT {
        text.truncate(LIMIT);
        text.push_str("...");
    }
    text
}

fn find_executable(name: &str) -> Option<PathBuf> {
    std::env::var_os("PATH").and_then(|path| {
        std::env::split_paths(&path)
            .map(|dir| dir.join(name))
            .find(|candidate| candidate.exists())
    })
}

struct LspProcess {
    child: Child,
    process_group_id: Option<i32>,
    stdin: Arc<Mutex<ChildStdin>>,
    pending: Arc<Mutex<BTreeMap<String, Sender<Result<Value, String>>>>>,
    diagnostics: Arc<DiagnosticsState>,
    open_documents: BTreeMap<String, i64>,
    next_id: AtomicU64,
    reader: Option<JoinHandle<()>>,
    torn_down: bool,
}

impl LspProcess {
    fn start(
        command: Vec<String>,
        workspace_root: &Path,
        max_response_bytes: usize,
    ) -> Result<Self, String> {
        let Some((program, args)) = command.split_first() else {
            return Err("pyright_lsp command is empty".to_owned());
        };
        let mut command = Command::new(program);
        command
            .args(args)
            .current_dir(workspace_root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let mut child = command.spawn().map_err(|err| err.to_string())?;
        let process_group_id = i32::try_from(child.id()).ok();
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "failed to open pyright_lsp stdin".to_owned())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "failed to open pyright_lsp stdout".to_owned())?;
        let stdin = Arc::new(Mutex::new(stdin));
        let pending = Arc::new(Mutex::new(BTreeMap::new()));
        let diagnostics = Arc::new(DiagnosticsState::default());
        let reader = spawn_reader(
            stdout,
            Arc::clone(&stdin),
            Arc::clone(&pending),
            Arc::clone(&diagnostics),
            max_response_bytes,
        );
        Ok(Self {
            child,
            process_group_id,
            stdin,
            pending,
            diagnostics,
            open_documents: BTreeMap::new(),
            next_id: AtomicU64::new(1),
            reader: Some(reader),
            torn_down: false,
        })
    }

    fn initialize(&mut self, workspace_root: &Path, timeout: Duration) -> Result<Value, String> {
        let root_uri = file_uri(workspace_root);
        let result = self.request(
            "initialize",
            json!({
                "processId": Value::Null,
                "rootUri": root_uri,
                "workspaceFolders": [{
                    "uri": root_uri,
                    "name": "pyright_lsp",
                }],
                "capabilities": {
                    "textDocument": {
                        "publishDiagnostics": {
                            "relatedInformation": true,
                            "versionSupport": true,
                        }
                    },
                    "workspace": {
                        "configuration": true,
                        "didChangeWatchedFiles": {
                            "dynamicRegistration": false,
                        }
                    }
                }
            }),
            timeout,
        )?;
        self.notify("initialized", json!({}))?;
        Ok(result)
    }

    fn document_symbols(
        &mut self,
        projection_root: &Path,
        file_path: &str,
        query: Option<&str>,
    ) -> Result<Vec<Value>, PluginRuntimeError> {
        let uri = target_file_uri(projection_root, file_path)?;
        let result = self
            .request(
                "textDocument/documentSymbol",
                json!({ "textDocument": { "uri": uri } }),
                Duration::from_secs(30),
            )
            .map_err(PluginRuntimeError::PyrightLsp)?;
        let mut symbols = Vec::new();
        let query = query.map(str::to_ascii_lowercase);
        flatten_symbols(
            &result,
            projection_root,
            file_path,
            query.as_deref(),
            &mut symbols,
        );
        Ok(symbols)
    }

    fn definition(
        &mut self,
        projection_root: &Path,
        file_path: &str,
        line: u64,
        character: u64,
    ) -> Result<Vec<Value>, PluginRuntimeError> {
        let uri = target_file_uri(projection_root, file_path)?;
        let result = self
            .request(
                "textDocument/definition",
                json!({
                    "textDocument": { "uri": uri },
                    "position": { "line": line, "character": character },
                }),
                Duration::from_secs(30),
            )
            .map_err(PluginRuntimeError::PyrightLsp)?;
        Ok(locations_from_lsp_result(&result, projection_root))
    }

    fn references(
        &mut self,
        projection_root: &Path,
        file_path: &str,
        line: u64,
        character: u64,
        include_declaration: bool,
    ) -> Result<Vec<Value>, PluginRuntimeError> {
        let uri = target_file_uri(projection_root, file_path)?;
        let result = self
            .request(
                "textDocument/references",
                json!({
                    "textDocument": { "uri": uri },
                    "position": { "line": line, "character": character },
                    "context": { "includeDeclaration": include_declaration },
                }),
                Duration::from_secs(30),
            )
            .map_err(PluginRuntimeError::PyrightLsp)?;
        Ok(locations_from_lsp_result(&result, projection_root))
    }

    fn diagnostics(
        &mut self,
        projection_root: &Path,
        file_path: &str,
        timeout: Duration,
    ) -> Result<Vec<Value>, String> {
        let uri =
            target_file_uri_string(projection_root, file_path).map_err(|err| err.to_string())?;
        self.diagnostics.wait_for_uri(&uri, timeout).map(|items| {
            items
                .into_iter()
                .map(|diagnostic| diagnostic_value(&uri, projection_root, diagnostic))
                .collect()
        })
    }

    fn open_document(
        &mut self,
        projection_root: &Path,
        file_path: &str,
        manifest_version: i64,
    ) -> Result<(), PluginRuntimeError> {
        let normalized = LayerPath::parse(file_path).map_err(|err| {
            PluginRuntimeError::InvalidRequest(format!("invalid file_path for pyright_lsp: {err}"))
        })?;
        let path = projection_root.join(normalized.as_str());
        let text = std::fs::read_to_string(&path).map_err(|err| {
            PluginRuntimeError::InvalidRequest(format!(
                "pyright_lsp target file is not readable: {}: {err}",
                normalized.as_str()
            ))
        })?;
        let uri = file_uri(&path);
        let version = manifest_version.max(0);
        if self.open_documents.contains_key(&uri) {
            self.notify(
                "textDocument/didChange",
                json!({
                    "textDocument": {
                        "uri": uri,
                        "version": version,
                    },
                    "contentChanges": [{ "text": text }],
                }),
            )
            .map_err(PluginRuntimeError::PyrightLsp)?;
        } else {
            self.notify(
                "textDocument/didOpen",
                json!({
                    "textDocument": {
                        "uri": uri,
                        "languageId": LANGUAGE_ID,
                        "version": version,
                        "text": text,
                    }
                }),
            )
            .map_err(PluginRuntimeError::PyrightLsp)?;
        }
        self.open_documents.insert(uri, version);
        Ok(())
    }

    fn request(&self, method: &str, params: Value, timeout: Duration) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let key = id.to_string();
        let (tx, rx) = mpsc::channel();
        self.pending
            .lock()
            .map_err(|_| "pyright_lsp pending map lock poisoned".to_owned())?
            .insert(key.clone(), tx);
        let message = json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });
        if let Err(err) = self.write_message(&message) {
            let _ = self.pending.lock().map(|mut pending| pending.remove(&key));
            return Err(err);
        }
        match rx.recv_timeout(timeout) {
            Ok(Ok(value)) => Ok(value),
            Ok(Err(err)) => Err(err),
            Err(mpsc::RecvTimeoutError::Timeout) => {
                let _ = self.pending.lock().map(|mut pending| pending.remove(&key));
                Err(format!("pyright_lsp request {method} timed out"))
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                Err(format!("pyright_lsp request {method} disconnected"))
            }
        }
    }

    fn notify(&self, method: &str, params: Value) -> Result<(), String> {
        self.write_message(&json!({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }))
    }

    fn write_message(&self, message: &Value) -> Result<(), String> {
        let mut stdin = self
            .stdin
            .lock()
            .map_err(|_| "pyright_lsp stdin lock poisoned".to_owned())?;
        write_lsp_message(&mut *stdin, message).map_err(|err| err.to_string())
    }

    fn pid(&self) -> u32 {
        self.child.id()
    }

    fn is_running(&mut self) -> bool {
        self.child.try_wait().ok().flatten().is_none()
    }

    fn teardown(&mut self) {
        if self.torn_down {
            return;
        }
        self.torn_down = true;
        terminate_process_group(self.process_group_id);
        let _ = self.child.kill();
        let _ = self.child.wait();
        if let Some(reader) = self.reader.take() {
            let _ = reader.join();
        }
    }
}

impl Drop for LspProcess {
    fn drop(&mut self) {
        self.teardown();
    }
}

#[derive(Default)]
struct DiagnosticsState {
    entries: Mutex<BTreeMap<String, Vec<Value>>>,
    changed: Condvar,
}

impl DiagnosticsState {
    fn record(&self, uri: String, diagnostics: Vec<Value>) {
        if let Ok(mut entries) = self.entries.lock() {
            entries.insert(uri, diagnostics);
            self.changed.notify_all();
        }
    }

    fn wait_for_uri(&self, uri: &str, timeout: Duration) -> Result<Vec<Value>, String> {
        let deadline = Instant::now() + timeout;
        let mut entries = self
            .entries
            .lock()
            .map_err(|_| "pyright_lsp diagnostics lock poisoned".to_owned())?;
        loop {
            if let Some(diagnostics) = entries.get(uri) {
                return Ok(diagnostics.clone());
            }
            let now = Instant::now();
            if now >= deadline {
                return Err(format!("timed out waiting for diagnostics for {uri}"));
            }
            let remaining = deadline.saturating_duration_since(now);
            let (next_entries, timeout) = self
                .changed
                .wait_timeout(entries, remaining)
                .map_err(|_| "pyright_lsp diagnostics lock poisoned".to_owned())?;
            entries = next_entries;
            if timeout.timed_out() {
                return Err(format!("timed out waiting for diagnostics for {uri}"));
            }
        }
    }
}

fn spawn_reader(
    stdout: std::process::ChildStdout,
    stdin: Arc<Mutex<ChildStdin>>,
    pending: Arc<Mutex<BTreeMap<String, Sender<Result<Value, String>>>>>,
    diagnostics: Arc<DiagnosticsState>,
    max_response_bytes: usize,
) -> JoinHandle<()> {
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        loop {
            let message = match read_lsp_message(&mut reader, max_response_bytes) {
                Ok(Some(message)) => message,
                Ok(None) => return,
                Err(err) => {
                    fail_pending(&pending, err);
                    return;
                }
            };
            handle_lsp_message(&message, &stdin, &pending, &diagnostics);
        }
    })
}

fn handle_lsp_message(
    message: &Value,
    stdin: &Arc<Mutex<ChildStdin>>,
    pending: &Arc<Mutex<BTreeMap<String, Sender<Result<Value, String>>>>>,
    diagnostics: &DiagnosticsState,
) {
    if let Some(method) = message.get("method").and_then(Value::as_str) {
        if method == "textDocument/publishDiagnostics" {
            if let Some(params) = message.get("params") {
                let uri = params
                    .get("uri")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_owned();
                let items = params
                    .get("diagnostics")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                if !uri.is_empty() {
                    diagnostics.record(uri, items);
                }
            }
        }
        if message.get("id").is_some() {
            respond_to_server_request(message, stdin);
        }
        return;
    }

    let Some(id) = message.get("id").map(lsp_id_key) else {
        return;
    };
    let sender = pending
        .lock()
        .ok()
        .and_then(|mut pending| pending.remove(&id));
    if let Some(sender) = sender {
        let _ = if let Some(error) = message.get("error") {
            sender.send(Err(error.to_string()))
        } else {
            sender.send(Ok(message.get("result").cloned().unwrap_or(Value::Null)))
        };
    }
}

fn respond_to_server_request(message: &Value, stdin: &Arc<Mutex<ChildStdin>>) {
    let Some(id) = message.get("id").cloned() else {
        return;
    };
    let method = message
        .get("method")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let result = match method {
        "workspace/configuration" => {
            let count = message
                .get("params")
                .and_then(|params| params.get("items"))
                .and_then(Value::as_array)
                .map_or(1, Vec::len);
            Value::Array((0..count).map(|_| json!({})).collect())
        }
        _ => Value::Null,
    };
    if let Ok(mut stdin) = stdin.lock() {
        let _ = write_lsp_message(
            &mut *stdin,
            &json!({
                "jsonrpc": "2.0",
                "id": id,
                "result": result,
            }),
        );
    }
}

fn fail_pending(
    pending: &Arc<Mutex<BTreeMap<String, Sender<Result<Value, String>>>>>,
    error: String,
) {
    if let Ok(mut pending) = pending.lock() {
        let senders = std::mem::take(&mut *pending);
        for sender in senders.into_values() {
            let _ = sender.send(Err(error.clone()));
        }
    }
}

fn read_lsp_message(
    reader: &mut BufReader<std::process::ChildStdout>,
    max_response_bytes: usize,
) -> Result<Option<Value>, String> {
    let mut content_length = None;
    loop {
        let mut line = String::new();
        let read = reader.read_line(&mut line).map_err(|err| err.to_string())?;
        if read == 0 {
            return Ok(None);
        }
        let line = line.trim_end_matches(['\r', '\n']);
        if line.is_empty() {
            break;
        }
        if let Some(value) = line.strip_prefix("Content-Length:") {
            let length = value
                .trim()
                .parse::<usize>()
                .map_err(|err| format!("invalid LSP Content-Length: {err}"))?;
            content_length = Some(length);
        }
    }
    let content_length = content_length.ok_or_else(|| "missing LSP Content-Length".to_owned())?;
    if content_length > max_response_bytes {
        return Err(format!(
            "pyright_lsp response exceeds {} byte limit",
            max_response_bytes
        ));
    }
    let mut body = vec![0_u8; content_length];
    reader
        .read_exact(&mut body)
        .map_err(|err| err.to_string())?;
    serde_json::from_slice(&body).map_err(|err| err.to_string())
}

fn write_lsp_message(writer: &mut impl Write, message: &Value) -> std::io::Result<()> {
    let body = serde_json::to_vec(message).map_err(std::io::Error::other)?;
    write!(writer, "Content-Length: {}\r\n\r\n", body.len())?;
    writer.write_all(&body)?;
    writer.flush()
}

fn lsp_id_key(id: &Value) -> String {
    match id {
        Value::String(value) => value.clone(),
        Value::Number(value) => value.to_string(),
        _ => id.to_string(),
    }
}

fn target_file_uri(projection_root: &Path, file_path: &str) -> Result<String, PluginRuntimeError> {
    target_file_uri_string(projection_root, file_path)
}

fn target_file_uri_string(
    projection_root: &Path,
    file_path: &str,
) -> Result<String, PluginRuntimeError> {
    let normalized = LayerPath::parse(file_path).map_err(|err| {
        PluginRuntimeError::InvalidRequest(format!("invalid file_path for pyright_lsp: {err}"))
    })?;
    Ok(file_uri(&projection_root.join(normalized.as_str())))
}

fn flatten_symbols(
    value: &Value,
    projection_root: &Path,
    fallback_file_path: &str,
    query: Option<&str>,
    out: &mut Vec<Value>,
) {
    let Some(items) = value.as_array() else {
        return;
    };
    for item in items {
        flatten_symbol_item(item, projection_root, fallback_file_path, query, out);
    }
}

fn flatten_symbol_item(
    item: &Value,
    projection_root: &Path,
    fallback_file_path: &str,
    query: Option<&str>,
    out: &mut Vec<Value>,
) {
    let name = item.get("name").and_then(Value::as_str).unwrap_or_default();
    let matched = query.is_none_or(|query| name.to_ascii_lowercase().contains(query));
    let range = item
        .get("range")
        .cloned()
        .or_else(|| {
            item.get("location")
                .and_then(|location| location.get("range"))
                .cloned()
        })
        .unwrap_or(Value::Null);
    let selection_range = item
        .get("selectionRange")
        .cloned()
        .unwrap_or_else(|| range.clone());
    let file_path = item
        .get("location")
        .and_then(|location| location.get("uri"))
        .and_then(Value::as_str)
        .and_then(|uri| file_path_from_uri(uri, projection_root))
        .unwrap_or_else(|| fallback_file_path.to_owned());
    if matched && !name.is_empty() {
        out.push(json!({
            "name": name,
            "kind": item.get("kind").cloned().unwrap_or(Value::Null),
            "file_path": file_path,
            "range": range,
            "selection_range": selection_range,
        }));
    }
    if let Some(children) = item.get("children") {
        flatten_symbols(children, projection_root, fallback_file_path, query, out);
    }
}

fn locations_from_lsp_result(result: &Value, projection_root: &Path) -> Vec<Value> {
    match result {
        Value::Null => Vec::new(),
        Value::Array(items) => items
            .iter()
            .filter_map(|item| location_from_lsp_value(item, projection_root))
            .collect(),
        Value::Object(_) => location_from_lsp_value(result, projection_root)
            .into_iter()
            .collect(),
        _ => Vec::new(),
    }
}

fn location_from_lsp_value(value: &Value, projection_root: &Path) -> Option<Value> {
    if let Some(uri) = value.get("uri").and_then(Value::as_str) {
        return Some(json!({
            "uri": uri,
            "file_path": file_path_from_uri(uri, projection_root).unwrap_or_else(|| uri.to_owned()),
            "range": value.get("range").cloned().unwrap_or(Value::Null),
        }));
    }
    if let Some(uri) = value.get("targetUri").and_then(Value::as_str) {
        return Some(json!({
            "uri": uri,
            "file_path": file_path_from_uri(uri, projection_root).unwrap_or_else(|| uri.to_owned()),
            "range": value.get("targetRange").cloned().unwrap_or(Value::Null),
            "selection_range": value.get("targetSelectionRange").cloned().unwrap_or(Value::Null),
        }));
    }
    None
}

fn diagnostic_value(uri: &str, projection_root: &Path, diagnostic: Value) -> Value {
    json!({
        "uri": uri,
        "file_path": file_path_from_uri(uri, projection_root).unwrap_or_else(|| uri.to_owned()),
        "range": diagnostic.get("range").cloned().unwrap_or(Value::Null),
        "severity": diagnostic.get("severity").cloned().unwrap_or(Value::Null),
        "code": diagnostic.get("code").cloned().unwrap_or(Value::Null),
        "source": diagnostic.get("source").cloned().unwrap_or(Value::Null),
        "message": diagnostic.get("message").cloned().unwrap_or(Value::Null),
        "raw": diagnostic,
    })
}

fn file_uri(path: &Path) -> String {
    let raw = path.to_string_lossy();
    let mut uri = String::from("file://");
    for byte in raw.as_bytes() {
        match *byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'/' | b'-' | b'_' | b'.' | b'~' => {
                uri.push(char::from(*byte))
            }
            other => {
                const HEX: &[u8; 16] = b"0123456789ABCDEF";
                uri.push('%');
                uri.push(char::from(HEX[usize::from(other >> 4)]));
                uri.push(char::from(HEX[usize::from(other & 0x0f)]));
            }
        }
    }
    uri
}

fn file_path_from_uri(uri: &str, projection_root: &Path) -> Option<String> {
    let path = uri.strip_prefix("file://")?;
    let decoded = percent_decode(path)?;
    let path = PathBuf::from(decoded);
    path.strip_prefix(projection_root)
        .ok()
        .map(|path| path.to_string_lossy().trim_start_matches('/').to_owned())
}

fn percent_decode(value: &str) -> Option<String> {
    let bytes = value.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' {
            let hi = *bytes.get(index + 1)?;
            let lo = *bytes.get(index + 2)?;
            out.push((hex_value(hi)? << 4) | hex_value(lo)?);
            index += 3;
        } else {
            out.push(bytes[index]);
            index += 1;
        }
    }
    String::from_utf8(out).ok()
}

fn hex_value(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

fn terminate_process_group(process_group_id: Option<i32>) {
    let Some(pgid) = process_group_id else {
        return;
    };
    let pid = nix::unistd::Pid::from_raw(pgid);
    if nix::sys::signal::killpg(pid, nix::sys::signal::Signal::SIGTERM).is_ok() {
        std::thread::sleep(Duration::from_millis(50));
    }
    let _ = nix::sys::signal::killpg(pid, nix::sys::signal::Signal::SIGKILL);
}
