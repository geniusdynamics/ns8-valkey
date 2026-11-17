*** Settings ***
Documentation    Valkey Cluster of three members on the test node (allowed by
...              allow_same_node, for tests only): formation, client access,
...              member restart, member removal.
Library    SSHLibrary
Library    Collections
Resource    valkey.resource

*** Variables ***
${m1}    ${EMPTY}
${m2}    ${EMPTY}
${m3}    ${EMPTY}

*** Test Cases ***
Install three instances
    ${a} =    Install Valkey
    ${b} =    Install Valkey
    ${c} =    Install Valkey
    Set Suite Variable    ${m1}    ${a}
    Set Suite Variable    ${m2}    ${b}
    Set Suite Variable    ${m3}    ${c}
    Copy Probe

Create the cluster on the first instance
    Run Action    ${m1}    configure-module    {"role": "initial", "allow_same_node": true}
    ${status} =    Wait Until Keyword Succeeds    120s    5s    Cluster Should Be Healthy    ${m1}    1    0
    Should Contain    ${status}[warnings]    below_quorum
    ${config} =    Run Action    ${m2}    get-configuration
    ${ids} =    Evaluate    [c["cluster_id"] for c in $config["cluster_candidates"]]
    Should Contain    ${ids}    ${m1}

Join a second member
    Run Action    ${m2}    configure-module    {"role": "join", "cluster_id": "${m1}"}
    ${status} =    Wait Until Cluster Healthy    ${m1}    2    0
    Should Contain    ${status}[warnings]    below_quorum
    Should Be Equal As Integers    ${status}[shards]    1

Joining twice is refused
    Action Should Fail    ${m2}    configure-module    {"role": "initial"}

Join a third member
    Run Action    ${m3}    configure-module    {"role": "join", "cluster_id": "${m1}"}
    ${status} =    Wait Until Cluster Healthy    ${m1}    3    1
    Should Be Equal As Integers    ${status}[shards]    3
    Should Not Contain    ${status}[warnings]    below_quorum
    Should Be True    ${status}[quorum_ok]

Periodic check changes nothing on a healthy cluster
    ${rc} =    Execute Command    runagent -m ${m1} systemctl --user is-enabled valkey-reconcile.timer
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0
    # Skipped while an event-driven repair holds the lock: retry
    Wait Until Keyword Succeeds    120s    10s    Timer Check Should Change Nothing    ${m1}
    # Only the coordinator (the first member) runs it
    ${output} =    Execute Command    runagent -m ${m3} valkey-reconcile --timer
    ${result} =    Evaluate    json.loads($output)    modules=json
    Should Be Equal    ${result}[result]    skipped
    ${status} =    Run Action    ${m2}    get-cluster-status
    Should Be Equal    ${status}[last_reconcile][result]    ok

Consistency mode applies to the whole cluster
    Run Action    ${m2}    configure-module    {"role": "join", "cluster_id": "${m1}", "consistency": "consistency"}
    Wait Until Keyword Succeeds    60s    5s    Consistency Should Be    ${m1}    consistency
    Wait Until Keyword Succeeds    60s    5s    Consistency Should Be    ${m3}    consistency
    ${output} =    Execute Command    redis-cli hget module/${m3}/srv/tcp/valkey consistency
    Should Be Equal    ${output}    consistency

Cluster client works through any member
    ${grant} =    Grant Client    ${m2}    {"client_id": "robot"}
    Should Be Equal    ${grant}[ha_mode]    cluster
    Should Be Equal    ${grant}[consistency]    consistency
    Should Be True    ${grant}[cluster]
    Length Should Be    ${grant}[nodes]    3
    ${result} =    Probe    ${m1}    write    300
    Should Be True    ${result}[ok]    ${result}
    ${result} =    Probe    ${m3}    read    300
    Should Be True    ${result}[ok]    ${result}

Client user exists on every process
    FOR    ${mid}    IN    ${m1}    ${m2}    ${m3}
        ${clients} =    Run Action    ${mid}    list-clients
        ${ids} =    Evaluate    [c["client_id"] for c in $clients["clients"]]
        Should Contain    ${ids}    robot
    END

Service records list every member
    ${output} =    Execute Command    redis-cli hget module/${m3}/srv/tcp/valkey nodes
    ${count} =    Evaluate    len($output.split(","))
    Should Be Equal As Integers    ${count}    3

A restarted member rejoins
    Execute Command    runagent -m ${m2} systemctl --user restart valkey.service valkey-replica.service
    Wait Until Cluster Healthy    ${m1}    3    1
    ${result} =    Probe    ${m1}    read    300
    Should Be True    ${result}[ok]    ${result}

Rolling restart of an update keeps the data
    ${rc} =    Execute Command    runagent -m ${m1} /home/${m1}/.config/update-module.d/20restart
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0
    Wait Until Cluster Healthy    ${m2}    3    1
    ${result} =    Probe    ${m2}    read    300
    Should Be True    ${result}[ok]    ${result}

Removing a member hands its slots over
    ${rc} =    Execute Command    remove-module --no-preserve ${m3}
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0
    ${status} =    Wait Until Cluster Healthy    ${m1}    2    0
    Should Be Equal As Integers    ${status}[shards]    1
    ${result} =    Probe    ${m1}    read    300
    Should Be True    ${result}[ok]    ${result}

A member leaves the cluster
    Run Action    ${m2}    configure-module    {"role": "standalone"}
    ${status} =    Wait Until Cluster Healthy    ${m1}    1    0
    ${result} =    Probe    ${m1}    read    300
    Should Be True    ${result}[ok]    ${result}
    ${config} =    Run Action    ${m2}    get-configuration
    Should Be Equal    ${config}[role]    standalone

The client user of a removed module is revoked
    Execute Command    redis-cli publish cluster/event/module-removed '{"module": "robot", "node": 1}'
    Wait Until Keyword Succeeds    60s    5s    Client Should Be Gone    ${m1}

Remove the instances
    FOR    ${mid}    IN    ${m1}    ${m2}
        ${rc} =    Execute Command    remove-module --no-preserve ${mid}
        ...    return_rc=True  return_stdout=False
        Should Be Equal As Integers    ${rc}  0
    END
    Execute Command    rm -f /root/valkey-grant.json

*** Keywords ***
Timer Check Should Change Nothing
    [Arguments]    ${mid}
    ${output}  ${rc} =    Execute Command    runagent -m ${mid} valkey-reconcile --timer
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}  0
    ${result} =    Evaluate    json.loads($output)    modules=json
    Should Be Equal    ${result}[result]    ok
    Should Be Empty    ${result}[changes]

Consistency Should Be
    [Arguments]    ${mid}    ${mode}
    ${config} =    Run Action    ${mid}    get-configuration
    Should Be Equal    ${config}[consistency]    ${mode}

Client Should Be Gone
    [Arguments]    ${mid}
    ${clients} =    Run Action    ${mid}    list-clients
    ${ids} =    Evaluate    [c["client_id"] for c in $clients["clients"]]
    Should Not Contain    ${ids}    robot
