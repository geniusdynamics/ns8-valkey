# High availability

Part of the [ns8-valkey documentation](../README.md).

## Choice: Valkey Cluster

Two high availability designs exist for Valkey:

| | Valkey Cluster | Primary/replica with Sentinel |
|---|---|---|
| Failover decided by | a majority of the slot-owning primaries | a majority of the Sentinel processes |
| Minimum for automatic failover | 3 primaries (with replicas) | 3 Sentinels on independent machines |
| Client | cluster-aware: seed list, `MOVED`/`ASK` redirections | Sentinel-aware: asks the Sentinels where the primary is |
| Data | sharded over the primaries | one primary holds everything |
| KumoMTA support | yes (`cluster = true`, `redis::cluster::ClusterClient`) | **no** |

KumoMTA, the first consumer, decides it: its Redis client
(`crates/mod-redis/src/lib.rs` in the KumoMTA source,
`docs/reference/redis/open.md`) connects to one node or to a Redis/Valkey
Cluster, and has no Sentinel support. Its throttle scripts use one key each,
so they work unchanged on a sharded cluster. The module therefore offers
Valkey Cluster for high availability, and a plain server for standalone.

Sentinel would have been the closer call for clients that only speak the
single-node protocol, but it needs the same number of nodes, an extra
process per node, and a Sentinel-aware client anyway.

Sources: [Valkey Cluster tutorial](https://valkey.io/topics/cluster-tutorial/),
[Valkey Sentinel](https://valkey.io/topics/sentinel/), Valkey 9.1.3 source
(`src/commands.def`: `CLUSTER MIGRATESLOTS` and `CLUSTER REPLICATE NO ONE`
since 9.0).

## Topology

Each instance (one per NS8 node) runs two Valkey processes in cluster mode:

- `valkey.service`, the *main* process (TCP ports 1 and 2: client, bus)
- `valkey-replica.service`, the *replica* process (TCP ports 3 and 4)

The module keeps this layout, recomputed after every membership change:

| Members | Layout | One member fails |
|---|---|---|
| 1 | one shard: the main process owns all 16384 slots, the replica process replicates it | store down until it returns |
| 2 | one shard; the processes of the second member replicate it | store down until it returns, or until an administrator removes the failed member (its replica is then promoted with `CLUSTER FAILOVER TAKEOVER`) |
| 3 or more | one shard per member, slots spread evenly; the replica process of member *i* replicates the primary of member *i-1* (a ring) | its replica on another member is promoted after the node timeout (10 s) plus the election: about 10 to 15 seconds |

Why 3: Valkey Cluster promotes a replica only with the votes of a majority
of the primaries that own slots. With 2 members, the survivor has 1 vote of
2: no majority, by design (otherwise both halves of a network partition
would accept writes: split brain). So 2 members give a copy of the data but
no automatic failover; the UI and `get-cluster-status` warn
(`below_quorum`).

Split brain: a primary cut off from the majority stops accepting writes
after the node timeout; the majority side promotes its replica. Writes
accepted by the old primary in that window are lost when it rejoins as a
replica. Replication is asynchronous, so a primary failure can lose about
the last second of acknowledged writes. For throttle and traffic shaping
counters this is harmless.

Settings: `cluster-node-timeout 10000`, `cluster-replica-validity-factor 0`
(a replica always tries to take over, availability first),
`cluster-require-full-coverage yes`, `appendonly yes` with
`appendfsync everysec`, `maxmemory-policy noeviction`.

## Membership: events and actions, no polling

- **Initial node** (`role: initial`): creates the cluster CA and starts a
  one-node cluster (`cluster_id` = its module ID).
- **Join** (`role: join`, `cluster_id`): the new instance runs
  `admit-member` on a member (role `valkeypeer`, authorization
  `valkey@any:valkeypeer`). The member checks that the caller is the module
  it claims to be (`AGENT_TASK_USER`) and that its node has no member yet,
  and returns the admin and replication passwords and the CA of the
  cluster bus. They go to `0600` files. The new instance starts, publishes
  its `valkey-cluster` record, and runs `reconcile-cluster` on the
  coordinator: the action returns when the cluster includes it.
- **Coordinator**: the member with the lowest module sequence number whose
  main process answers. It runs the reconciliation; a file lock serializes
  repairs on it.
- **Reconciliation** (`valkey_ns8.Reconciler`): `CLUSTER MEET` unknown
  member processes; `CLUSTER FORGET` processes of removed members (a failed
  primary still owning slots is first replaced by its replica with
  `CLUSTER FAILOVER TAKEOVER`); assign all slots on a new cluster; fail
  back (a member without a primary takes it back from a member with two,
  with a graceful `CLUSTER FAILOVER`); turn a replica into an empty
  primary (`CLUSTER REPLICATE NO ONE`); move slots with the atomic
  `CLUSTER MIGRATESLOTS` of Valkey 9; place replicas
  (`CLUSTER REPLICATE`). While any process is down it only meets and
  forgets: Valkey fails over by itself, and moving slots would be undone
  when the member returns.
- **Events**: every process start publishes `valkey-cluster-changed`
  (reason `main-started`, ...); the coordinator reconciles. A member that
  restarted after a failure therefore gets its primary back. Repairs
  publish the event with reason `reconciled`, which only refreshes the seed
  lists of the service records.
- **Member removed** (`remove-module`): `destroy-module` hands its slots
  over (graceful failover to its replicas on other members, or slot
  migration), stops, and runs `reconcile-cluster` on the coordinator. The
  `module-removed` event repeats the repair on every member, also when the
  node died and `destroy-module` could not run.
- **Initial node removed**: nothing special. The cluster keeps its ID; the
  next member by sequence number becomes the coordinator, and every member
  can admit new ones.
- **Leaving** (`role: standalone` on a `join` member): slots handed over,
  then the instance restarts empty as a standalone server.
- **Rejoin**: a member that was down rejoins with its `nodes.conf` and
  catches up from its primary; ACL users are copied from a peer at start.

## Discovery

```
module/<id>/srv/tcp/valkey           consumers: see docs/integration.md
module/<id>/srv/tcp/valkey-cluster   members: host (VPN address), port,
                                     bus_port, replica_port,
                                     replica_bus_port, node, cluster_id,
                                     role, allow_same_node
```

No secret is published. `get-configuration` lists the clusters to join as
`cluster_candidates`.

## Networking

- Host network, every port bound to the node VPN address (`10.5.4.x`) and
  `127.0.0.1`. Valkey Cluster announces the VPN address
  (`cluster-announce-ip`).
- The NS8 cluster VPN network is a source of the `trusted` firewall zone
  (`create-cluster`): members reach each other with no firewall rule, and
  nothing is opened on public interfaces. The module needs no `fwadm`.
- The cluster bus uses mutual TLS (`tls-cluster yes`,
  `tls-auth-clients yes`) with certificates signed by the cluster CA: a
  process without a cluster certificate cannot join the bus, which Valkey
  otherwise trusts blindly. Client and replication traffic is plain TCP
  inside the WireGuard VPN, authenticated with ACL users.

## Credentials

| User | Rules | Used by |
|---|---|---|
| `default` | off | nobody |
| `ns8admin` | all | the module actions (topology, ACL) |
| `ns8repl` | all keys and commands | replication (`masteruser`) and slot migration, whose target replays the data as this user |
| `ns8c-<client>` | `+@all -@admin -@dangerous`, keys and channels of `key_prefix` | consumers, see [integration](integration.md) |

All are `#<sha256>` rules in `users.acl` of each process. The clear admin
and replication passwords are in `state/secrets.env` (`0600`); the
replication password is also in the rendered `valkey.conf` (`0600`,
regenerated at every start, not backed up). Client passwords are not kept
in clear anywhere.

## Limits

- One member per node (refused by `admit-member`). `allow_same_node` exists
  for tests only.
- Data size per shard is limited by the memory of the node; `maxmemory_mb`
  applies per process, and a node also holds a replica of another shard.
- Client TLS is not offered: KumoMTA's client verifies certificates
  against the system trust store, which does not know the cluster CA. The
  VPN encrypts the traffic.
- A cluster member's backup holds its own share of the data: restoring it
  gives a standalone server (see [operations](operations.md)).
