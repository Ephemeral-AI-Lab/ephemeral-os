use sha2::{Digest, Sha256};

use crate::error::LayerStackError;
use crate::model::{hex_lower, LayerPath, Manifest};
use crate::stack::projection::MergedEntry;
use crate::stack::MergedView;

use super::model::ContentFingerprint;

pub(crate) fn content_fingerprint(
    view: &MergedView,
    manifest: &Manifest,
    path: &LayerPath,
) -> Result<ContentFingerprint, LayerStackError> {
    match view.read_entry(path.as_str(), manifest)? {
        MergedEntry::Absent => Ok(ContentFingerprint::Absent),
        MergedEntry::File { bytes, executable } => {
            let mut hasher = Sha256::new();
            hasher.update(bytes);
            Ok(ContentFingerprint::File {
                digest: hex_lower(hasher.finalize()),
                executable: Some(executable),
            })
        }
        MergedEntry::Symlink { target } => Ok(ContentFingerprint::Symlink { target }),
        MergedEntry::Directory => Ok(ContentFingerprint::Directory),
    }
}
