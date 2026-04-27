## v1.6.0 (2026-04-27)

### Feat

- add task-board AI assist models and client methods (PRA-329) (#36)

## v1.5.0 (2026-04-25)

### Feat

- add task graph models and client methods

## v1.4.1 (2026-04-25)

### Fix

- **ci**: detect new commitizen no-commits output (#34)

## v1.4.0 (2026-04-24)

### Feat

- kind-based ProviderAuthor + prefix/name split + delete TrustTier (PRA-369) (#32)

## v1.3.0 (2026-04-22)

### Feat

- **sdk**: allow ProviderAuthor.organization_id to be None for platform-owned providers (PRA-361) (#31)

## v1.2.0 (2026-04-20)

### Feat

- **sdk**: add wait/timeout kwargs to Resource.apply() (#30)

## v1.1.0 (2026-04-14)

### Feat

- **sdk**: add orphan_resources field and ProjectHasResourcesError

## v1.0.0 (2026-04-14)

### BREAKING CHANGE

- Resources now require project_id at construction time.
The flat slash-based identifier format is gone -- use
ResourceIdentity.canonical / ResourceIdentity.parse. Top-level resource
methods on PragmaClient / AsyncPragmaClient are removed; route resource
operations through `client.project(project_id)`. All downstream
consumers (pragma-os API, pragma-cli, pragma-providers, and the web
TypeScript mirror) must update to the new surface before upgrading.

### Feat

- **sdk**: canonical resource identity and project foundation (#29)

## v0.36.0 (2026-04-12)

### Feat

- **sdk**: add LLM catalog, organization settings, and client methods
- **sdk**: add managed_by ownership field to Resource base

## v0.35.0 (2026-04-11)

### Feat

- add agent models (AgentType, AgentInstance, Task, enums)

## v0.34.1 (2026-04-04)

### Refactor

- remove direct provider cascade from SDK publish

## v0.34.0 (2026-04-03)

### Feat

- add icon_url parameter to publish_provider

## v0.33.1 (2026-04-03)

### Fix

- default API URL to production instead of localhost

## v0.33.0 (2026-03-27)

### Feat

- add on_copy and on_patch optional lifecycle methods to Resource (PRA-286) (#26)

## v0.32.4 (2026-03-25)

### Fix

- **ci**: pull --rebase before push to prevent race conditions

## v0.32.3 (2026-03-25)

### Refactor

- remove provider identity from Python classes (PRA-269)

## v0.32.2 (2026-03-19)

### Fix

- remove force parameter from publish_provider (#25)

## v0.32.1 (2026-03-18)

### Fix

- use query params for resource get/delete/deactivate methods (#24)

## v0.32.0 (2026-03-17)

## v0.30.0 (2026-03-16)

### Feat

- expose resource ID as provider/resource/name (#21)

## v0.29.1 (2026-03-16)

### Fix

- unset VIRTUAL_ENV in ty pre-commit hook (#20)

## v0.29.0 (2026-03-16)

### Feat

- add deactivate_resource method to sync and async clients (#19)

## v0.28.0 (2026-03-16)

### Feat

- add config parameter to install_provider for provider-level env vars (#17)
- provider migration framework (PRA-226) (#14)

### Refactor

- rename provider models and client methods (PRA-253) (#15)

## v0.26.0 (2026-03-04)

## v0.25.0 (2026-03-04)

### Feat

- forward ref validation + Resource description field (PRA-139, PRA-141) (#12)

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
