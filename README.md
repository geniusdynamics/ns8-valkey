# ns8-valkey

[Valkey](https://valkey.io/) key-value store for other NethServer 8
modules, as a standalone server or as a highly available
[Valkey Cluster](https://valkey.io/topics/cluster-tutorial/) spread over the
nodes of the NethServer cluster.

- **Standalone**: one server, nothing to know about clustering.
- **Cluster**: install one instance per node; the first one is the
  *initial node*, the others *join* it. With 3 or more members the store
  survives the loss of any one node.
- Consumer modules get their own Valkey user from a guarded action;
  passwords never go through the cluster Redis keys.

Documentation:

- [Consumer integration](docs/integration.md): the contract for modules
  that use Valkey (discovery, `grant-client`, events, HA modes, KumoMTA)
- [High availability](docs/high-availability.md): design, failure
  behaviour, membership, upgrades
- [Operations](docs/operations.md): backup and restore, updates,
  troubleshooting

## Install

    add-module ghcr.io/geniusdynamics/valkey:latest 1

Output example:

    {"module_id": "valkey1", "image_name": "valkey", "image_url": "ghcr.io/geniusdynamics/valkey:latest"}

## Configure

The UI *Settings* page has a role picker. From the command line:

```bash
# Standalone server
api-cli run configure-module --agent module/valkey1 --data '{"role": "standalone"}'

# New cluster on node 1, then members on nodes 2 and 3
api-cli run configure-module --agent module/valkey1 --data '{"role": "initial"}'
api-cli run configure-module --agent module/valkey2 --data '{"role": "join", "cluster_id": "valkey1"}'
api-cli run configure-module --agent module/valkey3 --data '{"role": "join", "cluster_id": "valkey1"}'
```

Input fields:

| Field | Description |
|---|---|
| `role` | `standalone`, `initial` (first node of a new cluster) or `join` |
| `cluster_id` | with `join`: the cluster to join, from `get-configuration` `cluster_candidates` |
| `consistency` | cluster-wide: `availability` (default) or `consistency`, see [high availability](docs/high-availability.md#consistency-mode) |
| `maxmemory_mb` | memory limit of each Valkey process, 0 (default) for none; writes fail when reached (`noeviction`) |
| `allow_same_node` | with `initial`: allow several members on one node. **Tests only**: such a cluster does not survive a node failure |

Read the settings with `get-configuration`, the health with
`get-cluster-status`, the client users with `list-clients`.

## Actions

| Action | Who | Purpose |
|---|---|---|
| `configure-module` | administrators | role and settings |
| `get-configuration`, `get-cluster-status`, `list-clients` | readers | settings, health, clients (no secrets) |
| `grant-client`, `revoke-client` | role `valkeyclient` | client credentials, see [integration](docs/integration.md) |
| `admit-member`, `reconcile-cluster` | role `valkeypeer` | used between ns8-valkey instances to form and repair a cluster |

## Uninstall

    remove-module --no-preserve valkey1

A cluster member hands its slots over to the other members before it is
removed.

## Security

- Valkey listens on the node cluster VPN address and on loopback only. The
  VPN is WireGuard and is in the `trusted` firewall zone: no public port.
- The `default` Valkey user is disabled. Each consumer has its own ACL
  user, stored as a SHA-256 hash.
- The cluster bus between members uses mutual TLS with a CA private to the
  cluster.
- Secrets (admin and replication passwords, CA key) are in `0600` files of
  the module state, never in the environment, argv, logs or Redis keys.

## Debug

```bash
runagent -m valkey1 systemctl --user status valkey valkey-replica
runagent -m valkey1 journalctl --user -u valkey -n 50
api-cli run module/valkey1/get-cluster-status | jq
```

`valkey-cli` inside the container, as the module administrator (the
password is read from the state file, not typed on the command line):

```bash
runagent -m valkey1 sh -c '. ./secrets.env; VALKEYCLI_AUTH=$VALKEY_ADMIN_PASSWORD podman exec -i -e VALKEYCLI_AUTH valkey valkey-cli --user ns8admin -p ${TCP_PORTS%%,*} cluster nodes'
```

## Testing

    ./test-module.sh <NODE_ADDR> ghcr.io/geniusdynamics/valkey:latest

The Robot Framework suites in `tests/` cover the standalone server and a
three-member cluster on one node (`allow_same_node`), including member
restart, rolling restart, member removal and client revocation.

## UI translation

Translated with [Weblate](https://hosted.weblate.org/projects/ns8/).
