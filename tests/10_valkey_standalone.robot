*** Settings ***
Documentation    Standalone server: install, configure, client credentials,
...              revocation, backup dump, removal.
Library    SSHLibrary
Resource    valkey.resource

*** Variables ***
${module_id}    ${EMPTY}

*** Test Cases ***
Install valkey
    ${mid} =    Install Valkey
    Set Suite Variable    ${module_id}    ${mid}
    Copy Probe

Grant is refused before configuration
    Action Should Fail    ${module_id}    grant-client    {"client_id": "robot"}

Configure standalone
    Run Action    ${module_id}    configure-module    {"role": "standalone", "maxmemory_mb": 128}
    ${config} =    Run Action    ${module_id}    get-configuration
    Should Be Equal    ${config}[role]    standalone
    Should Be Equal    ${config}[ha_mode]    standalone
    Should Be Equal As Integers    ${config}[maxmemory_mb]    128
    Should Be True    ${config}[admin_password_set]
    Should Not Contain    ${config}    password

Service record is published without secrets
    ${output} =    Execute Command    redis-cli hgetall module/${module_id}/srv/tcp/valkey
    Should Contain    ${output}    standalone
    Should Not Contain    ${output}    password

Status reports a running standalone server
    ${status} =    Run Action    ${module_id}    get-cluster-status
    Should Be True    ${status}[configured]
    Should Be Equal    ${status}[ha_mode]    standalone
    Should Be True    ${status}[local][running]
    Should Be True    ${status}[local][aof_enabled]

Listening only on the VPN address and loopback
    ${config} =    Run Action    ${module_id}    get-configuration
    ${output} =    Execute Command    ss -Htln 'sport = :${config}[port]'
    Should Not Contain    ${output}    0.0.0.0
    Should Not Contain    ${output}    *:${config}[port]
    Should Contain    ${output}    127.0.0.1

Grant client credentials
    ${grant} =    Grant Client    ${module_id}    {"client_id": "robot"}
    Should Be Equal    ${grant}[username]    ns8c-robot
    Should Be Equal    ${grant}[ha_mode]    standalone
    Should Be Equal As Integers    ${grant}[password_length]    64
    ${clients} =    Run Action    ${module_id}    list-clients
    Should Be Equal    ${clients}[clients][0][client_id]    robot

Client can write and read
    ${result} =    Probe    ${module_id}    write
    Should Be True    ${result}[ok]    ${result}
    ${result} =    Probe    ${module_id}    read
    Should Be True    ${result}[ok]    ${result}

Password is not stored in clear
    ${rc} =    Execute Command    python3 -c 'import json,sys; print(json.load(open("/root/valkey-grant.json"))["password"])' > /root/valkey-pw && grep -rqsF -f /root/valkey-pw /home/${module_id}/.config/state /home/${module_id}/.local/share/containers/storage/volumes
    ...    return_rc=True  return_stdout=False
    Should Not Be Equal As Integers    ${rc}  0
    Execute Command    rm -f /root/valkey-pw

Backup dump works
    ${rc} =    Execute Command    runagent -m ${module_id} module-dump-state
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0

Data survives a restart
    Execute Command    runagent -m ${module_id} systemctl --user restart valkey.service
    ${result} =    Probe    ${module_id}    read
    Should Be True    ${result}[ok]    ${result}

Revoke client credentials
    ${result} =    Run Action    ${module_id}    revoke-client    {"client_id": "robot"}
    Should Be True    ${result}[revoked]
    ${result} =    Probe    ${module_id}    ping
    Should Not Be True    ${result}[ok]

Remove valkey
    ${rc} =    Execute Command    remove-module --no-preserve ${module_id}
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0
    Execute Command    rm -f /root/valkey-grant.json
