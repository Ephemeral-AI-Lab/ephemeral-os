mod publish_changes;
mod squash;

use crate::operation::OperationEntry;

const SQUASH: OperationEntry = OperationEntry::cli(&squash::SPEC, squash::dispatch);

pub(crate) const OPERATIONS: &[OperationEntry] = &[SQUASH];
