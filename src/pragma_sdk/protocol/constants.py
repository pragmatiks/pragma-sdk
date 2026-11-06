"""Version, method names, error codes and framing limits of the worker protocol.

The host and the worker exchange JSON-RPC 2.0 messages over a Unix socket.
Each frame is a 4-byte big-endian length prefix followed by one UTF-8 JSON
body of at most :data:`MAXIMUM_FRAME_BYTES`.

The host sends the requests (``handshake``, ``dispatch``, ``observe``,
``migrate``, the resource queries and ``shutdown``) and the ``cancel``
notification; the worker sends the ``heartbeat`` notification and, from a
dispatch, the :data:`APPLY_RESOURCE_METHOD` and :data:`READ_RESOURCE_METHOD`
callbacks. Beside the JSON-RPC codes, a handshake offering no supported
protocol version fails with :data:`UNSUPPORTED_PROTOCOL_CODE`, a create
dispatch refused because its external object already exists, and an
observe that finds an object the resource did not create, with
:data:`ALREADY_EXISTS_CODE`, a request the host cancelled at its deadline with :data:`CANCELLED_CODE`, and
the host answers a callback the platform refused with
:data:`CALLBACK_REFUSED_CODE`, its message the platform's reason.
"""

from __future__ import annotations


PROTOCOL_VERSION = 5
OFFERED_PROTOCOL_VERSIONS = [PROTOCOL_VERSION]

JSONRPC_VERSION = "2.0"

HANDSHAKE_METHOD = "handshake"
DISPATCH_METHOD = "dispatch"
OBSERVE_METHOD = "observe"
SHUTDOWN_METHOD = "shutdown"
APPLY_RESOURCE_METHOD = "apply_resource"
READ_RESOURCE_METHOD = "read_resource"
MIGRATE_METHOD = "migrate"
RESOURCE_LOGS_METHOD = "resource_logs"
RESOURCE_HEALTH_METHOD = "resource_health"
CANCEL_METHOD = "cancel"
HEARTBEAT_METHOD = "heartbeat"

METHOD_NOT_FOUND_CODE = -32601
INVALID_PARAMS_CODE = -32602
INTERNAL_ERROR_CODE = -32603
UNSUPPORTED_PROTOCOL_CODE = -32001
ALREADY_EXISTS_CODE = -32002
CANCELLED_CODE = -32003
CALLBACK_REFUSED_CODE = -32004

LENGTH_PREFIX_FORMAT = ">I"
LENGTH_PREFIX_BYTES = 4
MAXIMUM_FRAME_BYTES = 64 * 1024 * 1024

MAXIMUM_OPERATION_DEADLINE_SECONDS = 3600
"""Longest ``operation_deadline_seconds``, in seconds, a resource type may declare.

A provider version declaring a longer one, or one under 1, is refused at
admission.
"""
