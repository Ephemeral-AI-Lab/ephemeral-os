use thiserror::Error;

use crate::yaml::Value;

/// Internal merge failures.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum MergeConflict {
    /// The baseline document root must be a YAML object.
    #[error("baseline config document root must be a YAML mapping")]
    BaselineRoot,
}

pub(crate) fn merge_values(
    baseline: &mut Value,
    override_value: Value,
) -> Result<(), MergeConflict> {
    match (baseline, override_value) {
        (Value::Mapping(base), Value::Mapping(override_map)) => {
            for (key, value) in override_map {
                match base.get_mut(&key) {
                    Some(existing) => merge_values(existing, value)?,
                    None => {
                        base.insert(key, value);
                    }
                }
            }
            Ok(())
        }
        (base, override_value) => {
            *base = override_value;
            Ok(())
        }
    }
}

pub(crate) fn ensure_mapping_root(value: &Value) -> Result<(), MergeConflict> {
    if matches!(value, Value::Mapping(_)) {
        Ok(())
    } else {
        Err(MergeConflict::BaselineRoot)
    }
}
