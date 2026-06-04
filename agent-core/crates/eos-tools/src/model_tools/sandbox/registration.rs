use std::sync::Arc;

use schemars::schema_for;

use crate::config::ToolConfigSet;
use crate::name::ToolName;
use crate::registry::ToolRegistry;
use crate::result::OutputShape;
use crate::spec::json_spec;

use super::super::register_tool;
use super::command::{ExecCommand, ExecCommandInput, WriteStdin, WriteStdinInput};
use super::files::{
    EditFile, EditFileInput, MultiEdit, MultiEditInput, ReadFile, ReadFileInput, WriteFile,
    WriteFileInput,
};
use super::outputs::{CommandToolOutput, GlobOutput, GrepOutput, MutationOutput, ReadFileOutput};
use super::search::{Glob, GlobInput, Grep, GrepInput};

pub(super) fn register(registry: &mut ToolRegistry, config: &ToolConfigSet) {
    let read_file = config.get(ToolName::ReadFile);
    register_tool(
        registry,
        ToolName::ReadFile,
        read_file,
        json_spec(
            ToolName::ReadFile,
            &read_file.description,
            schema_for!(ReadFileInput),
            schema_for!(ReadFileOutput),
        ),
        OutputShape::json::<ReadFileOutput>("ReadFileOutput"),
        Arc::new(ReadFile),
    );
    let write_file = config.get(ToolName::WriteFile);
    register_tool(
        registry,
        ToolName::WriteFile,
        write_file,
        json_spec(
            ToolName::WriteFile,
            &write_file.description,
            schema_for!(WriteFileInput),
            schema_for!(MutationOutput),
        ),
        OutputShape::json::<MutationOutput>("WriteFileOutput"),
        Arc::new(WriteFile),
    );
    let edit_file = config.get(ToolName::EditFile);
    register_tool(
        registry,
        ToolName::EditFile,
        edit_file,
        json_spec(
            ToolName::EditFile,
            &edit_file.description,
            schema_for!(EditFileInput),
            schema_for!(MutationOutput),
        ),
        OutputShape::json::<MutationOutput>("EditFileOutput"),
        Arc::new(EditFile),
    );
    let multi_edit = config.get(ToolName::MultiEdit);
    register_tool(
        registry,
        ToolName::MultiEdit,
        multi_edit,
        json_spec(
            ToolName::MultiEdit,
            &multi_edit.description,
            schema_for!(MultiEditInput),
            schema_for!(MutationOutput),
        ),
        OutputShape::json::<MutationOutput>("MultiEditOutput"),
        Arc::new(MultiEdit),
    );
    let exec_command = config.get(ToolName::ExecCommand);
    register_tool(
        registry,
        ToolName::ExecCommand,
        exec_command,
        json_spec(
            ToolName::ExecCommand,
            &exec_command.description,
            schema_for!(ExecCommandInput),
            schema_for!(CommandToolOutput),
        ),
        OutputShape::json::<CommandToolOutput>("CommandToolOutput"),
        Arc::new(ExecCommand),
    );
    let write_stdin = config.get(ToolName::WriteStdin);
    register_tool(
        registry,
        ToolName::WriteStdin,
        write_stdin,
        json_spec(
            ToolName::WriteStdin,
            &write_stdin.description,
            schema_for!(WriteStdinInput),
            schema_for!(CommandToolOutput),
        ),
        OutputShape::json::<CommandToolOutput>("CommandToolOutput"),
        Arc::new(WriteStdin),
    );
    let glob = config.get(ToolName::Glob);
    register_tool(
        registry,
        ToolName::Glob,
        glob,
        json_spec(
            ToolName::Glob,
            &glob.description,
            schema_for!(GlobInput),
            schema_for!(GlobOutput),
        ),
        OutputShape::json::<GlobOutput>("GlobOutput"),
        Arc::new(Glob),
    );
    let grep = config.get(ToolName::Grep);
    register_tool(
        registry,
        ToolName::Grep,
        grep,
        json_spec(
            ToolName::Grep,
            &grep.description,
            schema_for!(GrepInput),
            schema_for!(GrepOutput),
        ),
        OutputShape::json::<GrepOutput>("GrepOutput"),
        Arc::new(Grep),
    );
}
