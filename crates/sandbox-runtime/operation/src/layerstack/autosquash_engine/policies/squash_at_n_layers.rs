pub(crate) const NAME: &str = "squash_at_n_layers";

#[must_use]
pub(crate) fn matches(active_layers: usize, threshold: usize) -> bool {
    active_layers >= threshold
}
