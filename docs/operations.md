# Operations

Part of the [ns8-valkey documentation](../README.md).

## Backup and restore

- Before each backup, `module-dump-state` runs `BGSAVE` on the main process
  and saves the ACL file. The backup contains `dump.rdb` and `users.acl` of
  the `valkey-data` volume, `state/secrets.env` and the cluster CA. The
  append-only file and `nodes.conf` are excluded
  (`etc/state-exclude.conf`).
- **Restore always gives a standalone server**, with the data and the
  client users of the backup. Consumers keep their credentials; they must
  point to the restored instance (a new module ID) by reconfiguring.
  Valkey starts from `dump.rdb` with the append-only file off, and the
  module turns it on again at once, which writes a new one.
- A cluster member's backup holds only the slots that member served. To
  replace a lost member, install a new instance and join it: the cluster
  copies the data to it. Restoring a member backup is useful only when the
  whole cluster is lost.
- **Clone** (and move) also gives a standalone server with a copy of the
  data.

## Migrate a standalone server into a cluster

1. Back up the source instance (any backup repository).
2. Restore that backup on the node that will hold the initial node of the
   cluster (*Backup*, *Restore*). The restored instance is a standalone
   server with the data and the client users of the source.
3. In its *Settings*, choose **Initial node of a new cluster**: it restarts
   in cluster mode and keeps its data.
4. Install ns8-valkey on the other nodes and join the cluster. The data is
   spread over the members as they join.
5. Reconfigure the consumers to use the new instance. Their credentials
   still work: the client users came with the backup.

A standalone server that already runs where the cluster should start can
skip steps 1 and 2.

## Updates

- An update is refused if the new image runs an older Valkey than the
  installed one (`update-module/04check_downgrade`, error
  `valkey_downgrade_refused`): Valkey cannot read data files written by a
  newer release. `force` does not bypass it.
- In a cluster, members restart one at a time (a lock key in Valkey). Before
  restarting, a member hands the slots of its primaries to their replicas
  on other members with a graceful `CLUSTER FAILOVER`, then waits until its
  own replicas are in sync again. The coordinator moves the primaries back.
  Updating all the instances at once is therefore safe; with 3 or more
  members the store stays available.
- A standalone server restarts: clients reconnect after a short outage.
- The periodic topology check skips its runs while a member holds the
  rolling restart lock, so it does not move slots during an update.

## Health

`get-cluster-status` (also on the *Status* page):

| Field | Meaning |
|---|---|
| `members_count`, `shards` | members of the cluster, slot-owning primaries |
| `cluster_state` | `ok`, `fail` (some slots not served) or `standalone` |
| `quorum_ok` | 3 or more members and state `ok`: automatic failover is possible |
| `failure_tolerance` | 1 when every primary has a replica on another member |
| `processes[]` | per process: member, node, endpoint, `primary`/`replica`, slots, its primary, `link` (`up`/`down`), `lag_bytes`, `lag_seconds`, `reachable` |
| `warnings` | `below_quorum`, `members_down`, `cluster_down`, `unreachable`, `no_redundancy`, `not_running` |

## Troubleshooting

- **Topology not repaired**: run `reconcile-cluster` on any member; the
  output lists the changes.

  ```bash
  api-cli run module/valkey1/reconcile-cluster --data '{}'
  ```

- **A node is gone for good**: remove its ns8-valkey instance
  (`remove-module --no-preserve valkeyN`, or remove the node). The members
  forget it, promote its replicas and rebalance.
- **Two members, one failed**: no automatic failover (no majority). If the
  failed node will not return, remove its instance: its replica on the
  surviving member is promoted.
- **Logs**: `runagent -m valkey1 journalctl --user -u valkey -u valkey-replica`.
