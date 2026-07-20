# Changelog

All notable changes to Ephemeral Sandbox will be documented in this file.

Version entries are grouped by product milestone. The repository does not have
release tags yet, so the commit anchors below are the intended Git tag targets.

## v0.1.3 - 2026-07-19

Core anchor: `ephemeral-sandbox@dde5da957`

Runtime hardening and platform setup.

### Added

- Added daemon disk polling for observability.
- Added Windows sandbox configuration support.
- Added Linux Multipass sandbox setup support.

### Changed

- Hardened runtime lifecycle handling, shutdown diagnostics, workspace session
  teardown, and resource accounting.
- Reduced background worker churn.

## v0.1.2 - 2026-07-18

Core anchor: `ephemeral-sandbox@005093d12`

Console extraction and observability hardening.

### Added

- Added coverage for permission-denied telemetry storage drops.

### Changed

- Completed console extraction and core cleanup.
- Isolated resource sampling.
- Merged daemon topology into resource metrics.
- Improved current fleet resource usage reporting.
- Kept platform policy out of daemon sources.

## v0.1.1 - 2026-07-17

Core anchor: `ephemeral-sandbox@07d8bce43`

Workspace sessions and process/resource observability.

### Added

- Exposed cgroup topology and workspace sessions.
- Added workspace process topology.
- Completed resource isolation support.
- Added workspace CPU and memory estimates.

### Changed

- Enforced disk-only telemetry state.
- Improved terminal session workflow and snapshot polling.
- Stopped idle daemon polling.
- Forwarded cgroup topology through manager and merged daemon topology in cgroup
  responses.

## v0.1.0 - 2026-07-15

Website anchor: `ephemeral-sandbox-website@4bfcc62`
Core baseline anchor: `ephemeral-sandbox@0bc6a7090`

First public documentation and site baseline.

### Added

- Published the static website.
- Built the documentation shell and architecture overview.
- Added architecture documentation pages.
- Built the multilingual documentation experience.

### Changed

- Completed Chinese documentation localization.
- Fixed Chinese architecture navigation and production Chinese TOC heading.
- Refreshed the project README and license.
