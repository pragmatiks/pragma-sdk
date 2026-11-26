# Host protocol

The contract between the Pragmatiks host and the worker and introspection entry points in `src/pragma_sdk/protocol/`, `worker/` and `introspection/`, at protocol 10.

## Contract

`protocol/` imports nothing from the SDK but `types`; the Pragmatiks host speaks only it. Changing a frame shape, the values an enum in a frame takes (such as the `LifecycleState` a `read_resource` result carries, which a worker validates against the states it knows), the introspection report or what its schemas mean, the config reference markers, or the worker's method set is a new `PROTOCOL_VERSION`.

The worker serves only over the connected Unix socket the host passes as an inherited descriptor (`python -m pragma_sdk.worker --socket-descriptor <number>`): it creates no socket file and listens for nothing, so the host's end is the only way in. `worker/__main__.py` adopts the descriptor (`adopt_host_socket`: a descriptor below 3, one that is not open, and one that does not hold a connected Unix stream socket are each refused, the last two as `worker_socket_descriptor_rejected`) before it loads the provider, so the socket is non-inheritable before any provider code runs and a child the provider `exec`s never holds the host connection; the worker's first frame still waits for the loaded provider, so a provider that cannot load never reaches the host. A child the provider forks without `exec` keeps the descriptor, which is the provider's own trust domain; the host treats the worker's process exit, not the end of the connection, as its death. The transport, like the framing, the handshake method and the offered `protocol_versions`, is fixed across protocol versions, because version negotiation runs over the connection and cannot pick the channel it runs on. Changing it therefore moves the host's minimum SDK version, not `PROTOCOL_VERSION`.

## Methods

The host and the worker exchange JSON-RPC 2.0 messages, each frame a 4-byte big-endian length prefix and one UTF-8 JSON body of at most `MAXIMUM_FRAME_BYTES`.

| Method | Sent by | Kind | Purpose |
|---|---|---|---|
| `handshake` | worker | request | Offers `protocol_versions`; the result binds catalog names and the heartbeat interval |
| `dispatch` | host | request | Runs a lifecycle handler (`on_create`, `on_update`, `on_delete`) |
| `observe` | host | request | Runs `on_observe` |
| `migrate` | host | request | Runs a resource class's `upgrade` or `downgrade` on one resource's state |
| `resource_logs`, `resource_vitals` | host | request | Query one resource's logs or vitals |
| `resource_logs_follow` | host | request | Streams one resource's logs until the provider's stream ends or the host cancels |
| `shutdown` | host | request | Ends the session |
| `cancel` | host | notification | Cancels one request |
| `heartbeat` | worker | notification | Shows the worker is alive |
| `resource_logs_follow_event` | worker | notification | Carries one event of a `resource_logs_follow` stream |
| `apply_resource`, `read_resource` | worker | callback request | Apply or read a child the dispatched resource owns |

Protocol 10 is the only version the worker offers; a handshake offering no version the host supports fails with `UNSUPPORTED_PROTOCOL_CODE`. A request for a method the worker does not serve is answered with `METHOD_NOT_FOUND_CODE` (logged `request_method_unknown`).

## Handshake and provider identity

`discover_resources` refuses two distinct classes of one package registering the same resource name (`DuplicateResourceTypeError`, reported by introspection as `import_failed`). The handshake result carries `distribution_catalog_names`, the catalog name (`prefix/name`, validated) of the served distribution and of every provider distribution it embeds, keyed by distribution name. Within the handshake the worker normalizes the keys, refuses a result without the served distribution (`distribution_not_cataloged`), maps each distribution's `pragma.provider` package to its catalog name (`declaration.provider.load_package_catalog_names`) and refuses a distribution it cannot load (`distribution_not_loadable`), all before it serves; the session then binds the map (`context.package_catalog_names_scope`). `Resource.provider` is the catalog name of the longest bound package holding the class's module (`context.compute_defining_provider`), so identities and applied children name the provider defining the type. `DispatchParams`, `ObserveParams` and `ResourceQueryParams` carry no `provider`; request log fields name the defining provider of the target's class. `ProviderHarness` binds the tested class's top-level package to its `provider_name`, merged over its `package_catalog_names` argument.

## Observe

The host sends `observe` (`OBSERVE_METHOD`, `ObserveParams`, `ObserveResult`) before it acts on a non-computed type, so `Provider.resource` refuses a non-computed type without `on_observe` (`MissingObserveError`) or without an `Outputs` class (`MissingOutputsError`, since only outputs report the object present). The introspection report gives them the `missing_observe` and `missing_outputs` categories, each naming the `resource`, and `AdmissionFailureCategory` carries both; one in a module the declared package imports from outside itself is a `ProviderDeclarationError` naming the module and the missing part, reported as `import_failed`.

For an `observe` the worker runs `on_observe` with no runtime context bound, so platform callbacks raise, and answers `outputs` or `None` when the object is absent. `on_observe` makes no write and reports only an object this resource created: it raises `AlreadyExistsError` for an object at the resource's identity that its create's conflict path would refuse, such as one without this resource's marker; the worker answers that observe with `ALREADY_EXISTS_CODE`, and the host never writes or deletes such an object.

The outputs of `observe`, and of a create or update `dispatch`, are normalized the same way (`provider.outputs.normalize_outputs`: read as plain data, each property by name or alias, so an Outputs subclass instance and a dict give the same result, validated through the type's `Outputs` class, undeclared top-level keys dropped and logged as `outputs_keys_ignored`; a class declaring no `Outputs` class that returns outputs raises `ValueError`). The codec then encodes them with secrets revealed, so unencodable outputs still answer `RESULT_NOT_SERIALIZABLE_MESSAGE`, and the host compares the encoded dicts. `ProviderHarness` results carry the same encoded form as `normalized_outputs`.

An `AlreadyExistsError` raised from `on_create` in a create `dispatch`, or from `on_observe` in an `observe`, alone or inside an exception group, is answered with `ALREADY_EXISTS_CODE`; raised anywhere else it is a plain `INTERNAL_ERROR_CODE` failure. An error code is no frame shape or method, so it is not versioned by `PROTOCOL_VERSION`.

## Provider response

`ProviderResponse.failure_code` (`ProviderFailureCode`: `already_exists`, `conformance`) marks a failure whose reason the platform words itself. `ProviderResponse.provider_version` is the version of the provider that ran the operation; the Pragmatiks host fills it, and no frame carries it. `ProviderResponse.children_pending` is valid only on a `SUCCESS` with no outputs (Owned children).

## Deadlines, heartbeat and cancel

`ResourceTypeCapabilities.operation_deadline_seconds` carries the class's `Resource.operation_deadline_seconds` (`None`, or 1 to `MAXIMUM_OPERATION_DEADLINE_SECONDS` = 3600) to the handshake and the introspection report; `Provider.resource` refuses any other value, and an annotated field of that name, with `OperationDeadlineOutOfRangeError`, which introspection reports as `operation_deadline_out_of_range` naming the `resource` (`AdmissionFailureCategory` carries it).

`HandshakeResult.heartbeat_interval_seconds` sets how often the worker sends the bare `heartbeat` notification (`HEARTBEAT_METHOD`), from the accepted handshake until the session ends; a heartbeat that cannot be written ends the session.

The host's `cancel` notification (`CANCEL_METHOD`, `CancelParams(correlation_id)`) cancels that request's task, logged `request_cancel_received`; a task that has not started skips its handler. Wherever the cancel lands, and however the handler ends (`CancelledError`, a return or another error, the latter two logged `request_cancel_swallowed` and their response dropped), the worker consumes the cancellation, waits once for the work the request had recorded in the loop's default executor when its handler ended (work submitted later is not waited for), then answers `CANCELLED_CODE` (-32003, `The request was cancelled at its deadline.`, logged `request_cancelled` with `executor_work_items`, work cancelled before it ran not counted). A cancel for a request with no task, a done task or one already cancelled is ignored (`cancel_ignored` with its `reason`); a cancel at session end still writes no response.

`serve_host` installs `worker.executor.RequestScopedExecutor` as the default executor. It runs each piece of work in a copy of the submitting context, as `asyncio.to_thread` does, so `run_in_executor(None, ...)` work sees the request's context variables, and records the work each request submits under `request_scope(correlation_id)`, which `run_request` enters. So `asyncio.to_thread`, `run_in_executor(None, ...)` and the loop's `getaddrinfo` are waited for; threads, processes and executors a handler starts itself, and asyncio tasks it spawns or shields, are not.

`NotificationFrame` carries `params` and `JsonRpcCodec.build_notification` takes them; a bare notification is written without a `params` member.

## Owned children

Resources compose through owned children. `ChildResourcePayload` names no project and no owner, and `DispatchParams` no owner: the host applies every child (`APPLY_RESOURCE_METHOD`) in the dispatched resource's project and names the dispatched resource as its owner, fenced on the dispatched resource's running operation. `Resource.apply()` returns `None` and never waits.

The `read_resource` callback (`READ_RESOURCE_METHOD`, `ReadResourceParams(dispatch_id, provider, resource, name)`, `ReadResourceResult(resource: ChildResourceStatePayload | None)`, `ChildResourceStatePayload(lifecycle_state, config, outputs)`, `lifecycle_state` any `LifecycleState`, `deactivating` included, the config as stored, references unresolved) serves only resources the dispatched resource owns, under the same fence, and `None` when no live resource has that identity. `Resource.read()` calls it through `context.read_resource` and builds a new instance of its own class from the stored config, unflattened since it holds no resolved values.

`on_create` and `on_update` may return `ChildrenPending` (`models.children`, a frozen model with no fields) instead of outputs: `RequestHandlers.run_dispatch` answers every dispatch with `DispatchResult(outputs, children_pending)`, whose validator refuses both together, and logs `dispatch_children_pending` for it; `on_observe` and `on_delete` returning it fail with `ValueError` (`provider.outputs.refuse_children_pending`, an `INTERNAL_ERROR_CODE` failure naming the method). `ProviderResponse.children_pending` carries it to the platform; the host skips the observe comparison for it.

A callback the platform refused is answered with `CALLBACK_REFUSED_CODE` (-32004), which `RuntimeContextProxy.call` logs as `callback_refused` and raises as `CallbackRefusedError` (a `RuntimeError`) whose text is the host's message alone, so the operation's failure reason is the platform's text; every other failure code stays a plain `RuntimeError`.

The protocol carries no callback that waits for a resource's state and no owner surface: a handler never waits for a child in process, and never names an owner.

`ProviderHarness` records `LifecycleResult.children_pending` and brokers `apply()` and `read()` in create, update and delete (`provider.harness_children`): a new or changed child starts PENDING, an unchanged one keeps its state, a foreign or unowned live resource is refused with the hub's reason, and a `ChildrenPending` pass that applied no child that was not READY and unchanged fails with the hub's nothing-pending reason; the test settles a child with `set_child_state` and runs the owner's next pass with `resume()` (`on_update` with the last config that returned outputs; a non-computed type is observed first and gets `on_create` when absent). The refusal texts are copied from the hub's `models/resource_failure.py`.

## Queries: logs and vitals

`resource_logs` (`RESOURCE_LOGS_METHOD`) and `resource_vitals` (`RESOURCE_VITALS_METHOD`) take `ResourceQueryParams`: a `target` whose config is the resolved config the host last applied, and `resolved_files` (`ResourceQueryParams.resolved_files`: signed download URLs keyed by Pragmatiks file reference, the shape `ObserveParams` carries). `RequestHandlers.build_queried_resource` builds the resource from both, so a config holding a required field or a `pragma://files` reference builds as it does for a dispatch.

Vitals are the provider-reported live check: `resource_vitals` runs `Resource.vitals()`, which answers `Vitals(status: VitalsStatus, message, details)` with `VitalsStatus` one of `nominal`, `impaired`, `down` (default `nominal`), and `ResourceTypeCapabilities.vitals` reports whether a type overrides it.

Following logs is a stream: `resource_logs_follow` (`RESOURCE_LOGS_FOLLOW_METHOD`) takes `ResourceLogsFollowParams`, which is `ResourceQueryParams` (`target`, `resolved_files`, `since`, `tail`), and runs `Resource.follow_logs(since, tail)`, which yields the `tail` most recent entries at or after `since`, then each new entry. `ResourceTypeCapabilities.follow_logs` reports whether a type overrides it; a request for a type that does not is answered with `FOLLOW_LOGS_UNSUPPORTED_CODE` (-32005) before anything is sent. Otherwise the worker sends `resource_logs_follow_event` notifications (`RESOURCE_LOGS_FOLLOW_EVENT_METHOD`, `ResourceLogsFollowEventParams(correlation_id, event)`, the request's correlation id in the params since a notification has no `id`): first `ResourceLogsFollowStarted` (`kind: started`), before the provider's generator is iterated, then one `ResourceLogsFollowEntry` (`kind: entry`, `entry: LogEntry`) per entry; each write waits for the connection to take the frame, so a slow host slows the generator. An entry too large for one frame, or one that cannot be encoded, is dropped with a `resource_logs_follow_entry_dropped` warning and the stream goes on. The request answers `{}` when the generator ends, `CANCELLED_CODE` when the host cancels it (the generator is closed), and `INTERNAL_ERROR_CODE` when the provider raises. Delivery is at most once, with no replay, in order per source. A cancel ends the generator only when it waits on cancellation-safe async sources: blocking work in an executor is waited for before the cancel is answered, so `follow_logs` must not follow from one.

## Config and outputs

`Outputs` ignore undeclared properties; `Config` forbids them. A dependency reference or a child resource read whose Config forbids extras is read with the top-level config keys it does not declare dropped (`models.base.drop_undeclared_config_keys`, pure) and logged by each caller (`dependency_config_keys_ignored`, `child_config_keys_ignored`). `Resource.resolved(value)` (`models.references.require_resolved`) types a `Field[T]` config value as `T`, raising `UnresolvedReferenceError` for a top-level `FieldReference`.

`config`, `previous_config` and child configs carry two reference marker shapes, owned by `protocol/references.py`: a dependency marker (`__dependency__: true` with `project_id`, `provider`, `resource` and `name`, plus `ref` once resolved) and a field-reference marker (`__field_ref__: true` with `ref` and `resolved_value`). Changing either is a new `PROTOCOL_VERSION`.

The config and outputs schemas the introspection report carries are Pydantic's serialization-mode schemas (`provider.loading.build_resource_type_schema`), the one schema the hub validates config against and masks by. A secret is declared as `SecretStr` or `SecretBytes` (`Field[SecretStr]` in config), and its schema node carries `"format": "password"`; the schemas carry no `sensitive` key. Config also adds `"immutable": true` to an immutable field. Every frame the worker writes carries secrets revealed (`protocol.codec.build_json_payload`), since the host is the trusted side and masks at read.
