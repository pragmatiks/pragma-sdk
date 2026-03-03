## v0.24.0 (2026-03-03)

### Feat

- add Sensitive field type for schema-driven masking (PRA-227) (#11)

## v0.23.0 (2026-03-01)

### Feat

- add ImmutableField and ImmutableDependency type annotations (PRA-225) (#10)

## v0.22.0 (2026-02-27)

### Feat

- add metadata fields to publish_provider() for store auto-create (#9)

## v0.21.1 (2026-02-26)

### Refactor

- unify provider API surface with org/name namespacing (#8)

## v0.21.0 (2026-02-24)

### Feat

- add store SDK models and client methods (#7)

## v0.20.0 (2026-02-24)

### Feat

- add extract_metadata() for provider store metadata (PRA-193) (#6)

## v0.19.0 (2026-02-23)

### Feat

- add WAITING and DELETING lifecycle states (#5)

## v0.18.0 (2026-01-31)

### Feat

- add upload_file method to PragmaClient

## v0.17.1 (2026-01-31)

### Fix

- **models**: normalize slashes in resource IDs (#3)

## v0.17.0 (2026-01-30)

### Feat

- add owner context for automatic owner_references (#2)

## v0.16.0 (2026-01-29)

### Feat

- trigger pragma-os update on SDK publish
- export API consumer types from top-level module

## v0.15.6 (2026-01-28)

### Refactor

- split models into package and align tooling (#1)

## v0.15.5 (2026-01-25)

### Fix

- **ci**: detect actual version change before publishing

## v0.15.4 (2026-01-25)

### Fix

- **ci**: detect actual version change before publishing

## v0.15.3 (2026-01-25)

### Fix

- **ci**: correct repo names for cross-repo triggers

## v0.15.2 (2026-01-22)

### Fix

- read [tool.pragma] package for schema extraction

## v0.15.1 (2026-01-16)

### Fix

- remove comments violating no-comments policy

## v0.15.0 (2026-01-16)

### Feat

- **sdk**: add subresource support with OwnerReference, apply(), wait_ready()

## v0.14.0 (2026-01-16)

### Feat

- **sdk**: add is_field_ref_marker() for reactive FieldReferences

## v0.13.0 (2026-01-16)

### Feat

- **sdk**: add is_dependency_marker utility function

## v0.12.0 (2026-01-16)

### Feat

- add Dependency[T] generic type for whole-instance resource access

## v0.11.0 (2026-01-16)

### Feat

- Add list_resource_types method to SDK clients

## v0.10.3 (2026-01-15)

### Refactor

- use BuildInfo for build status responses

## v0.10.2 (2026-01-15)

### Refactor

- hide internal details from API responses

## v0.10.1 (2026-01-15)

### Refactor

- simplify ProviderDeleteResult to hide internal details

## v0.10.0 (2026-01-15)

### Feat

- add ProviderStatus model with minimal user-facing fields

## v0.9.0 (2026-01-15)

### Feat

- **sdk**: change deploy_provider to accept version instead of image

## v0.8.0 (2026-01-15)

### Feat

- **provider**: add schema extraction module

## v0.7.0 (2026-01-15)

### Feat

- **sdk**: add UserInfo model and get_me method

## v0.6.0 (2026-01-14)

### Feat

- add provider list, rollback, status, and builds methods

## v0.5.0 (2026-01-14)

### Feat

- update PushResult to use CalVer version instead of build_id

## v0.4.0 (2026-01-14)

### Feat

- add ProviderDeleteResult model and delete_provider client method

## v0.3.1 (2026-01-13)

### Fix

- add pypi environment for trusted publisher

## v0.3.0 (2026-01-13)

### Feat

- add PyPI publishing with trusted publisher

## v0.2.1 (2026-01-13)

### Refactor

- use absolute imports instead of relative

## v0.2.0 (2026-01-12)

### Feat

- trigger CLI update on SDK release

## v0.1.3 (2026-01-12)

### Fix

- **ci**: only upload .tar.gz and .whl to release

## v0.1.2 (2026-01-12)

### Fix

- ensure trailing newline in README

## v0.1.1 (2026-01-12)

### Fix

- update README to use correct project name

## v0.1.0 (2026-01-12)

### Feat

- initial SDK release
