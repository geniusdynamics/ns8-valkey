# Consumer integration

This page is the contract between ns8-valkey and the modules that store
data in it (the first one is ns8-kumomta, for shared throttles and
traffic shaping state). Everything a consumer needs is here: the
discovery records, the guarded actions that hand out credentials, the
events, and the high availability modes a client must support.

The same client code path also works with a Redis or Valkey deployment
outside NethServer: see [External deployments](#external-deployments).

## Summary

| What | Value |
|---|---|
| Discovery record | `module/<valkey_id>/srv/tcp/valkey` (no secrets) |
| Authorization label of the consumer | `valkey@any:valkeyclient` |
| Get credentials | action `grant-client` on `module/<valkey_id>` |
| Drop credentials | action `revoke-client`, or automatic on `module-removed` |
| HA modes | `standalone` and `cluster` (Valkey Cluster protocol) |
| Transport | plain TCP on the cluster VPN (WireGuard) or loopback, ACL user and password |
| Events | `valkey-changed`, `valkey-cluster-changed` |

## Authorization

Declare the role in the consumer `build-images.sh`:

```bash
--label="org.nethserver.authorizations=valkey@any:valkeyclient"
```

The `valkeyclient` role of every ns8-valkey instance contains only
`grant-client` and `revoke-client`. Their names do not start with `get-`,
`list-`, `show-` or `read-`, so the builtin `reader` role cannot run them.

## Discovery

Every configured instance publishes a service provider record. It holds no
secret.

```
module/<valkey_id>/srv/tcp/valkey
  host        VPN address of the node, e.g. 10.5.4.1
  port        client port, e.g. 20001
  node        NS8 node ID
  ha_mode     "standalone" or "cluster"
  cluster     "true" when ha_mode is "cluster", else "false"
  cluster_id  ID of the Valkey cluster ("" when standalone)
  nodes       comma separated host:port seed list of all the cluster
              members, this one included ("host:port" alone when standalone)
  tls         "false" (see Security)
  version     Valkey server version, e.g. 9.1.3
```

```python
import agent
rdb = agent.redis_connect(use_replica=True)
for rec in agent.list_service_providers(rdb, "valkey", "tcp"):
    print(rec["module_id"], rec["ha_mode"], rec["cluster_id"], rec["host"], rec["port"])
```

The members of one cluster share `cluster_id`. A consumer normally stores
the module ID it was configured with. If that member is removed, any other
record with the same `cluster_id` serves the same data with the same
credentials: see [Events](#events).

Cluster members also publish `module/<valkey_id>/srv/tcp/valkey-cluster`,
used by ns8-valkey itself to form the cluster. Consumers should ignore it.

## Actions

### grant-client

Creates or updates the Valkey ACL user of the caller and returns its
credentials and endpoints. In a cluster the user is created on every
member, so the credentials work on any endpoint.

Run it as a task on the chosen instance:

```python
import agent, agent.tasks
ret = agent.tasks.run(agent_id="module/valkey1", action="grant-client", data={},
                      extra={"isNotificationHidden": True})
if ret["exit_code"] != 0:
    raise RuntimeError("grant-client failed")
creds = ret["output"]   # never log it
```

Input (all optional):

```json
{
  "key_prefix": "kumo:",
  "client_id": "kumomta1"
}
```

- `key_prefix`: restrict the user to keys and Pub/Sub channels starting
  with this prefix. Empty (default): all keys. Allowed characters:
  letters, digits and `:_.-{}`.
- `client_id`: only for administrators. When the caller is a module
  (`AGENT_TASK_USER` is `module/<id>`) the client ID is always the
  caller's module ID and a different `client_id` is refused, so a
  module cannot obtain or revoke the credentials of another module.

Output:

```json
{
  "client_id": "kumomta1",
  "username": "ns8c-kumomta1",
  "password": "<64 hexadecimal characters>",
  "ha_mode": "cluster",
  "cluster": true,
  "cluster_id": "valkey1",
  "nodes": [
    {"host": "10.5.4.1", "port": 20001, "module_id": "valkey1", "node": "1"},
    {"host": "10.5.4.2", "port": 20001, "module_id": "valkey2", "node": "2"},
    {"host": "10.5.4.3", "port": 20001, "module_id": "valkey3", "node": "3"}
  ],
  "urls": ["redis://10.5.4.1:20001", "redis://10.5.4.2:20001", "redis://10.5.4.3:20001"],
  "tls": false,
  "key_prefix": ""
}
```

Rules:

- **Every call rotates the password.** The previous password stops working
  at once on every reachable member. Store the new one before using it.
  Calling it again is the way to recover lost or rejected credentials.
- The password has 64 hexadecimal characters: it can be put in a URL
  without escaping.
- Store the password in a `0600` file of the consumer state, never with
  `agent.set_env()`, in argv, in logs or in the consumer's own action
  output. Its `get-configuration` should return a `*_set` boolean only.
- The task output is kept by NS8 for 8 hours under
  `task/module/<valkey_id>/...` in the cluster Redis. Only the ns8-valkey
  instance, the cluster administrators and the caller that ran the task
  can read it; other modules cannot.
- Validation errors (exit code 3, `validation-failed`): `client_id`
  `client_id_mismatch` (a module asked for another client ID),
  `client_id` `required` (an administrator call without it), `key_prefix`
  `invalid`, and `""` `not_configured` (the instance has no role yet).
- A cluster member that is down when the user is created gets it when it
  starts again, from the other members.

### revoke-client

Deletes the ACL user on every reachable member. Input and caller rules as
for `grant-client` (`client_id` only for administrators). Output:

```json
{"client_id": "kumomta1", "revoked": true}
```

`revoked` is `false` when the user did not exist. A consumer should call it
from its `destroy-module` (best effort: ignore errors). If it does not, the
`module-removed` event revokes the user anyway.

### Read-only actions (any reader)

- `get-cluster-status`: health of the instance and of its cluster:
  quorum, the role of each member and process, replication lag. No secrets.
- `list-clients`: client IDs, user names and key prefixes. No secrets.

## Events

Published by ns8-valkey:

| Event | Payload | When |
|---|---|---|
| `valkey-changed` | `{"module_id", "module_uuid", "cluster_id", "ha_mode"}` | the endpoint, mode or seed list of an instance changed |
| `valkey-cluster-changed` | `{"cluster_id", "module_id", "reason"}` | a member joined, left, or the topology was repaired (internal, consumers may ignore it) |

Handled by ns8-valkey:

| Event | Effect |
|---|---|
| `module-removed` | the ACL user `ns8c-<removed module>` is deleted on every member; if the removed module was a member of the cluster, its slots and replicas are moved to the others |

A consumer should handle:

- `valkey-changed` with its configured `module_id`: re-read the record and
  reconfigure its client (the seed list or the mode may have changed). The
  credentials stay valid.
- `module-removed` with its configured `module_id`: switch to another
  record with the same `cluster_id`, if any. The credentials stay valid
  there; otherwise call `grant-client` on the new instance.

## High availability modes

A consumer must support exactly two client modes. Use `ha_mode` (or the
boolean `cluster`) to pick one.

### `standalone`

One Valkey server, no failover: when its node is down, the data store is
down. Connect to the single `nodes[0]` endpoint with a normal client.

This is also the mode of an external deployment that hides failover behind
one address (a virtual IP, a proxy, a cloud "primary endpoint").

### `cluster`

The Valkey Cluster protocol. Use a cluster-aware client with all the
`nodes` as seeds; it discovers the topology with `CLUSTER SLOTS` or
`CLUSTER SHARDS` and follows `MOVED` and `ASK` redirections.

- The client must reach every VPN endpoint that the cluster announces,
  not only the seeds. All of them are in the cluster VPN.
- Keys used together by one command, transaction or script must hash to
  the same slot: use a hash tag, e.g. `{tenant1}:counter`. Single-key
  commands and single-key scripts (as KumoMTA throttles use) need nothing.
- Use database 0 only.
- A cluster with one or two members has a single shard and works the
  same way for the client (a single seed with `cluster = true`).

Failure behaviour of the server side:

| Members | Shards | Failure of one member |
|---|---|---|
| 1 | 1 | the store is down until it returns |
| 2 | 1, replicated to the second member | the store is down until it returns, or until an administrator removes the failed member (its replica is then promoted) |
| 3 or more | one per member, each replicated on another member | automatic failover after about 10 to 15 seconds; writes of the last second acknowledged by the failed primary can be lost (asynchronous replication) |

Two members cannot vote a failover: a majority of 2 is 2. This is the
same rule as for every quorum system, and the module shows a warning.

Recommended client settings: a connect timeout and a response timeout of a
few seconds, and retries, so that requests during a failover fail fast and
are retried after the new primary is elected.

### Sentinel

Not offered. KumoMTA, the first consumer, has no Sentinel client (see
below), and Valkey Cluster gives automatic failover with the same number of
nodes.

## KumoMTA

KumoMTA's Redis client (`redis.open`, `kumo.configure_redis_throttles`)
supports one node or a Redis/Valkey Cluster, not Sentinel. Source:
`crates/mod-redis/src/lib.rs` (`NodeSpec::Single` / `NodeSpec::Cluster`,
`ClusterClient`) and `docs/reference/redis/open.md`.

```lua
-- ha_mode "standalone"
kumo.configure_redis_throttles {
  node = 'redis://ns8c-kumomta1:<password>@10.5.4.1:20001',
  connect_timeout = '5 seconds',
  response_timeout = '5 seconds',
}

-- ha_mode "cluster": every url of the grant-client output
kumo.configure_redis_throttles {
  node = { 'redis://10.5.4.1:20001', 'redis://10.5.4.2:20001', 'redis://10.5.4.3:20001' },
  cluster = true,
  username = 'ns8c-kumomta1',
  password = '<password>',
  connect_timeout = '5 seconds',
  response_timeout = '5 seconds',
}
```

- For one node, put the user and the password in the URL: the KumoMTA
  documentation says the `username` and `password` fields are used only
  for a cluster (recent source code also applies them to a single node).
  The password is hexadecimal, so it needs no escaping.
- Set `cluster = true` whenever `ha_mode` is `cluster`, also with a single
  seed.
- The default user permissions (`+@all -@admin -@dangerous`) cover what
  KumoMTA uses: `COMMAND INFO` (redis-cell detection), `EVALSHA`/`EVAL`/
  `SCRIPT LOAD`, `GET`/`SET`, the sorted set commands of connection
  limits, `TIME` inside scripts, and `CLUSTER SLOTS`/`SHARDS` for the
  cluster client. KumoMTA throttle keys have no configurable prefix: do
  not set `key_prefix` for it.

## External deployments

A consumer that also accepts a Redis or Valkey server outside NethServer
should ask for the same fields and run the same code path:

```json
{
  "ha_mode": "standalone | cluster",
  "nodes": ["redis://host:6379"],
  "username": "user",
  "password": "secret",
  "tls": false
}
```

- Redis Sentinel deployments must be reached through something that gives
  one stable address (`standalone`), because Sentinel needs a
  Sentinel-aware client.
- Managed services with a failover address (one DNS name for the primary)
  are `standalone`.
- Redis Cluster and Valkey Cluster are `cluster`.

## Security

- Passwords never go to the cluster Redis keys, the module environment,
  argv or logs. ns8-valkey stores the SHA-256 of client passwords only
  (Valkey ACL `#<hash>` rules), and does not keep the clear password.
- The `default` Valkey user is disabled. Each consumer has its own user:
  `+@all -@admin -@dangerous`, keys and channels limited to `key_prefix`.
- Client ports listen on the node VPN address and on loopback only, never
  on public addresses. The cluster VPN is WireGuard: traffic between nodes
  is encrypted, and NS8 accepts it in the `trusted` firewall zone, so no
  public port is opened.
- Client connections do not use TLS (`tls` is `false`): the VPN encrypts
  them. The cluster bus between members uses mutual TLS with a certificate
  authority private to the Valkey cluster.
