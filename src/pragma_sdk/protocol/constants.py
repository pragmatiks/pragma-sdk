"""Version, method names, error codes and framing limits of the worker protocol.

The host and the worker exchange JSON-RPC 2.0 messages over a Unix socket.
Each frame is a 4-byte big-endian length prefix followed by one UTF-8 JSON
body of at most :data:`MAXIMUM_FRAME_BYTES`.
"""

from __future__ import annotations


PROTOCOL_VERSION = 1
OFFERED_PROTOCOL_VERSIONS = [PROTOCOL_VERSION]

JSONRPC_VERSION = "2.0"

HANDSHAKE_METHOD = "handshake"
DISPATCH_METHOD = "dispatch"
SHUTDOWN_METHOD = "shutdown"
WAIT_FOR_STATE_METHOD = "wait_for_state"
APPLY_RESOURCE_METHOD = "apply_resource"
MIGRATE_METHOD = "migrate"
RESOURCE_LOGS_METHOD = "resource_logs"
RESOURCE_HEALTH_METHOD = "resource_health"

METHOD_NOT_FOUND_CODE = -32601
INVALID_PARAMS_CODE = -32602
INTERNAL_ERROR_CODE = -32603
UNSUPPORTED_PROTOCOL_CODE = -32001

LENGTH_PREFIX_FORMAT = ">I"
LENGTH_PREFIX_BYTES = 4
MAXIMUM_FRAME_BYTES = 64 * 1024 * 1024
