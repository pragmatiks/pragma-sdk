"""The worker that runs a provider's lifecycle methods for the Pragmatiks host.

The host starts it as ``python -m pragma_sdk.worker --socket-descriptor
<number>`` inside the provider's environment, with
``PRAGMA_PROVIDER_DISTRIBUTION`` naming the provider distribution. The
descriptor holds one end of a connected Unix socket pair: the worker creates
no socket file and listens for nothing.
"""
